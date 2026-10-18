# -*- coding: utf-8 -*-
"""Refresh watchdog: tells Paul when the self-healing refresh pipeline has NOT healed itself.

NO AI IN THIS LOOP (DE-019).  Stdlib only; it reads files, asks Task Scheduler one question and,
when something has stayed wrong long enough, raises a Windows toast and appends a line to an alert
log.  Owner ruling (Paul, 2026-10-15): alerts are the toast plus the log, and only after self-heal
has had several chances to fix the problem.

  python fec/refresh_watchdog.py --once              # the scheduled form (default); exit 0 always
  python fec/refresh_watchdog.py --once --dry-run    # print what it sees; writes/toasts NOTHING
  python fec/refresh_watchdog.py --root <dir>        # another checkout / a test tree
  ops/scheduler/register_refresh_watchdog_task.ps1   # task 'MelangeRefreshWatchdog', every 30 min

WHAT IS A BREACH (every input is optional; an absent input is a note, never a breach)
  source:<name>            data/engine/source_freshness.json -- age > 2 x cadence_h (tier core) or
                           > 3 x cadence_h (enrichment; a missing/unknown tier is read as enrichment,
                           the lenient side).  Age comes from last_success_utc when it parses (so a
                           file nobody has rewritten still ages), else the file's own age_h.
                           Accepts {name: {...}}, {'sources': {name: {...}}} or a list of dicts
                           carrying 'name' / 'source'.
  live:<file>              mtime of member_desk_bundle.json / Moneyball_email.zip older than 30 h.
                           Looked up in the root, then data/money/ (where the zip really lives).
  lease                    root/.pipeline_run.lock older than 2 h AND its owner is not alive.  Age is
                           the heartbeat file's mtime (.pipeline_run.heartbeat.lock, POSIX only) else
                           the lease's started_utc, else its mtime -- Windows writes no heartbeat, so
                           lease age is the stand-in.  A live owner (pid alive and process start time
                           matches the recorded proc_start, same host) is a long run, not a breach;
                           a lease from another host cannot be judged and is never a breach.
  tick_task                'MelangeRefreshTick' missing or disabled in Task Scheduler (schtasks
                           /query ... /fo csv /v), or its Last Result nonzero for 3 consecutive
                           DISTINCT fires (a new 'Last Run Time' is a new fire; running 0x41301 and
                           never-run 0x41303 are not failures).  If schtasks cannot be run at all the
                           check is skipped with a note.
  dead_letter              data/engine/dead_letter/*.jsonl grew since the last check.  The first run
                           only baselines.  Lines stay in those files forever, so growth is held as
                           a breach for 6 h after it was last seen -- long enough to cross the
                           3-run gate -- and then clears.
  disk                     free space on the root's drive < 30 GB.
  check_error:<check>      a check itself raised (the watchdog must not go blind quietly).

WHEN IT ALERTS
  The watchdog is meant to run every 30 min.  A breach is counted once per run; it must be present
  in >= 3 CONSECUTIVE runs before anything is said (that is the time self-heal gets).  Then ONE
  alert; while the breach continues, a re-alert at most every 12 h; when it clears, ONE 'resolved'
  notice.  A breach that clears before the 3rd run is dropped silently.  One toast per run for all
  new/re-alerts together and one for all resolutions; the log gets one JSON line per key and event
  ('alert' | 'realert' | 'resolved') in data/engine/watchdog_alerts.log, with the toast outcome.  A
  failed toast is logged and never raises; the alert still counts as sent (the log is the durable
  channel, and retrying a dead toast every 30 min would just be noise).

FILES (all under <root>/data/engine)
  watchdog_state.json      {schema, updated_utc, breaches:{key:{first_seen_utc, consecutive,
                           detail, alerted_utc, alert_count}}, tick_task:{last_run, fail_streak},
                           dead_letter:{counts, last_new}} -- written atomically (refresh_state's
                           lock-window-retrying writer) and ONLY outside --dry-run.  An unreadable
                           state file starts clean (alerts are delayed by the 3-run gate, not lost).
  watchdog_alerts.log      append-only, one JSON object per line.
  refresh_state.json       read for context only (last tick outcome / last publish) in alert lines.

EXIT CODES  0 always, including every "something is wrong" outcome.  Only an unexpected crash of
the watchdog itself is nonzero; the traceback is also appended to the alert log as event 'crash',
because the scheduled form runs under pythonw.exe (no console).
"""
import argparse
import base64
import csv
import datetime
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import refresh_state as rs                                            # noqa: E402  (stdlib-only)

SCHEMA = 1
TASK_NAME = 'MelangeRefreshTick'
TOAST_TITLE = 'Melange pipeline'
TOAST_APP_ID = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'

RUNS_TO_ALERT = 3             # consecutive watchdog runs a breach must persist before the 1st alert
REALERT_H = 12.0              # at most one re-alert per breach per this many hours
CORE_MULT, ENRICH_MULT = 2.0, 3.0
LIVE_MAX_H = 30.0
LEASE_STALE_H = 2.0
TICK_FAIL_FIRES = 3
DEAD_LETTER_HOLD_H = 6.0
DISK_FLOOR_GB = 30.0
LIVE_FILES = ('member_desk_bundle.json', 'Moneyball_email.zip')
# Task Scheduler results that are not failures: 0x41301 running, 0x41303 has not yet run.
TASK_OK_RESULTS = (0, 267009, 267011)
TASK_RUNNING = 267009
PROC_START_TOLERANCE_S = 2.0   # same tolerance fec/pipeline_lock.py applies


def _hours(later, earlier):
    return (later - earlier).total_seconds() / 3600.0


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if abs(value) < float('inf') else None      # also drops NaN (abs(nan) < inf is False)


def _parse_ts(value):
    """Aware UTC datetime from an ISO string (Z or offset; naive read as UTC) or epoch seconds."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
        moment = datetime.datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except (ValueError, OverflowError, OSError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


def _sub(state, key):
    if not isinstance(state.get(key), dict):
        state[key] = {}
    return state[key]


# =============================================================================================
# the checks: each returns {breach_key: detail}
# =============================================================================================
def _iter_sources(doc):
    if isinstance(doc, dict) and isinstance(doc.get('sources'), (dict, list)):
        doc = doc['sources']
    if isinstance(doc, dict):
        pairs = list(doc.items())
    elif isinstance(doc, list):
        pairs = [(e.get('name') or e.get('source'), e) for e in doc if isinstance(e, dict)]
    else:
        pairs = []
    return [(str(name), e) for name, e in pairs if name and isinstance(e, dict)]


def check_sources(eng, now, state):
    doc, problem = rs.read_json(os.path.join(eng, 'source_freshness.json'))
    if problem:
        raise ValueError('source_freshness.json unreadable: ' + problem)
    out = {}
    for name, entry in _iter_sources(doc):
        cadence = _num(entry.get('cadence_h'))
        if not cadence or cadence <= 0:
            continue
        stamp = _parse_ts(entry.get('last_success_utc'))
        age = _hours(now, stamp) if stamp else _num(entry.get('age_h'))
        if age is None:
            continue
        tier = str(entry.get('tier') or '').lower()
        mult = CORE_MULT if tier == 'core' else ENRICH_MULT
        if age > mult * cadence:
            out['source:' + name] = '%s (%s) age %.1f h > %gx cadence %g h' % (
                name, tier or 'enrichment', age, mult, cadence)
    return out


def check_live(root, now, state):
    out = {}
    for name in LIVE_FILES:
        for folder in (root, os.path.join(root, 'data', 'money')):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                age = _hours(now, datetime.datetime.fromtimestamp(os.stat(path).st_mtime,
                                                                  datetime.timezone.utc))
                if age > LIVE_MAX_H:
                    out['live:' + name] = '%s last written %.1f h ago (limit %g h)' % (name, age, LIVE_MAX_H)
                break
    return out


def _owner_live(payload):
    """Is the process that took the lease still that process?  Uncertain answers say True."""
    pid = payload.get('pid')
    if pid is None:
        return False                                     # malformed lease: no owner to wait for
    host = payload.get('host')
    if os.name == 'nt' and isinstance(host, str) and host.casefold() != ('nt:' + socket.gethostname()).casefold():
        return True                                      # foreign host: pid means nothing here
    try:
        import process_liveness as pl
    except ImportError:
        return True
    if not pl.process_alive(pid):
        return False
    recorded = payload.get('proc_start')
    live = pl.process_start_time(pid) if recorded is not None else None
    if live is None:
        return True
    try:
        return abs(float(live) - float(recorded)) <= PROC_START_TOLERANCE_S
    except (TypeError, ValueError):
        return True


def check_lease(root, now, state):
    path = os.path.join(root, '.pipeline_run.lock')
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    payload, _problem = rs.read_json(path)
    payload = payload if isinstance(payload, dict) else {}
    beat = (path[:-5] if path.endswith('.lock') else path) + '.heartbeat.lock'
    try:
        stamp = os.stat(beat).st_mtime
    except OSError:
        stamp = _num(payload.get('started_utc')) or mtime
    age = (now.timestamp() - stamp) / 3600.0
    if age <= LEASE_STALE_H or _owner_live(payload):
        return {}
    return {'lease': 'pipeline lease %.1f h old, owner pid %s (%s) is not running'
                     % (age, payload.get('pid', '?'), payload.get('owner_kind', '?'))}


def parse_schtasks_csv(text):
    """{'status','state','last_result','last_run'} from `schtasks /query /fo csv /v`, or None."""
    rows = [row for row in csv.reader(io.StringIO(text or '')) if row]
    if len(rows) < 2 or len(rows[1]) != len(rows[0]):
        return None
    rec = dict(zip(rows[0], rows[1]))
    result = _num(rec.get('Last Result'))
    return {'status': rec.get('Status', ''), 'state': rec.get('Scheduled Task State', ''),
            'last_result': None if result is None else int(result),
            'last_run': rec.get('Last Run Time', '')}


def query_tick_task():
    """Task Scheduler's view of MelangeRefreshTick: a dict, {'missing': True}, or {'unavailable': why}."""
    try:
        done = subprocess.run(['schtasks', '/query', '/tn', TASK_NAME, '/fo', 'csv', '/v'],
                              capture_output=True, text=True, errors='replace', timeout=60,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError) as error:
        return {'unavailable': '%s: %s' % (type(error).__name__, str(error)[:100])}
    parsed = parse_schtasks_csv(done.stdout)
    if parsed:
        return parsed
    # ponytail: any failed query with no row reads as "missing" (schtasks messages are localized).
    return {'missing': True} if done.returncode else {'unavailable': 'unparseable schtasks output'}


def check_tick_task(now, state, notes):
    info = query_tick_task()
    mem = _sub(state, 'tick_task')
    if info.get('unavailable'):
        notes.append('tick_task skipped: ' + info['unavailable'])
        return {}
    if info.get('missing'):
        return {'tick_task': '%s is missing from Task Scheduler' % TASK_NAME}
    if 'disabled' in (info['status'].lower(), info['state'].lower()):
        return {'tick_task': '%s is disabled' % TASK_NAME}
    result, last_run = info['last_result'], info['last_run']
    if result != TASK_RUNNING and last_run and last_run != mem.get('last_run'):
        mem['last_run'], mem['last_result'] = last_run, result   # a new fire: count it, once
        mem['fail_streak'] = 0 if result in TASK_OK_RESULTS or result is None \
            else int(mem.get('fail_streak') or 0) + 1
    streak = int(mem.get('fail_streak') or 0)
    if streak >= TICK_FAIL_FIRES:
        return {'tick_task': '%s last result nonzero for %d consecutive fires (last 0x%X at %s)'
                             % (TASK_NAME, streak, (mem.get('last_result') or 0) & 0xFFFFFFFF, last_run)}
    return {}


def _line_count(path):
    with open(path, 'rb') as handle:
        return sum(1 for line in handle if line.strip())


def check_dead_letter(eng, now, state):
    folder = os.path.join(eng, 'dead_letter')
    counts = {}
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith('.jsonl'))
    except OSError:
        names = []
    for name in names:
        counts[name] = _line_count(os.path.join(folder, name))
    mem = _sub(state, 'dead_letter')
    before = mem.get('counts')
    if isinstance(before, dict):                         # no baseline yet: this run only records one
        grown = {n: c - before.get(n, 0) if c >= before.get(n, 0) else c for n, c in counts.items()}
        grown = {n: g for n, g in grown.items() if g > 0}
        if grown:
            mem['last_new'] = {'utc': rs.stamp(now), 'lines': sum(grown.values()), 'files': sorted(grown)}
    mem['counts'] = counts
    last = mem.get('last_new') if isinstance(mem.get('last_new'), dict) else {}
    seen = rs.parse_stamp(last.get('utc'))
    if seen and _hours(now, seen) < DEAD_LETTER_HOLD_H:
        return {'dead_letter': '%s new dead-letter line(s) at %s in %s'
                               % (last.get('lines'), last['utc'], ', '.join(last.get('files') or []))}
    return {}


def free_gb(root):
    return shutil.disk_usage(root).free / float(1 << 30)


def check_disk(root, now, state):
    free = free_gb(root)
    if free < DISK_FLOOR_GB:
        return {'disk': 'free disk %.1f GB < %g GB floor' % (free, DISK_FLOOR_GB)}
    return {}


def evaluate(root, now, state):
    """(breaches {key: detail}, notes [str]).  A check that raises becomes check_error:<name>."""
    eng = os.path.join(root, 'data', 'engine')
    notes, breaches = [], {}
    checks = (('sources', lambda: check_sources(eng, now, state)),
              ('live', lambda: check_live(root, now, state)),
              ('lease', lambda: check_lease(root, now, state)),
              ('tick_task', lambda: check_tick_task(now, state, notes)),
              ('dead_letter', lambda: check_dead_letter(eng, now, state)),
              ('disk', lambda: check_disk(root, now, state)))
    for name, check in checks:
        try:
            breaches.update(check())
        except Exception as error:                       # noqa: BLE001 -- see docstring
            breaches['check_error:' + name] = '%s check failed: %s: %s' % (
                name, type(error).__name__, str(error)[:120])
    return breaches, notes


# =============================================================================================
# the persistence gate (pure state arithmetic)
# =============================================================================================
def advance(state, breaches, now):
    """Count this run.  Returns (alerts [(event, key, rec)], resolved [(key, rec)])."""
    book = _sub(state, 'breaches')
    alerts, resolved = [], []
    for key, detail in sorted(breaches.items()):
        rec = book.get(key)
        if not isinstance(rec, dict):
            rec = book[key] = {'first_seen_utc': rs.stamp(now), 'consecutive': 0,
                               'alerted_utc': None, 'alert_count': 0}
        rec['consecutive'] = int(rec.get('consecutive') or 0) + 1
        rec['detail'] = detail
        if rec['consecutive'] < RUNS_TO_ALERT:
            continue
        last = rs.parse_stamp(rec.get('alerted_utc'))
        if last is None:
            alerts.append(('alert', key, rec))
        elif _hours(now, last) >= REALERT_H:
            alerts.append(('realert', key, rec))
    for key in sorted(k for k in book if k not in breaches):
        rec = book.pop(key)
        if isinstance(rec, dict) and rec.get('alerted_utc'):
            resolved.append((key, rec))
    return alerts, resolved


# =============================================================================================
# the toast
# =============================================================================================
_TOAST_PS = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; "
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null; "
    "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
    "$t = $x.GetElementsByTagName('text'); "
    "$t.Item(0).AppendChild($x.CreateTextNode($env:MELANGE_TOAST_TITLE)) | Out-Null; "
    "$t.Item(1).AppendChild($x.CreateTextNode($env:MELANGE_TOAST_BODY)) | Out-Null; "
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('" + TOAST_APP_ID + "')"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")


def send_toast(title, body):
    """(ok, note).  Never raises.  Text travels in the environment, not in the script text."""
    if os.name != 'nt':
        return False, 'not windows'
    command = base64.b64encode(_TOAST_PS.encode('utf-16-le')).decode('ascii')
    env = dict(os.environ, MELANGE_TOAST_TITLE=title, MELANGE_TOAST_BODY=body)
    try:
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                               'Bypass', '-EncodedCommand', command], env=env, capture_output=True,
                              text=True, errors='replace', timeout=60,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError) as error:
        return False, '%s: %s' % (type(error).__name__, str(error)[:150])
    if done.returncode:
        return False, 'powershell exit %d: %s' % (done.returncode, (done.stderr or '').strip()[:150])
    return True, ''


def _notify(body):
    try:
        ok, note = send_toast(TOAST_TITLE, body[:230])
    except Exception as error:                           # noqa: BLE001 -- a toast must never kill a run
        ok, note = False, '%s: %s' % (type(error).__name__, str(error)[:150])
    return 'ok' if ok else 'failed: ' + str(note)


# =============================================================================================
# one run
# =============================================================================================
def _context(eng):
    payload, _problem = rs.read_json(os.path.join(eng, 'refresh_state.json'))
    payload = payload if isinstance(payload, dict) else {}
    tick, pub = payload.get('last_tick') or {}, payload.get('publication') or {}
    return 'last tick: %s exit=%s ended=%s; last publish: %s' % (
        tick.get('outcome'), tick.get('exit_code'), tick.get('ended_utc'), pub.get('last_publish_utc'))


def run(root, dry_run=False, now=None):
    """One watchdog pass.  Returns a result dict; in dry-run nothing is written or shown."""
    root = os.path.abspath(str(root))
    now = now or rs.utcnow()
    eng = os.path.join(root, 'data', 'engine')
    state_path = os.path.join(eng, 'watchdog_state.json')
    state, _problem = rs.read_json(state_path)
    if not isinstance(state, dict) or state.get('schema') != SCHEMA:
        state = {'schema': SCHEMA}
    breaches, notes = evaluate(root, now, state)
    alerts, resolved = advance(state, breaches, now)
    result = {'now': rs.stamp(now), 'breaches': breaches, 'notes': notes, 'alerts': [],
              'resolved': [], 'state': state, 'dry_run': dry_run}
    stamp, lines = rs.stamp(now), []
    for event, key, rec in alerts:
        lines.append({'at': stamp, 'event': event, 'key': key, 'detail': rec['detail'],
                      'consecutive_runs': rec['consecutive'], 'first_seen_utc': rec['first_seen_utc'],
                      'alert_count': int(rec.get('alert_count') or 0) + 1, 'context': _context(eng)})
    for key, rec in resolved:
        lines.append({'at': stamp, 'event': 'resolved', 'key': key, 'detail': rec.get('detail'),
                      'first_seen_utc': rec.get('first_seen_utc'),
                      'alert_count': int(rec.get('alert_count') or 0)})
    result['alerts'] = [l for l in lines if l['event'] != 'resolved']
    result['resolved'] = [l for l in lines if l['event'] == 'resolved']
    if dry_run:
        return result
    if result['alerts']:
        body = '%s: %s' % ('Still failing' if all(l['event'] == 'realert' for l in result['alerts'])
                           else 'Needs attention', '; '.join(l['detail'] for l in result['alerts']))
        outcome = _notify(body)
        for line in result['alerts']:
            line['toast'] = outcome
        for _event, _key, rec in alerts:
            rec['alerted_utc'] = stamp
            rec['alert_count'] = int(rec.get('alert_count') or 0) + 1
    if result['resolved']:
        outcome = _notify('Resolved: ' + '; '.join(l['key'] for l in result['resolved']))
        for line in result['resolved']:
            line['toast'] = outcome
    for line in lines:
        rs.append_line(os.path.join(eng, 'watchdog_alerts.log'),
                       json.dumps(line, ensure_ascii=False, sort_keys=True))
    state['updated_utc'] = stamp
    state['last_notes'] = notes
    rs.write_json_atomic(state_path, state)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Refresh pipeline watchdog (stdlib, no AI).')
    parser.add_argument('--once', action='store_true', help='one pass (the default and only mode)')
    parser.add_argument('--dry-run', action='store_true', help='print findings; no state, log or toast')
    parser.add_argument('--root', default=os.environ.get('MONEYBALL_ROOT') or os.path.dirname(_HERE))
    args = parser.parse_args(argv)
    try:
        result = run(args.root, dry_run=args.dry_run)
    except Exception:                                    # the watchdog itself crashed: say so, exit nonzero
        rs.append_line(os.path.join(os.path.abspath(args.root), 'data', 'engine', 'watchdog_alerts.log'),
                       json.dumps({'at': rs.stamp(rs.utcnow()), 'event': 'crash',
                                   'error': traceback.format_exc()[-1500:]}, ensure_ascii=False))
        raise
    book = result['state'].get('breaches') or {}
    print('refresh_watchdog %s root=%s now=%s' % ('DRY-RUN' if args.dry_run else 'run', args.root, result['now']))
    for key, detail in sorted(result['breaches'].items()):
        print('  BREACH %-34s run %d/%d  %s' % (key, book[key]['consecutive'], RUNS_TO_ALERT, detail))
    if not result['breaches']:
        print('  no breaches')
    for line in result['alerts'] + result['resolved']:
        print('  %s%s %s' % ('WOULD ' if args.dry_run else '', line['event'].upper(), line['key']))
    for note in result['notes']:
        print('  note: ' + note)
    return 0


if __name__ == '__main__':
    sys.exit(main())

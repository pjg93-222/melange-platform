"""Mentat prewarm harness: calculate the chunks before anyone selects them.

Paul, 2026-09-24: "use cache rendering to reduce latency where it pre-calculates the chunks before it
is selected/rendered". This module computes, ahead of the click, exactly what the click would compute.
It uses the same entry points and the same request bodies, so the same cache keys land on disk (the
warmer process), or in the console's own memory (the startup warm). Every data and integrity rule is
unchanged: Insights as_of defaults to today, the observation journal is written, integrity_check and
full hashing still run, and nothing here reads a cache the app would not read itself.

In the console process (see the integration notes in the receipt):
  start_startup_warm(read_only, app)   one daemon thread that fills the console's MEMORY caches after
                                       a restart: source discovery, the source-guard hashing that the
                                       analytical routes run, the workbook receipts and the default
                                       Graph view per industry. It only reads. It never blocks or
                                       fails a request.
  launch_warmer(data_root, read_only, console_port)
                                       starts this file as a separate below-normal-priority process
                                       (`run`) that builds the DISK caches. It never starts on a
                                       read-only console.

As a CLI. Every op prints exactly one JSON object and exits 0 (success), 1 (warning) or 2 (error):
  python mentat/app/prewarm.py plan    [--industries A,B]
  python mentat/app/prewarm.py run     [--parent-pid PID --console URL] [--once] [--max-tasks N]
  python mentat/app/prewarm.py warm    --task ID
  python mentat/app/prewarm.py verify  --sample N
  python mentat/app/prewarm.py bench   --base URL [--out DIR] [--industries A,B] [--repeats 3]
  python mentat/app/prewarm.py status
Common options: --data-root DIR (as server.py), --state-root DIR, --allow-index-builds.
--state-root DIR, outside mentat/, runs against scratch state. Every writable app root is redirected
under DIR. An audit hook refuses any write to the Mentat tree, the report folder and the console data
root, and a missing analytical index is never built.

Task kinds, in priority order. Industries are ordered by their most recent Insights hint, newest first.
  query_view         The Graph sparkline's first-load /api/contributions/query per industry (graph.js
                     sparkRequest on the latest=1 view). It builds the query projection and the
                     response cache (perf.md H7: Banking 48 s, Telecom 26 s, Credit Arms 13 s). When the
                     response cache has no room (see "Response-cache budget") it builds the projection only.
  insights_default   insights.js's first-open body per industry, polled until it is ready.
  insights_neighbor  From the newest hint: that scope; then "All covered companies" (the body without a
                     company key) and each other company, in the order of the client's company select,
                     which comes from the result's options.companies.
  insights_company   Every company of every industry at the default body (insights.js first open with
                     that company chosen), industries in hint order, companies in the select's order.
  (insights_neighbor) Then every other basis for the newest hint's scope.
  graph_spark        For the newest hint's industry: the Graph sparkline query of each company's view at
                     the first-load cycle (graph.js company onchange -> refresh() -> sparkRequest).
  comparison_flows   Per industry, when response_cache is installed: comparison_data.flows_entry, the
                     /api/comparison-flows answer stored in the response cache's disk tier.
  drawer_sidecars    Per industry, when drawer_sidecars is installed: build_sidecars, every sidecar the
                     Graph drawer reads (it no longer loads the 1.1 GB inventory of perf.md H8).
  worksheets         Per industry, when response_cache is installed: report_insights.worksheet_entry, the
                     /api/report-data answer for the sheet list and every sheet of each of the industry's
                     published workbooks (a sheet the route refuses is cached as its refusal and skipped).
                     These three kinds need ARTIFACT_EXTRA_FREE_BYTES more free memory than the 1.5 GB floor.
The planning Graph views (first loads and company views) land in the response cache's disk tier too.
A missing analytical index is reported as index_missing. Only --allow-index-builds builds it.

Response-cache budget. contribution_query_store._prune keeps the newest 128 response files (and at most
128 MB) per namespace, by mtime, after every write. A response this warmer writes is "demoted": its mtime
moves into early 2000 (its day and its plan position kept), so any later prune removes warm entries,
oldest day and lowest priority first, before any response a click stored. A task that would write runs
only while every file its write could push out (with one more written by a click at the same moment) is a
warm entry from an earlier day; otherwise it waits as over_budget. A warm entry a click later used (its
key in hints.json) is promoted back to the click's time.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as dt
import functools
import gzip
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
import urllib.parse
import uuid

APP = Path(__file__).resolve().parent
PROJECT = APP.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))   # proclock and the console's modules live beside this file
import proclock  # noqa: E402  (cross-process locks and process liveness, shared with the console)
DEFAULT_STATE_ROOT = PROJECT / 'state'
SCHEMA = 1

# (d) of the run loop: a change to any of these exits the warmer; the console relaunches it.
WATCHED_CODE = ('prewarm.py', 'analysis_data.py', 'contribution_data.py', 'proclock.py')
# Modules whose code decides a warmed key or a warmed result. A change re-plans and releases quarantine.
RESULT_CODE = ('prewarm.py', 'server.py', 'analysis_data.py', 'analysis_metrics.py', 'analysis_history.py',
               'analysis_integrations.py', 'analysis_scan.py', 'contribution_data.py', 'contribution_query.py',
               'contribution_query_store.py', 'contribution_semantics.py', 'contribution_workbook_guard.py',
               'contribution_source_guard.py', 'industry_scope.py', 'snapshot_registry.py', 'graph_data.py',
               'report_insights.py', 'native_scan.py', 'view_cache.py', 'source_memo.py',
               'response_cache.py', 'comparison_data.py', 'drawer_sidecars.py', 'downstream.py',
               'collection_gaps.py', 'evidence_csv.py')

MIN_FREE_BYTES = int(1.5 * 1024 ** 3)
PAUSE_SECONDS = 60
IDLE_SECONDS = 30
SOURCE_CHECK_SECONDS = 300
HINTS_DEBOUNCE_SECONDS = 30
MAX_CONSECUTIVE_FAILURES = 3
LEDGER_LIMIT_BYTES = 5 * 1024 * 1024
TASK_TIMEOUT_SECONDS = 30 * 60
LOCK_RELEASE_GRACE_SECONDS = 30
START_DELAY_SECONDS = 15
PARENT_WATCH_SECONDS = 1.0               # the CLI warmer's watchdog checks its console this often
# The warmer's sweep of projection files an ended builder left behind (sweep_pending): the store folders it
# reads, how old a file must be, and how often the warmer sweeps again.
PENDING_SWEEP_FOLDERS = ('views', 'insights-views')
PENDING_MIN_AGE_SECONDS = 120
PENDING_SWEEP_SECONDS = 15 * 60
PENDING_NAME = re.compile(r'^([0-9a-f]{64})\.[0-9a-f]{32}\.pending$')   # <key>.<uuid4 hex>.pending
DEFAULT_MAX_COMPANY_NEIGHBORS = 48
PAUL_CONSOLE_PORT = 8806
EXIT_CODES = {'success': 0, 'warning': 1, 'error': 2}
ROOT_CAUSES = ('low_memory', 'console_busy', 'source_changed_during_build', 'lock_held_by_other_process',
               'index_missing', 'code_changed', 'parent_gone', 'cache_budget')  # and unexpected_exception:<type>

# Response-cache budget (module docstring). contribution_query_store._prune's own limits:
PRUNE_KEEP_FILES = 128
PRUNE_KEEP_BYTES = 128 * 1024 * 1024
BUDGET_SLACK_FILES = 1                   # a click's response written at the same moment as a warm one
BUDGET_MIN_ENTRY_BYTES = 512 * 1024      # size assumed for a response not yet written (or the largest seen)
DEMOTED_BASE_NS = 946684800 * 10 ** 9    # 2000-01-01T00:00:00Z: a demoted mtime's origin
DEMOTED_DAY_NS = 10 ** 12                # one plan day's span inside the demoted range
DEMOTED_SLOTS = 10 ** 6                  # plan positions per day; the first task gets the latest mtime
DEMOTED_CEILING_NS = DEMOTED_BASE_NS + 40000 * DEMOTED_DAY_NS   # below this an mtime is a warm entry's
DEMOTED_EPOCH = dt.date(2020, 1, 1)      # day 0 of the demoted range
RESPONSE_NAMESPACES = {'insights': ('insights',), 'query': ('results', 'summaries')}
INDEX_REFUSED = ('prewarm: the analytical index is missing; this harness never builds an index '
                 'without --allow-index-builds.')

# ----------------------------------------------------------------------------- client contract
# Every body below is what the browser sends. The literal source lines they mirror are pinned in
# CLIENT_CONTRACT and re-read by `verify`, so an edit to the client that changes a body is reported
# as drift instead of silently warming keys nobody asks for.
INSIGHTS_CORE_PANELS = ('favorites', 'trends', 'changes')          # insights.js const CORE; enabled = CORE.slice()
INSIGHTS_BASES = ('receipt', 'original_observed', 'original_supported', 'pac_activity')  # insights.js const BASES
POLL_STATES = ('building', 'queued', 'preparing')                   # insights.js queryReady(), graph.js sparkReady()

CLIENT_CONTRACT = {
    'insights.js': (
        "const CORE = ['favorites', 'trends', 'changes'];",
        "const BASES = ['receipt', 'original_observed', 'original_supported', 'pac_activity'];",
        "let enabled = CORE.slice()",
        "const out = {dataset: el('dataset').value, basis: BASES.includes(el('basis').value) ? el('basis').value : 'receipt', panels: enabled.slice()};",
        "for (const [field, control] of [['company','company'],['funding_source','funding-source'],['as_of','as-of'],['comparison_dataset','comparison-dataset']]) if (el(control).value) out[field] = el(control).value;",
        "if (options.companies) fillSelect(el('company'), options.companies, 'All covered companies',",
        "function pollDelay(attempts, retryAfterMs) { return attempts >= 40 ? 5000 : Math.max(200, Math.min(5000, retryAfterMs || 1500)); }",
        "const nextQuery = {...query}; delete nextQuery.retry;",
    ),
    'templates/view_insights.html': (
        '<select id="insights-basis"><option value="receipt">',
        '<select id="insights-company"><option value="">All covered companies</option></select>',
    ),
    'graph.js': (
        "return new URLSearchParams({id:s.run||'',industry:s.industry||'',company:s.company||'',",
        "tier:s.tier||'',cycle:s.cycle||'',funding:s.funding||'all',mode:s.mode||'direct',",
        "direction:s.direction||'Support',category:s.category||'',recipient:s.recipient||''});",
        "const url='/api/graph?'+query+(initial&&!query.get('cycle')?'&latest=1':'');",
        "category='';recipient='';options(get('company'),[['','All companies']]);options(get('cycle'),[['','All cycles']]);refresh(true);",
        "get('tier').value='';get('funding').value='all';get('mode').value='direct';",
        "item.available !== false && (item.id === industry || item.label === industry)) || null;",
        "if(data.filters.cycle){const end=Number(data.filters.cycle);if(Number.isFinite(end))scoped.push({field:'cycle',op:'eq',value:`${end-1}-${end}`});}",
        "return {dataset,basis:'receipt',dimensions:['company','quarter'],measures:['amount'],filters:scoped,",
        "aggregate_filters:[],sort:[{field:'quarter',direction:'asc'}],limit:100000,offset:0};",
        "Math.max(500,Math.min(4000,body.retry_after_ms||1000))",
    ),
    'templates/view_graph.html': (
        '<select id="graph-direction"><option value="Support">',
        '<select id="graph-mode"><option value="direct">',
        '<select id="graph-funding"><option value="all">',
    ),
}


def insights_default_body(dataset):
    """insights.js scopeQuery() on first open: the dataset, the basis select's first option, enabled =
    CORE (enabled = CORE.slice()), and no optional key, because every optional control is blank."""
    return {'dataset': dataset, 'basis': 'receipt', 'panels': list(INSIGHTS_CORE_PANELS)}


def graph_first_load_query(industry):
    """graph.js requestParams(currentScope()) with the controls loadCatalog() and the industry onchange
    reset, plus '&latest=1', which refresh(true) adds when no cycle is chosen."""
    params = {'id': 'published-reports', 'industry': industry, 'company': '', 'tier': '', 'cycle': '',
              'funding': 'all', 'mode': 'direct', 'direction': 'Support', 'category': '', 'recipient': ''}
    return urllib.parse.urlencode(params) + '&latest=1'


def graph_company_query(industry, company, cycle):
    """graph.js requestParams(currentScope()) once `company` is chosen after the first load: render()
    left the cycle select on the first load's echoed cycle (its option value, String(cycle)), the
    company onchange clears category and recipient and calls refresh(), which adds no latest=1."""
    number = _js_number(cycle) if cycle not in (None, '') else None
    value = _js_text(number) if number is not None and not isinstance(cycle, str) else str(cycle or '')
    params = {'id': 'published-reports', 'industry': industry, 'company': company, 'tier': '', 'cycle': value,
              'funding': 'all', 'mode': 'direct', 'direction': 'Support', 'category': '', 'recipient': ''}
    return urllib.parse.urlencode(params)


def _js_number(value):
    """JavaScript Number(value) restricted to what Number.isFinite accepts; None otherwise."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _js_text(number):
    return str(int(number)) if float(number).is_integer() else repr(number)


def spark_request(dataset, filters, category='', recipient=''):
    """graph.js sparkRequest(dataset, view), from the view's echoed filters."""
    if not filters or filters.get('tier'):
        return None
    scoped = []
    if filters.get('company'):
        scoped.append({'field': 'company', 'op': 'eq', 'value': filters['company']})
    if filters.get('cycle'):
        end = _js_number(filters['cycle'])
        if end is not None:
            scoped.append({'field': 'cycle', 'op': 'eq', 'value': f'{_js_text(end - 1)}-{_js_text(end)}'})
    if filters.get('funding') == 'personal':
        scoped.append({'field': 'funding_source', 'op': 'eq', 'value': 'employee_original'})
    if filters.get('funding') == 'corporate':
        scoped.append({'field': 'funding_source', 'op': 'eq', 'value': 'corporate_original'})
    if category:
        scoped.append({'field': 'recipient_category', 'op': 'eq', 'value': category})
    if recipient:
        scoped.append({'field': 'committee_id', 'op': 'eq', 'value': recipient})
    return {'dataset': dataset, 'basis': 'receipt', 'dimensions': ['company', 'quarter'], 'measures': ['amount'],
            'filters': scoped, 'aggregate_filters': [], 'sort': [{'field': 'quarter', 'direction': 'asc'}],
            'limit': 100000, 'offset': 0}


def insights_poll_delay(attempts, retry_after_ms):
    """insights.js pollDelay() (the 2026-09-24 perf edit: a 200 ms floor), in seconds."""
    return 5.0 if attempts >= 40 else max(0.2, min(5.0, (retry_after_ms or 1500) / 1000))


def spark_poll_delay(retry_after_ms):
    """graph.js sparkReady(): max(500, min(4000, retry_after_ms || 1000)), in seconds."""
    return max(0.5, min(4.0, (retry_after_ms or 1000) / 1000))


def client_contract_check(app_dir=APP):
    """Report every pinned client line that is no longer in the client source."""
    missing, checked = [], 0
    for name, snippets in CLIENT_CONTRACT.items():
        try:
            text = (Path(app_dir) / name).read_text(encoding='utf-8')
        except OSError as error:
            missing.append({'file': name, 'snippet': None, 'error': str(error)})
            continue
        for snippet in snippets:
            checked += 1
            if snippet not in text:
                missing.append({'file': name, 'snippet': snippet})
    return {'ok': not missing, 'checked': checked, 'missing': missing}


# ----------------------------------------------------------------------------- small utilities
def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def utc_iso(moment=None):
    return (moment or utc_now()).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def local_today():
    # The same clock analysis_data.validate() uses for the default as_of.
    return dt.date.today().isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def slug(text):
    # contribution_data._slug, the console's one slug rule.
    return re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_')


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def atomic_write_json(path, value, attempts=5):
    """Write then os.replace; retried briefly because a Windows reader can hold the target open."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=1, ensure_ascii=False, default=str)
    for attempt in range(attempts):
        pending = path.with_name(f'.{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
        try:
            pending.write_text(payload, encoding='utf-8')
            os.replace(pending, path)
            return True
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
        finally:
            with contextlib.suppress(OSError):
                if pending.exists():
                    pending.unlink()
    return False


def file_stamp(path):
    try:
        info = Path(path).stat()
        return [info.st_size, info.st_mtime_ns]
    except OSError:
        return None


def watched_code_stamps(app_dir=APP):
    return {name: file_stamp(Path(app_dir) / name) for name in WATCHED_CODE}


def code_fingerprint(extra_paths=(), app_dir=APP):
    stamps = {name: file_stamp(Path(app_dir) / name) for name in RESULT_CODE}
    stamps.update({str(path): file_stamp(path) for path in extra_paths})
    return digest(stamps)[:16]


def log(message):
    try:
        sys.stderr.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} prewarm[{os.getpid()}] {message}\n')
        sys.stderr.flush()
    except Exception:
        pass


def emit(kind, status, summary, **fields):
    """Glass Box emission; never raises."""
    try:
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        import glassbox_emit
        return glassbox_emit.emit(kind, status, summary, **fields)
    except Exception:
        return None


# ----------------------------------------------------------------------------- Windows probes
if os.name == 'nt':
    import ctypes
    import ctypes.wintypes as _wintypes
    import msvcrt

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.GetCurrentProcess.restype = _wintypes.HANDLE
    _kernel32.SetPriorityClass.argtypes = (_wintypes.HANDLE, _wintypes.DWORD)
    _kernel32.SetProcessInformation.restype = _wintypes.BOOL
    _kernel32.SetProcessInformation.argtypes = (_wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, _wintypes.DWORD)
    _kernel32.GetProcessInformation.restype = _wintypes.BOOL
    _kernel32.GetProcessInformation.argtypes = (_wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, _wintypes.DWORD)

    class _PowerThrottlingState(ctypes.Structure):
        # PROCESS_POWER_THROTTLING_STATE
        _fields_ = [('Version', _wintypes.ULONG), ('ControlMask', _wintypes.ULONG), ('StateMask', _wintypes.ULONG)]

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000
PROCESS_POWER_THROTTLING = 4             # ProcessPowerThrottling (PROCESS_INFORMATION_CLASS)
POWER_THROTTLING_EXECUTION_SPEED = 0x1   # PROCESS_POWER_THROTTLING_EXECUTION_SPEED


def free_physical_bytes():
    """Available physical memory (GlobalMemoryStatusEx.ullAvailPhys)."""
    if os.name != 'nt':
        pages = os.sysconf('SC_AVPHYS_PAGES')
        return pages * os.sysconf('SC_PAGE_SIZE')
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError(ctypes.get_last_error(), 'GlobalMemoryStatusEx failed')
    return int(status.ullAvailPhys)


def process_started(pid):
    """The creation time of a running `pid` (FILETIME ticks as an int, proclock.process_started), or
    None when it is not running or its creation time cannot be read."""
    return proclock.process_started(pid) or None


def pid_alive(pid, started=None):
    """True while `pid` runs. With `started`, a reused pid (another creation time) counts as gone.
    (proclock.alive: the one liveness rule of every lock and pid check in the app.)"""
    return proclock.alive(pid, started)


def execution_speed_unthrottled(handle=None):
    """Opt a process out of Windows power throttling of its execution speed (EcoQoS) without changing
    its priority class: SetProcessInformation(ProcessPowerThrottling, {Version 1, ControlMask
    EXECUTION_SPEED, StateMask 0}). Review finding H1: below-normal and windowless, the warmer was put
    on EcoQoS by Windows and got 0.21-0.27 of a core in a CPU-bound spin (0.94 with this set, 0.002
    with throttling forced on), so warm tasks took 10-100x longer on battery or under load. The trade:
    on battery the warmer may now run on the performance cores. `handle`: a process handle with
    PROCESS_SET_INFORMATION access (a child's Popen handle); None means this process. True when
    Windows accepted it; False elsewhere."""
    if os.name != 'nt':
        return False
    state = _PowerThrottlingState(1, POWER_THROTTLING_EXECUTION_SPEED, 0)
    target = _kernel32.GetCurrentProcess() if handle is None else int(handle)
    return bool(_kernel32.SetProcessInformation(target, PROCESS_POWER_THROTTLING, ctypes.byref(state),
                                                ctypes.sizeof(state)))


def power_throttling(handle=None):
    """{'control': ControlMask, 'state': StateMask} of a process's explicit power-throttling policy
    (GetProcessInformation), or None when it cannot be read. Control 1 with state 0: EcoQoS is off.
    `handle`: a process handle with PROCESS_QUERY_INFORMATION access; None means this process."""
    if os.name != 'nt':
        return None
    state = _PowerThrottlingState(1, 0, 0)
    target = _kernel32.GetCurrentProcess() if handle is None else int(handle)
    if not _kernel32.GetProcessInformation(target, PROCESS_POWER_THROTTLING, ctypes.byref(state),
                                           ctypes.sizeof(state)):
        return None
    return {'control': state.ControlMask, 'state': state.StateMask}


def set_below_normal_priority():
    """Below-normal priority, so the warmer yields to every interactive process, and no EcoQoS, so the
    CPU time it does get runs at full speed (execution_speed_unthrottled). Returns what was set."""
    if os.name != 'nt':
        with contextlib.suppress(OSError):
            os.nice(5)
        return {'priority': 'nice +5'}
    _kernel32.SetPriorityClass(_kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    return {'priority': 'below_normal', 'ecoqos_off': execution_speed_unthrottled()}


def _unthrottle_child(process):
    """execution_speed_unthrottled for a running subprocess.Popen; False when it cannot be applied."""
    handle = getattr(process, '_handle', None)
    if handle is None or process.poll() is not None:
        return False
    try:
        return execution_speed_unthrottled(handle)
    except Exception:   # never fails a scan: the scanner then runs as Windows schedules it
        return False


def unthrottle_native_scanners():
    """Turn EcoQoS off for the warmer's native scanners too (review round 2 of batch 1, after H1). A
    native_scan.Worker (mentat-scan.exe) inherits the warmer's below-normal priority class, but not its
    power-throttling opt-out, so Windows throttled it as it did the warmer before H1: 0.08-0.45 of a core
    in a CPU-bound spin, 0.93-0.96 with the opt-out, and a Graph comparison build's scan timed out after
    120 s. This wraps Worker.__init__ in this process so that every scanner started from now on is opted
    out right after it is spawned, and opts out the scanners already running. Only Warmer.run calls it,
    after lowering the warmer's own priority; the console's scanners are left as they are, and
    native_scan.py itself is unchanged. Returns what was done, or None off Windows."""
    if os.name != 'nt':
        return None
    module = sys.modules.get('native_scan')
    worker = getattr(module, 'Worker', None)
    if worker is None:
        return {'installed': False, 'reason': 'native_scan is not loaded'}
    if getattr(worker.__init__, '__prewarm_original__', None) is None:
        original = worker.__init__

        @functools.wraps(original)
        def start(self, *args, **kwargs):
            original(self, *args, **kwargs)
            _unthrottle_child(getattr(self, 'process', None))

        start.__prewarm_original__ = original
        worker.__init__ = start
    running = [item for item in list(getattr(module, '_all', ()) or ()) if getattr(item, 'process', None)]
    return {'installed': True, 'running': len(running),
            'running_unthrottled': sum(1 for item in running if _unthrottle_child(item.process))}


def sweep_pending(store, min_age=PENDING_MIN_AGE_SECONDS, now=None):
    """Remove the projection files that a builder which ended mid-build left behind (review round 2 of batch
    1, after H3). contribution_query_store.view and analysis_scan.view build a projection as
    <key>.<uuid>.pending in store.ROOT/views and store.ROOT/insights-views (a query view is 409-433 MB, an
    Insights view up to 200 MB) and remove it only in a `finally`, which os._exit skips: the warmer's exit
    after parent_gone or code_changed (exit_now, the watchdog) and any hard kill leave it for good.
    A builder creates its .pending file only while it holds the key's <key>.lock (contribution_query_store
    ._lock, an OS byte-range lock the OS frees when its process ends), and it holds that lock until the file
    is renamed or removed. So this takes the same lock, through the same function, for each key that has a
    .pending file at least `min_age` seconds old, and removes those files while it holds it: at that moment
    no live builder of the key exists. A live build keeps its file and makes this wait until it ends. Only
    names of the builders' exact form are touched. Returns {'removed': [paths], 'bytes': n, 'errors': [...]}."""
    root = Path(store.ROOT)
    moment = time.time() if now is None else now
    found = {}
    for folder in PENDING_SWEEP_FOLDERS:
        directory = root / folder
        try:
            with os.scandir(directory) as items:
                entries = list(items)
        except OSError:
            continue
        for item in entries:
            match = PENDING_NAME.match(item.name)
            if not match:
                continue
            try:
                if not item.is_file(follow_symlinks=False):
                    continue
                info = item.stat(follow_symlinks=False)
            except OSError:
                continue
            if moment - info.st_mtime >= min_age:
                found.setdefault((str(directory), match.group(1)), []).append((item.name, info.st_size))
    removed, errors, freed = [], [], 0
    for (directory, key), files in sorted(found.items()):
        directory = Path(directory)
        try:
            with store._lock(directory / (key + '.lock')):
                for name, size in files:
                    try:
                        (directory / name).unlink()
                    except FileNotFoundError:
                        continue   # its builder finished while this waited for the lock
                    except OSError as error:
                        errors.append(f'{name}: {type(error).__name__}: {error}')
                        continue
                    removed.append(str(directory / name))
                    freed += size
        except OSError as error:
            errors.append(f'{key}.lock: {type(error).__name__}: {error}')
    return {'removed': removed, 'bytes': freed, 'errors': errors}


# ----------------------------------------------------------------------------- recovery contract
class Stop(Exception):
    """A guard ended the run: code_changed or parent_gone."""

    def __init__(self, root_cause, message):
        super().__init__(message)
        self.root_cause = root_cause


class IndexMissing(Exception):
    pass


class LockHeld(Exception):
    pass


class TaskTimeout(Exception):
    pass


_CODE_CHANGED = re.compile(r'code changed|definitions changed|restart (the app|mentat|the console)', re.I)
_INDEX_MISSING = re.compile(r'analytical index is missing', re.I)
_LOCKED = re.compile(r'database is locked|being used by another process|still being prepared', re.I)
_SOURCE_CHANGED = re.compile(r'changed (while|before|during)|source (has )?changed|active (uncheckpointed )?writes'
                             r'|lineage changed|changed during source discovery', re.I)


def classify(error):
    """The root_cause_hint for one failure."""
    if isinstance(error, MemoryError):
        return 'low_memory'
    if isinstance(error, Stop):
        return error.root_cause
    if isinstance(error, IndexMissing):
        return 'index_missing'
    if isinstance(error, LockHeld):
        return 'lock_held_by_other_process'
    text = str(error)
    if _CODE_CHANGED.search(text):
        return 'code_changed'
    if _INDEX_MISSING.search(text):
        return 'index_missing'
    if isinstance(error, OSError) and getattr(error, 'winerror', None) in (32, 33):
        return 'lock_held_by_other_process'
    if _LOCKED.search(text):
        return 'lock_held_by_other_process'
    if _SOURCE_CHANGED.search(text):
        return 'source_changed_during_build'
    return 'unexpected_exception:' + type(error).__name__


def recovery(hint, consecutive=1):
    """(retry, stop) for a failure observation with this root cause."""
    quarantine = (f'{MAX_CONSECUTIVE_FAILURES} consecutive failures of one task quarantine it until its source '
                  f'fingerprint or code changes (this is failure {consecutive} of {MAX_CONSECUTIVE_FAILURES})')
    if hint == 'code_changed':
        return 'do not retry in this process', 'exit cleanly; the console relaunches the warmer with the new code'
    if hint == 'parent_gone':
        return 'do not retry', 'exit cleanly; the console that launched this warmer is gone'
    if hint == 'index_missing':
        return ('do not retry until the source fingerprint changes',
                'blocked until the analytical index exists (opening the industry in the console builds it, '
                'or run: prewarm.py warm --task <id> --allow-index-builds)')
    if hint in ('low_memory', 'console_busy'):
        return 'retry after 60 s', 'a pause is not a task failure and never quarantines; the guard is re-checked every 60 s'
    if hint == 'cache_budget':
        return ('retry at the next plan, when the response cache has room',
                'not a task failure and never quarantines: its response would push a stored click response out '
                '(raise the per-namespace cap in contribution_query_store._prune to warm more)')
    if hint == 'source_changed_during_build':
        return ('retry once now' if consecutive <= 1 else 'retry after 60 s'), quarantine
    return 'retry after 60 s', quarantine


def retry_delay(instruction):
    if instruction == 'retry once now':
        return 0.0
    match = re.fullmatch(r'retry after (\d+) s', instruction or '')
    return float(match.group(1)) if match else None


# ----------------------------------------------------------------------------- state files
class StateDir:
    """The harness's own files under <state root>/prewarm (hints.json is written by the console)."""

    def __init__(self, state_root):
        self.state_root = Path(state_root)
        self.dir = self.state_root / 'prewarm'
        self.hints = self.dir / 'hints.json'
        self.lock = self.dir / 'warmer.lock'
        self.ledger = self.dir / 'ledger.jsonl'
        self.ledger_old = self.dir / 'ledger.1.jsonl'
        self.plan = self.dir / 'plan.json'
        self.quarantine = self.dir / 'quarantine.json'
        self.verify = self.dir / 'verify.json'
        self.options = self.dir / 'options.json'
        self.heartbeat = self.dir / 'warmer.json'

    def ensure(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        return self


class WarmerLock:
    """Single instance. An exclusive OS byte-range lock on warmer.lock, which Windows releases when the
    holder dies, so a stale lock (holder pid dead) is simply free; the pid it names is recorded inside."""

    OFFSET = 0x7FFFFFF0  # locked byte lies beyond the JSON content, so readers can still read it

    def __init__(self, path):
        self.path = Path(path)
        self.fd = None

    def holder(self):
        value = read_json(self.path)
        return value if isinstance(value, dict) else None

    def acquire(self, info):
        """(True, previous holder or None) when acquired; (False, current holder) when another live
        process holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        previous = self.holder()
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o644)
        try:
            if os.name == 'nt':
                os.lseek(fd, self.OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False, self.holder() or previous
        self._write(fd, info)
        self.fd = fd
        self.info = info
        # A holder that released cleanly left released_utc; one that died did not (stale, pid dead).
        stale = previous if previous and previous.get('pid') != info.get('pid') and not previous.get('released_utc') else None
        return True, stale

    @staticmethod
    def _write(fd, info):
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(info, sort_keys=True).encode('utf-8'))
        os.fsync(fd)

    def release(self):
        if self.fd is None:
            return
        with contextlib.suppress(OSError):
            self._write(self.fd, {**getattr(self, 'info', {}), 'released_utc': utc_iso()})
        try:
            if os.name == 'nt':
                os.lseek(self.fd, self.OFFSET, os.SEEK_SET)
                with contextlib.suppress(OSError):
                    msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(self.fd)
            self.fd = None


class Ledger:
    """One JSON line per task observation; rotated to ledger.1.jsonl at 5 MB."""

    def __init__(self, state, limit=LEDGER_LIMIT_BYTES):
        self.state = state
        self.limit = limit
        self._lock = threading.Lock()

    def append(self, observation):
        line = json.dumps(observation, ensure_ascii=False, default=str, separators=(',', ':')) + '\n'
        with self._lock:
            self.state.ensure()
            try:
                if self.state.ledger.stat().st_size + len(line) > self.limit:
                    os.replace(self.state.ledger, self.state.ledger_old)
            except OSError:
                pass
            with open(self.state.ledger, 'a', encoding='utf-8', newline='\n') as stream:
                stream.write(line)

    def read(self):
        rows = []
        for path in (self.state.ledger_old, self.state.ledger):
            try:
                with open(path, encoding='utf-8') as stream:
                    for line in stream:
                        line = line.strip()
                        if line:
                            with contextlib.suppress(ValueError):
                                rows.append(json.loads(line))
            except OSError:
                continue
        return rows


def last_successes(rows):
    """task id -> its newest succeeded observation."""
    result = {}
    for row in rows:
        if row.get('outcome') == 'succeeded' and row.get('task_id'):
            result[row['task_id']] = row
    return result


# Failures that say nothing about the task itself: a blocked index, the warmer's own exit, a guard.
NOT_TASK_FAILURES = ('index_missing', 'code_changed', 'parent_gone', 'low_memory', 'console_busy', 'cache_budget')


def consecutive_failures(rows):
    """task id -> (count, source_fp, code_fp) of its trailing failures. Pauses, blocked indexes and the
    warmer's own exits do not count; a success, or a failure under other fingerprints, restarts it."""
    result = {}
    for row in rows:
        task = row.get('task_id')
        if not task:
            continue
        if row.get('outcome') == 'succeeded':
            result.pop(task, None)
        elif row.get('outcome') == 'failed' and row.get('root_cause_hint') not in NOT_TASK_FAILURES:
            count, source_fp, code_fp = result.get(task, (0, None, None))
            if (source_fp, code_fp) != (row.get('source_fp'), row.get('code_fp')):
                count = 0
            result[task] = (count + 1, row.get('source_fp'), row.get('code_fp'))
    return result


def read_hints(path):
    """hints.json (written by non-read-only consoles), newest first; malformed entries are ignored."""
    value = read_json(path)
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('requests'), list):
        return []
    hints = []
    for position, item in enumerate(value['requests'][:25]):
        if not isinstance(item, dict) or not isinstance(item.get('body'), dict):
            continue
        body = {key: item_value for key, item_value in item['body'].items() if key != 'retry'}
        dataset = item.get('dataset') or body.get('dataset')
        if not isinstance(dataset, str) or not dataset:
            continue
        if 'dataset' not in body:
            body['dataset'] = dataset
        hints.append({'body': body, 'key': item.get('key') if isinstance(item.get('key'), str) else None,
                      'dataset': dataset, 'seen_utc': item.get('seen_utc') if isinstance(item.get('seen_utc'), str) else '',
                      'position': position})
    hints.sort(key=lambda hint: (hint['seen_utc'] or '', -hint['position']), reverse=True)
    return hints


def hints_stamp(path):
    return file_stamp(path)


def industry_order(datasets, hints):
    """Datasets with the most recent hint first, then the rest in catalog order."""
    ordered = []
    for hint in hints:
        if hint['dataset'] in datasets and hint['dataset'] not in ordered:
            ordered.append(hint['dataset'])
    return ordered + [dataset for dataset in datasets if dataset not in ordered]


# ----------------------------------------------------------------------------- response-cache budget
def demoted_mtime_ns(day, position):
    """The mtime a warm response is given: its plan day, then its plan position (the first task gets the
    latest mtime of the day, so a prune removes the day's lowest-priority entries first)."""
    index = (dt.date.fromisoformat(day) - DEMOTED_EPOCH).days
    slot = DEMOTED_SLOTS - min(max(int(position), 0), DEMOTED_SLOTS - 1)
    return DEMOTED_BASE_NS + index * DEMOTED_DAY_NS + slot * 1000


def demoted_day(mtime_ns):
    """The plan day (local ISO date) a warm entry's mtime records; None for any other mtime."""
    if not DEMOTED_BASE_NS <= mtime_ns < DEMOTED_CEILING_NS:
        return None
    return (DEMOTED_EPOCH + dt.timedelta(days=(mtime_ns - DEMOTED_BASE_NS) // DEMOTED_DAY_NS)).isoformat()


def response_files(directory):
    """(mtime_ns, size, path) of each response JSON in one namespace folder, newest first (the order
    contribution_query_store._prune sorts them in)."""
    files = []
    try:
        entries = list(Path(directory).glob('*.json'))
    except OSError:
        return files
    for path in entries:
        try:
            info = path.stat()
        except OSError:
            continue
        files.append((info.st_mtime_ns, info.st_size, path))
    return sorted(files, reverse=True)


def warm_capacity(files, today, keep_files=PRUNE_KEEP_FILES, keep_bytes=PRUNE_KEEP_BYTES):
    """How many responses the warmer may still write today into a namespace holding `files`
    (response_files()). store._prune keeps the longest newest-first run within keep_files and keep_bytes
    and removes the rest, oldest first. A write is allowed only while everything that write, plus
    BUDGET_SLACK_FILES written by a click at the same moment, could remove is a warm entry from a day
    before `today`: warm entries of today and every response a click stored are never pushed out."""
    entry = max([BUDGET_MIN_ENTRY_BYTES] + [size for _, size, _ in files])
    removable = sorted((mtime, size) for mtime, size, _ in files
                       if (day := demoted_day(mtime)) is not None and day < today)   # oldest first
    count, total = len(files), sum(size for _, size, _ in files)
    capacity = 0
    while capacity < keep_files:
        extra = 1 + BUDGET_SLACK_FILES
        need_count, need_bytes, taken = count + extra, total + extra * entry, 0
        while need_count > keep_files or need_bytes > keep_bytes:
            if taken >= len(removable):
                return capacity
            need_count -= 1
            need_bytes -= removable[taken][1]
            taken += 1
        capacity += 1
        count, total = count + 1, total + entry          # the warm write itself happens
        while removable and (count > keep_files or total > keep_bytes):
            count, total = count - 1, total - removable.pop(0)[1]   # and prunes the oldest warm entries
    return capacity


def demote(paths, day, position):
    """Give each response the warmer wrote the demoted mtime of its plan day and position."""
    moved = []
    for path in paths:
        with contextlib.suppress(OSError):
            info = os.stat(path)
            if demoted_day(info.st_mtime_ns) is None:
                os.utime(path, ns=(info.st_atime_ns, demoted_mtime_ns(day, position)))
                moved.append(str(path))
    return moved


def _utc_ns(iso):
    try:
        moment = dt.datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return int(moment.timestamp() * 1e6) * 1000


def promote(entries):
    """(path, seen_utc) pairs: a warm entry a click has used since gets the click's time as its mtime."""
    moved = []
    for path, seen in entries:
        seen_ns = _utc_ns(seen) or time.time_ns()
        with contextlib.suppress(OSError):
            info = os.stat(path)
            if demoted_day(info.st_mtime_ns) is not None:
                os.utime(path, ns=(info.st_atime_ns, seen_ns))
                moved.append(str(path))
    return moved


# ----------------------------------------------------------------------------- value ranking (RACF 3.4-3.7)
# Paul, 2026-09-24: "replicational aware caching ... is the methodology we should integrate into the data
# retrieval generally". RACF sections 3.4-3.7, applied to the console's caches:
#   3.4 value   value = (access + importance) * rebuild_weight, where access is access_stats' decayed
#               frequency score, importance boosts each industry's defaults by use order (1 / (1 + rank))
#               and the most recently used industry by RECENT_INDUSTRY_BOOST, and
#               rebuild_weight = log2(2 + rebuild seconds) / (1 + size MB / SIZE_SCALE_MB) favours what is
#               slow to rebuild and cheap to hold.
#   3.5 tiers   the HOT SET is the highest-value candidates that fit the startup budget (HOT_MAX_ITEMS
#               items, HOT_BUDGET_SECONDS of estimated work, the free-memory floor); it is primed into the
#               console's memory first. Everything else stays background priority.
#   3.6 re-sync when a source fingerprint changes, that industry's hot items are re-primed first.
#   3.7 lookup  nearest tier first: memory, then the verified disk tier, then a build (response_cache,
#               ViewCache and the Insights store already look up in that order).
HOT_MAX_ITEMS = 20
HOT_BUDGET_SECONDS = 60.0
RECENT_INDUSTRY_BOOST = 1.0
ACCESS_IMPORTANCE_SHARE = 0.25          # a non-default view's share of its industry's importance
SIZE_SCALE_MB = 64.0
ACCESS_TOP_LIMIT = 200
PROBE_SECONDS = 0.2                     # an Insights scope is only probed at startup, never built
# Rebuild seconds assumed when neither access_stats nor the ledger measured one (perf.md orders of magnitude).
DEFAULT_COST_SECONDS = {'graph_default': 2.0, 'graph_view': 2.0, 'graph_drawer': 3.0, 'comparison_flows': 3.5,
                        'insights_scope': 12.0, 'query_view': 20.0, 'insights_default': 12.0,
                        'insights_neighbor': 5.0, 'insights_company': 5.0, 'graph_spark': 5.0, 'drawer_sidecars': 10.0,
                        'worksheets': 60.0}
DEFAULT_KINDS = ('graph_default', 'comparison_flows', 'query_view', 'insights_default')
# The kinds server.py records (record_access) and the prime replays.
ACCESS_GRAPH_KINDS = ('graph_view', 'graph_drawer')
# Every kind server.py records (record_access), and so the only access_stats rows the warmer's value ranking
# reads: use_order reads their bodies, task_access their scores. The cache layers' own 'store/<namespace>' and
# 'cache/<namespace>' rows have no body and rank no task; read with top(kinds=None) they filled the
# ACCESS_TOP_LIMIT window ahead of the requests people made (review of batch 1, round 3, 2026-09-24).
ACCESS_REQUEST_KINDS = ACCESS_GRAPH_KINDS + ('comparison_flows', 'insights_query', 'contribution_query')


def access_key(body):
    """The key server.py records an access under: sha256 of the canonical JSON of its parameters or body."""
    return digest(body)[:32]


def load_access_top(kinds=None, limit=ACCESS_TOP_LIMIT, module=None):
    """access_stats.top() rows (builder "value"'s module), or [] when it is missing or fails: the ranking
    then falls back to hints only."""
    try:
        stats = module or __import__('access_stats')
    except ImportError:
        return []
    try:
        rows = stats.top(kinds=list(kinds) if kinds else None, limit=limit)
    except Exception as error:
        log(f'access_stats.top failed: {type(error).__name__}: {error}')
        return []
    return [row for row in rows or [] if isinstance(row, dict) and row.get('kind') and row.get('key') is not None]


def warmer_access_top(limit=ACCESS_TOP_LIMIT, module=None):
    """load_access_top() over ACCESS_REQUEST_KINDS: the rows the warmer re-plans by (Warmer.replan)."""
    return load_access_top(kinds=ACCESS_REQUEST_KINDS, limit=limit, module=module)


def _number(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def rebuild_weight(cost_s, size_bytes=None):
    size_mb = max(0.0, _number(size_bytes)) / 1024 ** 2
    return math.log2(2.0 + max(0.0, _number(cost_s))) / (1.0 + size_mb / SIZE_SCALE_MB)


def _industry_of(value, industries):
    """A recorded industry (label, dataset id or slug) as one of `industries`, or None."""
    if not isinstance(value, str) or not value:
        return None
    wanted = value.strip().lower()
    for name in industries:
        if name.lower() == wanted or slug(name) == slug(value):
            return name
    return None


def use_order(industries, hints, access_rows=()):
    """Industries by last use, newest first (hints' seen_utc and access_stats' last_utc), then the
    rest in catalog order; and the most recently used industry (None before any use)."""
    last = {}
    for hint in hints or []:
        name = _industry_of(hint.get('dataset'), industries)
        if name:
            last[name] = max(last.get(name, ''), hint.get('seen_utc') or '')
    for row in access_rows or []:
        body = row.get('body') if isinstance(row.get('body'), dict) else {}
        name = _industry_of(body.get('industry') or body.get('dataset'), industries)
        if name:
            last[name] = max(last.get(name, ''), str(row.get('last_utc') or ''))
    used = sorted((name for name in industries if name in last), key=lambda name: (last[name], -industries.index(name)),
                  reverse=True)
    return used + [name for name in industries if name not in used], (used[0] if used else None)


def industry_importance(industries, hints, access_rows=()):
    order, recent = use_order(industries, hints, access_rows)
    weights = {name: 1.0 / (1 + rank) for rank, name in enumerate(order)}
    if recent:
        weights[recent] += RECENT_INDUSTRY_BOOST
    return weights, order, recent


def graph_default_params(industry):
    """server.py's recorded /api/graph parameters for graph.js's first load: parse_qs drops blank values."""
    params = {key: values[0] for key, values in urllib.parse.parse_qs(graph_first_load_query(industry)).items()}
    params['industry'] = industry
    return params


def comparison_params(industry):
    return {'id': 'published-reports', 'industry': industry}


def _candidate(kind, industry, key, body, *, access=0.0, importance=0.0, cost_s=None, size=None, source, order):
    cost = _number(cost_s, DEFAULT_COST_SECONDS.get(kind, 5.0)) if cost_s is not None else DEFAULT_COST_SECONDS.get(kind, 5.0)
    value = (max(0.0, _number(access)) + importance) * rebuild_weight(cost, size)
    return {'id': f'{kind}:{slug(industry or "-")}:{str(key)[:12]}', 'kind': kind, 'industry': industry, 'key': key,
            'body': body, 'access': round(_number(access), 6), 'importance': round(importance, 6),
            'cost_s': round(cost, 3), 'size': size, 'value': round(value, 6), 'source': source, 'order': order,
            'est_s': PROBE_SECONDS if kind == 'insights_scope' else cost}


def prime_candidates(industries, hints, access_rows, today):
    """Everything the console can prime into memory at startup, valued (RACF 3.4): each industry's Graph
    first-load view and comparison flows (its defaults), the Graph views and drawer views access_stats
    ranks, and today's Insights scopes from hints (probed from disk, never built here)."""
    industries = list(industries)
    weights, order, recent = industry_importance(industries, hints, access_rows)
    access = {}
    for row in access_rows or []:
        access[(row['kind'], str(row['key']))] = row
    candidates, seen = [], set()

    def add(candidate):
        identity = (candidate['kind'], str(candidate['key']))
        if identity not in seen:
            seen.add(identity)
            candidates.append(candidate)

    for name in order:
        for kind, params in (('graph_default', graph_default_params(name)), ('comparison_flows', comparison_params(name))):
            key = access_key(params)
            row = access.get(('graph_view' if kind == 'graph_default' else kind, key)) or {}
            add(_candidate(kind, name, key, params, access=row.get('score', 0.0), importance=weights[name],
                           cost_s=row.get('cost_s'), size=row.get('size'), source='default', order=len(candidates)))
    for row in access_rows or []:
        if row['kind'] not in ACCESS_GRAPH_KINDS or not isinstance(row.get('body'), dict):
            continue
        name = _industry_of(row['body'].get('industry'), industries)
        if not name or (row['kind'] == 'graph_view' and str(row['key']) == access_key(graph_default_params(name))):
            continue
        add(_candidate(row['kind'], name, str(row['key']), row['body'], access=row.get('score', 0.0),
                       importance=ACCESS_IMPORTANCE_SHARE * weights[name], cost_s=row.get('cost_s'),
                       size=row.get('size'), source='access', order=len(candidates)))
    for position, hint in enumerate(hints or []):
        name = _industry_of(hint.get('dataset'), industries)
        if not name or not hint.get('key') or _local_day(hint.get('seen_utc')) != today:
            continue   # an earlier day's scope keys an earlier as_of: today's click needs a build
        body = {key: value for key, value in hint['body'].items() if key != 'retry'}
        row = access.get(('insights_query', access_key(body))) or {}
        add(_candidate('insights_scope', name, hint['key'], body, access=row.get('score', 0.0),
                       importance=ACCESS_IMPORTANCE_SHARE * weights[name] + 1.0 / (1 + position),
                       cost_s=row.get('cost_s'), size=row.get('size'), source='hint', order=len(candidates)))
    return candidates, {'order': order, 'recent': recent, 'weights': {name: round(value, 6) for name, value in weights.items()}}


def select_hot_set(candidates, max_items=HOT_MAX_ITEMS, budget_seconds=HOT_BUDGET_SECONDS):
    """(hot, background, estimated seconds): candidates by value (ties keep candidate order), each taken
    while the hot set has fewer than max_items and its estimated work stays within budget_seconds."""
    ranked = sorted(candidates, key=lambda item: (-item['value'], item['order']))
    hot, background, spent = [], [], 0.0
    for item in ranked:
        if len(hot) < max_items and spent + item['est_s'] <= budget_seconds:
            hot.append({**item, 'tier': 'hot'})
            spent += item['est_s']
        else:
            background.append({**item, 'tier': 'background',
                               'why': 'hot set full' if len(hot) >= max_items else 'over the startup budget'})
    return hot, background, round(spent, 3)


def task_access(task, access):
    """The access_stats row that speaks for a planned warmer task (see server.py record_access)."""
    kind, body = task['kind'], task.get('body') or {}
    if kind in ('query_view', 'graph_spark'):
        return access.get(('contribution_query', access_key(body)))
    if kind in INSIGHTS_KINDS:
        return access.get(('insights_query', access_key({k: v for k, v in body.items() if k != 'retry'})))
    if kind == 'comparison_flows':
        return access.get(('comparison_flows', access_key(comparison_params(task['dataset']))))
    return None


def value_order(tasks, *, hints=(), access_rows=None, reprime=(), successes=None, max_hot=HOT_MAX_ITEMS):
    """The warmer's plan re-ranked by value (RACF 3.4). Without access data and without an industry to
    re-prime, the plan keeps its hints-only order untouched. Otherwise: the max_hot most valuable tasks
    form the hot tier, a re-primed industry's hot tasks go first (3.6), then the rest of the hot tier by
    value, then every background task in its original order. Each task gains 'value' and 'tier'."""
    if not access_rows and not reprime:
        return tasks
    successes = successes or {}
    industries = []
    for task in tasks:
        if task['dataset'] not in industries:
            industries.append(task['dataset'])
    weights, _, _ = industry_importance(industries, list(hints or []), access_rows or [])
    access = {(row['kind'], str(row['key'])): row for row in access_rows or []}
    for index, task in enumerate(tasks):
        row = task_access(task, access) or {}
        importance = weights.get(task['dataset'], 0.0) * (1.0 if task['kind'] in DEFAULT_KINDS else ACCESS_IMPORTANCE_SHARE)
        cost = row.get('cost_s') or (successes.get(task['id']) or {}).get('seconds') or DEFAULT_COST_SECONDS.get(task['kind'], 5.0)
        task['value'] = round((max(0.0, _number(row.get('score'))) + importance) * rebuild_weight(cost, row.get('size')), 6)
    ranked = sorted(range(len(tasks)), key=lambda index: (-tasks[index]['value'], index))
    hot = set(ranked[:max(0, int(max_hot))])
    reprimed = [index for index in ranked if index in hot and tasks[index]['dataset'] in set(reprime)]
    rest_hot = [index for index in ranked if index in hot and index not in reprimed]
    background = [index for index in range(len(tasks)) if index not in hot]
    for index in reprimed:
        tasks[index]['tier'] = 'reprime'
    for index in rest_hot:
        tasks[index]['tier'] = 'hot'
    for index in background:
        tasks[index]['tier'] = 'background'
    return [tasks[index] for index in reprimed + rest_hot + background]


class AccessRecorder:
    """Hands server.py's access records to access_stats.record on one daemon thread, so recording never
    delays or fails a response: submit() only enqueues; a full queue drops the record."""

    def __init__(self, record, maxsize=1024):
        self._record = record
        self._queue = queue.Queue(maxsize=maxsize)
        self.recorded = self.dropped = self.failed = 0
        self._thread = threading.Thread(target=self._drain, name='mentat-access-stats', daemon=True)
        self._thread.start()

    @classmethod
    def start(cls, module=None):
        try:
            stats = module or __import__('access_stats')
        except ImportError:
            log('access_stats is not installed: access recording is off (hints-only ranking)')
            return None
        record = getattr(stats, 'record', None)
        return cls(record) if callable(record) else None

    def submit(self, kind, body, cost_s=None, size=None):
        try:
            self._queue.put_nowait((kind, body, cost_s, size))
        except Exception:
            self.dropped += 1

    def _drain(self):
        while True:
            kind, body, cost_s, size = self._queue.get()
            try:
                self._record(kind, access_key(body), body=body, cost_s=cost_s, size=size)
                self.recorded += 1
            except Exception:
                self.failed += 1
            finally:
                self._queue.task_done()


# ----------------------------------------------------------------------------- planning
# Plan order. 'other_basis' orders the newest hint's other-basis neighbours after every company of every
# industry; those tasks keep the insights_neighbor kind (and so their task ids and ledger history).
KIND_RANK = {'query_view': 1, 'insights_default': 2, 'insights_neighbor': 3, 'insights_company': 4,
             'other_basis': 5, 'graph_spark': 6, 'comparison_flows': 7, 'drawer_sidecars': 8, 'worksheets': 9}
INSIGHTS_KINDS = ('insights_default', 'insights_neighbor', 'insights_company')
QUERY_KINDS = ('query_view', 'graph_spark')
# Kinds that build another builder's cache (SP build/cache and build/drawer receipts): no analytical index
# and no contribution-query response is involved, and each needs more memory headroom than a query.
ARTIFACT_KINDS = ('comparison_flows', 'drawer_sidecars', 'worksheets')
ARTIFACT_EXTRA_FREE_BYTES = 768 * 1024 ** 2   # Banking: flows about +0.5 GB transient, drawer 0.6 GB peak


def response_group(kind):
    """The contribution-query response namespaces a task of this kind writes (RESPONSE_NAMESPACES key),
    or None when it writes none."""
    if kind in QUERY_KINDS:
        return 'query'
    return 'insights' if kind in INSIGHTS_KINDS else None


def task_id(kind, dataset, body):
    return f'{kind}:{slug(dataset)}:{digest(body)[:12]}'


def make_task(kind, dataset, body, rank, reason, source, order=None):
    return {'id': task_id(kind, dataset, body), 'kind': kind, 'dataset': dataset, 'body': body,
            'rank': [KIND_RANK[order or kind], *rank], 'reason': reason, 'source_fp': source.get('source_fp'),
            'index_present': bool(source.get('index_present')), 'state': 'pending'}


def neighbor_bodies(hint_body, companies, max_company_neighbors=DEFAULT_MAX_COMPANY_NEIGHBORS, exclude=()):
    """The newest hint's neighbours in the client's select order: 'All covered companies' (the body
    without a company key) and every other company, then every other basis for the same company.
    Bodies already planned (`exclude`, canonical JSON) are left out before the company cap applies."""
    base = {key: value for key, value in hint_body.items() if key != 'retry'}
    current = base.get('company') or ''
    company_bodies = []
    if current:
        company_bodies.append({key: value for key, value in base.items() if key != 'company'})
    for company in companies or []:
        if isinstance(company, str) and company and company != current:
            company_bodies.append({**base, 'company': company})
    company_bodies = [body for body in company_bodies if canonical(body) not in exclude]
    company_bodies = company_bodies[:max(0, int(max_company_neighbors))]
    basis = base.get('basis') or 'receipt'
    basis_bodies = [{**base, 'basis': other} for other in INSIGHTS_BASES if other != basis]
    return company_bodies, [body for body in basis_bodies if canonical(body) not in exclude]


def already_warm(task, success, today, code_fp, exists=os.path.exists):
    """True when the ledger's last success still describes the cache the console would read."""
    if not success or success.get('source_fp') != task.get('source_fp') or success.get('code_fp') != code_fp:
        return False
    if task['kind'] in INSIGHTS_KINDS:
        wanted = task['body'].get('as_of') or today
        if success.get('as_of') != wanted:
            return False
    # A query view warmed without its response (no room in the response cache) is warm while its
    # projection exists: the projection is the cost the click would otherwise pay.
    path = success.get('response_path') or (success.get('detail') or {}).get('view_path')
    return bool(path) and exists(path)


def company_bodies(dataset, companies):
    """insights.js's first-open body with each company of the select chosen, in the select's order."""
    return [{**insights_default_body(dataset), 'company': company}
            for company in companies or [] if isinstance(company, str) and company]


def build_plan(backend, *, hints, today, code_fp, industries=None, allow_index_builds=False,
               max_company_neighbors=DEFAULT_MAX_COMPANY_NEIGHBORS, options_cache=None, successes=None,
               quarantine=None, hints_file_stamp=None, exists=os.path.exists, access_rows=None, reprime=()):
    """The ordered task list. Pure given the backend's answers, so tests drive it with fakes.
    access_rows (access_stats.top()) and reprime (industries whose source changed) re-rank it by value
    (value_order); without either the order is the hints-only order."""
    options_cache = options_cache or {}
    successes = successes or {}
    quarantine = dict(quarantine or {})
    sources = backend.sources()
    by_dataset = {item['dataset']: item for item in sources}
    wanted = [item['dataset'] for item in sources if item.get('available')]
    if industries:
        wanted = [dataset for dataset in wanted if dataset in industries]
    usable_hints = [hint for hint in hints if hint['dataset'] in wanted]
    order = industry_order(wanted, usable_hints)
    tasks, skipped = [], []

    # A dataset whose published totals fail the source guard is refused by every analytical route
    # (server.py:2438 strict verify), so nothing of it is planned; the client cannot open it either.
    unverified = [dataset for dataset in order if by_dataset[dataset].get('verified') is False]
    for dataset in unverified:
        skipped.append({'kind': '*', 'dataset': dataset, 'reason': 'source_unverified',
                        'root_cause_hint': 'unexpected_exception:ValueError', 'message': by_dataset[dataset].get('message')})
    order = [dataset for dataset in order if dataset not in unverified]
    usable_hints = [hint for hint in usable_hints if hint['dataset'] in order]

    for rank, dataset in enumerate(order):
        source = by_dataset[dataset]
        try:
            body = backend.query_view_body(dataset)
        except Stop:
            raise
        except Exception as error:  # a dataset Graph cannot draw has no sparkline to warm
            skipped.append({'kind': 'query_view', 'dataset': dataset, 'reason': 'no_graph_view',
                            'root_cause_hint': classify(error), 'message': str(error)})
            continue
        if body is None:
            skipped.append({'kind': 'query_view', 'dataset': dataset, 'reason': 'no_sparkline_request'})
            continue
        tasks.append(make_task('query_view', dataset, body, [rank, 0], 'Graph sparkline first-load query', source))

    for rank, dataset in enumerate(order):
        tasks.append(make_task('insights_default', dataset, insights_default_body(dataset), [rank, 0],
                               'insights.js first-open default', by_dataset[dataset]))

    newest = usable_hints[0] if usable_hints else None
    pending_expansion = None
    if newest:
        dataset = newest['dataset']
        source = by_dataset[dataset]
        rank = order.index(dataset)
        body = newest['body']
        tasks.append(make_task('insights_neighbor', dataset, body, [0, rank, 0], 'newest hint scope', source))
        cached = (options_cache.get(dataset) or {}).get(body.get('basis') or 'receipt') or {}
        companies = cached.get('companies') if cached.get('source_fp') == source.get('source_fp') else None
        if companies is None:
            pending_expansion = {'dataset': dataset, 'basis': body.get('basis') or 'receipt'}
        planned = {canonical(task['body']) for task in tasks if task['kind'] != 'query_view' and task['dataset'] == dataset}
        neighbor_companies, basis_bodies = neighbor_bodies(body, companies or [], max_company_neighbors, planned)
        for index, neighbor in enumerate(neighbor_companies, 1):
            tasks.append(make_task('insights_neighbor', dataset, neighbor, [0, rank, index],
                                   'newest hint: other company' if neighbor.get('company') else
                                   'newest hint: All covered companies', source))
        # After every company of every industry: the newest hint's scope at each other basis.
        for index, neighbor in enumerate(basis_bodies, 1):
            tasks.append(make_task('insights_neighbor', dataset, neighbor, [rank, index],
                                   'newest hint: other basis', source, order='other_basis'))

    # Every company of every industry at the default body, industries in hint order, companies in the
    # order of the client's company select: the default result's options.companies (installOptions()).
    pending_companies = []
    for rank, dataset in enumerate(order):
        source = by_dataset[dataset]
        cached = (options_cache.get(dataset) or {}).get('receipt') or {}
        if cached.get('source_fp') != source.get('source_fp') or not isinstance(cached.get('companies'), list):
            pending_companies.append(dataset)   # known once its default result has been warmed
            continue
        for index, body in enumerate(company_bodies(dataset, cached['companies'])):
            tasks.append(make_task('insights_company', dataset, body, [rank, index],
                                   'every company: insights.js default with the company chosen', source))

    # Graph neighbours of the newest hint's industry: each company's sparkline at the first-load cycle.
    graph_companies = getattr(backend, 'graph_company_bodies', None)
    if newest and callable(graph_companies):
        dataset = newest['dataset']
        try:
            for index, (company, body) in enumerate(graph_companies(dataset)):
                tasks.append(make_task('graph_spark', dataset, body, [order.index(dataset), index],
                                       f'Graph company {company}: sparkline at the first-load cycle', by_dataset[dataset]))
        except Stop:
            raise
        except Exception as error:   # a Graph that cannot draw the industry has no company sparklines
            skipped.append({'kind': 'graph_spark', 'dataset': dataset, 'reason': 'no_graph_company_views',
                            'root_cause_hint': classify(error), 'message': str(error)})

    # Last, the other builders' caches, when their hooks are installed: each industry's
    # /api/comparison-flows answer and the Graph drawer's sidecars.
    hooks = getattr(backend, 'artifact_hooks', None)
    for kind in (hooks() if callable(hooks) else ()):
        if kind in ARTIFACT_KINDS:
            for rank, dataset in enumerate(order):
                tasks.append(make_task(kind, dataset, {'artifact': kind, 'industry': dataset}, [rank],
                                       {'comparison_flows': 'comparison-flows answer (cache receipt hook)',
                                        'drawer_sidecars': 'Graph drawer sidecars (drawer receipt hook)',
                                        'worksheets': 'report-data answers (worksheet cache hook)'}[kind],
                                       by_dataset[dataset]))

    # One task per request: a hint that is the default body (or repeats a neighbour) is warmed once.
    unique, seen = [], set()
    for task in sorted(tasks, key=lambda item: item['rank']):
        # (A graph_spark body carries a company filter, so it never equals a query_view body or any
        # Insights body; the original two-way identity stays exact.)
        identity = ('query' if task['kind'] == 'query_view' else 'insights', task['dataset'], canonical(task['body']))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(task)
    # RACF 3.4-3.6: by value when access_stats has data or a source changed; before the budget below,
    # so the response-cache room goes to the most valuable tasks first.
    unique = value_order(unique, hints=hints, access_rows=access_rows, reprime=reprime, successes=successes)

    released = []
    for task in unique:
        entry = quarantine.get(task['id'])
        if entry and (entry.get('source_fp') != task['source_fp'] or entry.get('code_fp') != code_fp):
            released.append(task['id'])
            quarantine.pop(task['id'], None)
            entry = None
        if entry:
            task['state'] = 'quarantined'
        elif not task['index_present'] and not allow_index_builds and task['kind'] not in ARTIFACT_KINDS:
            task['state'] = 'blocked'
            task['root_cause_hint'] = 'index_missing'
        elif already_warm(task, successes.get(task['id']), today, code_fp, exists):
            task['state'] = 'warm'

    # The response-cache budget, in priority order: a pending task may write one response into each
    # namespace of its group. Past the room, a query view is warmed as its projection only and every
    # other task waits as over_budget (never failed, never quarantined).
    budget = {}
    capacity_of = getattr(backend, 'response_capacity', None)
    for group, namespaces in RESPONSE_NAMESPACES.items():
        room = [capacity_of(namespace, today) for namespace in namespaces] if callable(capacity_of) else [None]
        budget[group] = {'room_at_plan': None if None in room else min(room), 'planned_writes': 0, 'over_budget': 0}
    for task in unique:
        group = response_group(task['kind'])
        if task['state'] != 'pending' or group is None:
            continue
        entry = budget[group]
        if entry['room_at_plan'] is None:
            continue
        if entry['planned_writes'] < entry['room_at_plan']:
            entry['planned_writes'] += 1
        elif task['kind'] == 'query_view':
            task['view_only'] = True
        else:
            task['state'] = 'over_budget'
            task['root_cause_hint'] = 'cache_budget'
            entry['over_budget'] += 1

    return {'schema': SCHEMA, 'created_utc': utc_iso(), 'as_of': today, 'code_fp': code_fp,
            'hints_stamp': hints_file_stamp,
            'newest_hint': {'dataset': newest['dataset'], 'body': newest['body'], 'key': newest['key'],
                            'seen_utc': newest['seen_utc']} if newest else None,
            'sources': {item['dataset']: {key: item.get(key) for key in
                                          ('available', 'verified', 'source_fp', 'index_present', 'message')}
                        for item in sources},
            'order': order, 'tasks': unique, 'skipped': skipped, 'released_quarantine': released,
            'quarantine': quarantine, 'pending_expansion': pending_expansion,
            'pending_companies': pending_companies, 'budget': budget,
            'ranking': 'value' if (access_rows or reprime) else 'hints', 'reprime': sorted(reprime or ())}


def plan_counts(plan):
    counts = {}
    for task in plan['tasks']:
        counts.setdefault(task['kind'], {}).setdefault(task['state'], 0)
        counts[task['kind']][task['state']] += 1
    return counts


def sources_signature(sources):
    return {item['dataset']: [item.get('available'), item.get('source_fp'), item.get('index_present')]
            for item in sources}


# ----------------------------------------------------------------------------- sandbox + write guard
STATE_REDIRECTS = (('contribution_query_store', 'ROOT', 'contribution-query-cache'),
                   ('analysis_history', 'ROOT', 'insights-history'),
                   ('contribution_source_guard', 'CACHE_ROOT', 'contribution-source-proofs'),
                   ('analysis_data', 'ROOT', 'insights'),
                   ('response_cache', 'ROOT', 'response-cache'))
# Read-only inputs that stay pointed at the real state: the analytical indexes and accepted lineage.
READ_ONLY_STATE = {('contribution_data', 'CACHE_ROOT'), ('contribution_source_guard', 'ACCEPTED_ROOT')}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_guard_state = {'installed': False, 'guarded': (), 'allowed': (), 'refusals': 0, 'log': None}


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path).replace('file:///', '').replace('file:', '')
                                            .split('?')[0])).rstrip('\\/') + os.sep


def _guarded(target):
    text = _norm(target)
    if any(text.startswith(allowed) for allowed in _guard_state['allowed']):
        return False
    return any(text.startswith(guarded) for guarded in _guard_state['guarded'])


def _refuse(event, detail):
    _guard_state['refusals'] += 1
    with contextlib.suppress(Exception):
        if _guard_state['log']:
            with open(_guard_state['log'], 'a', encoding='utf-8') as stream:
                stream.write(f'{utc_iso()} {event} {detail}\n')
    raise PermissionError(f'prewarm sandbox refused {event}: {detail}')


def _audit(event, args):
    if not _guard_state['installed']:
        return
    if event == 'open':
        path, mode, flags = args
        if path is None or isinstance(path, int):
            return
        writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or \
                  (isinstance(flags, int) and bool(flags & _WRITE_FLAGS))
        if writing and _guarded(path):
            _refuse('open', f'{path} mode={mode} flags={flags}')
    elif event in ('os.replace', 'os.rename'):
        if _guarded(args[0]) or _guarded(args[1]):
            _refuse(event, f'{args[0]} -> {args[1]}')
    elif event in ('os.link', 'os.symlink'):
        if len(args) > 1 and _guarded(args[1]):
            _refuse(event, f'{args[0]} -> {args[1]}')
    elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.truncate', 'shutil.rmtree'):
        if args and not isinstance(args[0], int) and _guarded(args[0]):
            _refuse(event, str(args[0]))
    elif event == 'sqlite3.connect':
        target = str(args[0])
        if 'mode=ro' not in target.lower() and _guarded(target):
            _refuse(event, target)


def install_write_guard(guarded, allowed, log_path=None):
    """Refuse every write under `guarded` except under `allowed` (sys.addaudithook: process-wide)."""
    _guard_state['guarded'] = tuple(_norm(path) for path in guarded if path)
    _guard_state['allowed'] = tuple(_norm(path) for path in allowed if path)
    _guard_state['log'] = str(log_path) if log_path else None
    if not _guard_state['installed']:
        sys.addaudithook(_audit)
        _guard_state['installed'] = True


REPOSITORY = PROJECT.parent   # C:\Users\pgoff\Mentat: the whole live checkout is guarded, not just mentat/


def _outside_project(path):
    return not _norm(path).startswith(_norm(REPOSITORY))


# ----------------------------------------------------------------------------- the real backend
class StoreObserver:
    """Records every store.cached(namespace, identity) call, so the key a warm produced is observed on
    the real code path rather than recomputed here. Calls through unchanged."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.calls = []
        original = getattr(store.cached, '__prewarm_original__', store.cached)

        @functools.wraps(original)
        def observed(namespace, identity, compute, stable=lambda: True):
            with self.lock:
                self.calls.append((namespace, copy.deepcopy(identity)))
            return original(namespace, identity, compute, stable)

        observed.__prewarm_original__ = original
        store.cached = observed
        self.original = original

    def reset(self):
        with self.lock:
            self.calls.clear()

    def last(self, namespace):
        with self.lock:
            return next((identity for name, identity in reversed(self.calls) if name == namespace), None)

    def response_path(self, namespace, identity):
        key = self.store._key([self.store.LOADED_SHA, namespace, identity])
        return self.store.ROOT / 'responses' / namespace / (key + '.json')


class HintObserver:
    """Records the key analysis_data.query() hands to _hint(body, key, dataset). query() calls it on
    every answered request (a stored result answered synchronously, a job still preparing, a job
    ready), so it is the one place that sees the key a click produces, and it is exactly the key the
    console writes into hints.json. In the warmer HINTS_ENABLED is False, so the call writes nothing."""

    def __init__(self, analysis):
        self.analysis = analysis
        self.lock = threading.Lock()
        self.keys = []
        original = getattr(analysis._hint, '__prewarm_original__', analysis._hint)

        @functools.wraps(original)
        def observed(body, key, dataset):
            with self.lock:
                self.keys.append((key, dataset, canonical({k: v for k, v in body.items() if k != 'retry'})))
            return original(body, key, dataset)

        observed.__prewarm_original__ = original
        analysis._hint = observed

    def reset(self):
        with self.lock:
            self.keys.clear()

    def last(self):
        with self.lock:
            return self.keys[-1][0] if self.keys else None


class WriteObserver:
    """Records every file contribution_query_store._atomic writes in this process: the response files
    (and proofs) a warm task itself stored, as opposed to responses a click stored. Calls through
    unchanged."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.paths = []
        original = getattr(store._atomic, '__prewarm_original__', store._atomic)

        @functools.wraps(original)
        def observed(path, payload):
            result = original(path, payload)
            with self.lock:
                self.paths.append(Path(path))
            return result

        observed.__prewarm_original__ = original
        store._atomic = observed

    def reset(self):
        with self.lock:
            self.paths.clear()

    def responses(self):
        """The response files written since the last reset (store.ROOT/responses/<namespace>/*.json)."""
        root = _norm(Path(self.store.ROOT) / 'responses')
        with self.lock:
            paths = list(self.paths)
        return [str(path) for path in dict.fromkeys(paths) if _norm(path).startswith(root)]


class AppBackend:
    """The real Mentat code paths, imported and configured the way server.py does it."""

    def __init__(self, *, data_root=None, state_root=None, allow_index_builds=False, guard_extra_allowed=()):
        self.data_root = Path(data_root or default_data_root()).resolve()   # server.py:2891, :2896
        self.state_root = Path(state_root).resolve() if state_root else DEFAULT_STATE_ROOT.resolve()
        self.sandboxed = _norm(self.state_root) != _norm(DEFAULT_STATE_ROOT)
        self.allow_index_builds = bool(allow_index_builds)
        self.guard_extra_allowed = tuple(guard_extra_allowed)
        self.loaded = False
        self.report_directory = None
        self._graph_memo = {}
        self.notes = []

    # -- setup -------------------------------------------------------------------------------
    def load(self):
        if self.loaded:
            return self
        if self.sandboxed and self.allow_index_builds:
            raise ValueError('--allow-index-builds writes the real analytical index directory; it cannot run '
                             'against a scratch --state-root.')
        if self.sandboxed and not _outside_project(self.state_root):
            raise ValueError('A scratch --state-root must lie outside the Mentat checkout.')
        for path in (str(PROJECT / 'tools'), str(APP)):          # server.py:34-35
            if path not in sys.path:
                sys.path.insert(0, path)
        from report_directory import report_directory
        self.report_directory = report_directory(PROJECT)         # server.py:2899, Console.__init__:964
        os.chdir(self.report_directory)                           # server.py:2900
        if self.sandboxed:
            # Bytecode caching is the one harmless write into the checkout; switching it off keeps the
            # refusal log to real findings only.
            sys.dont_write_bytecode = True
            self.state_root.mkdir(parents=True, exist_ok=True)
            install_write_guard([REPOSITORY, self.report_directory, self.data_root],
                                [self.state_root, *self.guard_extra_allowed],
                                self.state_root / 'prewarm-write-refusals.log')
        import contribution_data
        import contribution_query
        import contribution_query_store
        import contribution_workbook_guard
        import contribution_source_guard
        import analysis_history
        import analysis_scan
        import industry_scope
        import snapshot_registry
        import view_cache
        import analysis_data
        import server as console
        self.data, self.query, self.store = contribution_data, contribution_query, contribution_query_store
        self.guard, self.source_guard = contribution_workbook_guard, contribution_source_guard
        self.history, self.scan, self.analysis = analysis_history, analysis_scan, analysis_data
        self.industry_scope, self.snapshot_registry, self.view_cache = industry_scope, snapshot_registry, view_cache
        self.console = console
        self._install_source_memo()
        self._configure_caches()
        if self.sandboxed:
            self._redirect_state(self.state_root)
        # The warmer never records hints: its own requests are not the user's intent.
        analysis_data.HINTS_ENABLED = False
        if not self.allow_index_builds:
            self._refuse_index_builds()
        self.observer = StoreObserver(contribution_query_store)
        self.writes = WriteObserver(contribution_query_store)
        self.hints = HintObserver(analysis_data) if callable(getattr(analysis_data, '_hint', None)) else None
        if self.hints is None:
            self.notes.append('analysis_data has no _hint(); Insights keys are observed at store.cached only')
        self.loaded = True
        return self

    def insights_request_key(self):
        """The key of the last Insights request: from _hint() when the console code has it, else from
        the store call (older code, or a build this process ran)."""
        if self.hints is not None and self.hints.last():
            return self.hints.last()
        identity = self.observer.last('insights')
        return identity.get('request_key') if isinstance(identity, dict) else None

    def insights_response_path(self, key):
        return self.observer.response_path('insights', {'request_key': key}) if key else None

    def _install_source_memo(self):
        # The console installs source_memo (another builder's module) before serving; a warm must run the
        # same code path as the click, so it is installed here too when present.
        if not (APP / 'source_memo.py').is_file():
            return
        try:
            import source_memo
            install = getattr(source_memo, 'install', None)
            if callable(install):
                install()
                self.notes.append('source_memo.install() applied')
        except Exception as error:
            self.notes.append(f'source_memo.install() failed: {type(error).__name__}: {error}')

    def _redirect_state(self, root):
        root = Path(root)
        redirected = {}
        for module_name, attribute, relative in STATE_REDIRECTS:
            module = sys.modules.get(module_name)
            if module is not None and hasattr(module, attribute):
                setattr(module, attribute, root / relative)
                redirected[f'{module_name}.{attribute}'] = str(root / relative)
        real = _norm(DEFAULT_STATE_ROOT)
        for module_name in ('analysis_data', 'contribution_query_store', 'analysis_history',
                            'contribution_source_guard', 'analysis_scan', 'contribution_query',
                            'contribution_data', 'source_memo'):
            module = sys.modules.get(module_name)
            if module is None:
                continue
            for attribute, value in list(vars(module).items()):
                if not isinstance(value, Path) or (module_name, attribute) in READ_ONLY_STATE:
                    continue
                if f'{module_name}.{attribute}' in redirected:
                    continue
                text = _norm(value)
                if text.startswith(real):
                    relative = Path(os.path.relpath(str(value), str(DEFAULT_STATE_ROOT)))
                    setattr(module, attribute, root / relative)
                    redirected[f'{module_name}.{attribute}'] = str(root / relative)
        # A sandbox never writes (or reads) the live access ledger: access_stats points at scratch state.
        try:
            import access_stats
        except ImportError:
            access_stats = None
        if access_stats is not None and callable(getattr(access_stats, 'configure', None)):
            redirected['access_stats.PATH'] = str(access_stats.configure(root / 'prewarm' / 'access.sqlite'))
        self.redirected = redirected
        proofs = root / 'contribution-source-proofs'
        real_proofs = DEFAULT_STATE_ROOT / 'contribution-source-proofs'
        if not proofs.exists() and real_proofs.is_dir():
            proofs.mkdir(parents=True, exist_ok=True)
            for proof in real_proofs.glob('*.json'):
                with contextlib.suppress(OSError):
                    shutil.copy2(proof, proofs / proof.name)

    def _refuse_index_builds(self):
        data = self.data

        def refuse_build(source, token, destination):
            data._progress(token, status='error', failed_at=time.time(), message=INDEX_REFUSED)

        data._materialize = refuse_build

    # -- sources -------------------------------------------------------------------------------
    def sources(self):
        """Every published dataset: availability, source-guard verification, index token and presence."""
        data = self.data
        items = data._sources(self.report_directory)
        checks = {item['dataset']: item for item in self.guard.verify_sources(self.report_directory, strict=False)}
        result = []
        for item in items:
            entry = {'dataset': item['id'], 'available': bool(item.get('available')), 'verified': None,
                     'message': item.get('message'), 'source_fp': None, 'index_present': False}
            if entry['available']:
                check = checks.get(item['id'])
                entry['verified'] = bool(check and check.get('matched'))
                if check and not check.get('matched'):
                    entry['message'] = check.get('error')
                try:
                    token = data._token(item)
                except ValueError as error:
                    if classify(error) == 'code_changed':
                        raise Stop('code_changed', str(error)) from error
                    entry.update(available=False, message=str(error))
                else:
                    entry['source_fp'] = token
                    entry['index_present'] = (data.CACHE_ROOT / (token + '.sqlite')).exists()
            result.append(entry)
        return result

    def code_fingerprint(self):
        return code_fingerprint(getattr(self.data, 'RULE_PATHS', ()))

    # -- the console's own route code ---------------------------------------------------------------
    def _handler(self):
        app = types.SimpleNamespace(report_directory=self.report_directory, read_only=True)
        return types.SimpleNamespace(server=types.SimpleNamespace(app=app))

    def route_post(self, path, body):
        """server.py Handler.do_POST for the two analytical query routes (:2756-2769), through the
        handler's own scope_body() and contribution_result()."""
        data = json.loads(json.dumps(body))   # as json.loads(self.rfile.read(length))
        handler = self._handler()
        Handler = self.console.Handler
        if path == '/api/insights/query':
            entry = Handler.scope_body(handler, data)
            result = Handler.contribution_result(handler, self.analysis.query, data)
            result.update(self.industry_scope.echo(entry))
            return result
        if path == '/api/contributions/query':
            entry = Handler.scope_body(handler, data)
            result = Handler.contribution_result(handler, self.query.query, data)
            if isinstance(result, dict):
                result.update(self.industry_scope.echo(entry))
            return result
        raise ValueError('prewarm only replays the two analytical query routes.')

    def _console_stub(self):
        Console = self.console.Console

        class GraphConsole:
            published_reports = Console.published_reports
            graph_catalog = Console.graph_catalog
            report_path = Console.report_path
            graph = Console.graph
            find = Console.find

            def __init__(self, directory):
                self.report_directory = directory

        return GraphConsole(self.report_directory)

    def _graph_view(self, industry, query_string):
        """(echoed filters, company options) of Console.graph's answer to one graph.js /api/graph request,
        through the route glue (server.py:2625-2636, :2166 Console.graph). Memoized per request and the
        reports' file signature; the view itself is not kept."""
        stub = self._console_stub()
        reports = stub.published_reports()
        catalog = stub.graph_catalog('published-reports', reports)
        query = urllib.parse.parse_qs(query_string)   # do_GET's parse_qs (:2454)
        wanted = query.get('industry', [''])[0]
        registry = self.snapshot_registry.registry(self.report_directory) if wanted else None
        label = self.industry_scope.label_or_value(registry, wanted) if registry else wanted
        filters = {**{key: value[0] for key, value in query.items()}, 'industry': label}
        group = next((item for item in catalog['industries'] if item['name'] == label), None)
        if group is None:
            raise ValueError('Choose an industry with completed reports.')
        paths = [stub.report_path('published-reports', index) for index in group['indices']]
        signature = (tuple(str(path) for path in paths), self.view_cache.file_signature(paths), canonical(filters))
        memo = self._graph_memo.get((label, query_string))
        if memo and memo[0] == signature:
            return memo[1]
        view = stub.graph('published-reports', label, filters, registry)
        companies = (view.get('options') or {}).get('companies') or []
        answer = (view['filters'], [company for company in companies if isinstance(company, str) and company])
        self._graph_memo[(label, query_string)] = (signature, answer)
        return answer

    def graph_first_view_filters(self, industry):
        """The filters the /api/graph route echoes for graph.js's first load of `industry`, computed by
        Console.graph itself (server.py:2625-2636 route glue, :2166 Console.graph)."""
        return self._graph_view(industry, graph_first_load_query(industry))[0]

    def graph_company_bodies(self, industry):
        """(company, sparkline body) for each company of the first-load view's company select
        (view.options.companies, graph.js render()), each built from Console.graph's own answer to that
        company's view at the first-load cycle (graph_company_query)."""
        filters, companies = self._graph_view(industry, graph_first_load_query(industry))
        bodies = []
        for company in companies:
            echoed, _ = self._graph_view(industry, graph_company_query(industry, company, filters.get('cycle')))
            body = spark_request(industry, echoed)
            if body is not None:
                bodies.append((company, body))
        return bodies

    def release_graph_memory(self):
        """Graph views were only read to learn each first-load cycle; the warmer keeps none of them."""
        graph_data = sys.modules.get('graph_data')
        if graph_data is not None:
            with contextlib.suppress(Exception):
                graph_data._views.clear()
            for name in ('report_facts', '_report_activity', '_report_transfers'):
                with contextlib.suppress(Exception):
                    getattr(graph_data, name).cache_clear()
        insights = sys.modules.get('report_insights')
        if insights is not None:
            with contextlib.suppress(Exception):
                insights.embedded_receipts.cache_clear()

    def query_view_body(self, dataset):
        return spark_request(dataset, self.graph_first_view_filters(dataset))

    # -- warming -------------------------------------------------------------------------------
    def warm_query_view(self, body, deadline=None, should_stop=None, retry=False, sleep=time.sleep):
        """POST /api/contributions/query as graph.js sparkReady() does, until it is not preparing."""
        self.observer.reset()
        statuses, polls = [], 0
        while True:
            result = self.route_post('/api/contributions/query', body)
            status = result.get('status') if isinstance(result, dict) else None
            statuses.append(status)
            if status not in POLL_STATES:
                break
            # contribution_query.query answers 'preparing' only from data._ready(): the analytical index
            # is not on disk. Without --allow-index-builds nothing will build it, so do not wait.
            if not self.allow_index_builds:
                raise IndexMissing(INDEX_REFUSED)
            polls += 1
            if polls >= 120:
                raise TaskTimeout('Quarterly contribution data is still preparing after 120 polls.')
            if should_stop and should_stop():
                raise Stop('parent_gone', 'The warmer is stopping.')
            sleep(spark_poll_delay(result.get('retry_after_ms')))
        identity = self.observer.last('results')
        return {'status': status, 'rows': len(result.get('rows') or []) if isinstance(result, dict) else None,
                'key': self.store._key([self.store.LOADED_SHA, 'results', identity]) if identity else None,
                'response_path': str(self.observer.response_path('results', identity)) if identity else None,
                'polls': polls, 'statuses': _compress(statuses), 'as_of': None}

    def warm_insights(self, body, deadline=None, should_stop=None, retry=False, sleep=time.sleep):
        """POST /api/insights/query as insights.js queryReady() does: the first request carries retry
        when this is a retry (the client's Try again), later polls drop it (queryReady's nextQuery)."""
        self.observer.reset()
        if self.hints is not None:
            self.hints.reset()
        statuses, polls = [], 0
        request = {**body, 'retry': True} if retry else dict(body)
        while True:
            result = self.route_post('/api/insights/query', request)
            request = {key: value for key, value in request.items() if key != 'retry'}
            status = result.get('status')
            statuses.append(status)
            if status not in POLL_STATES:
                break
            if should_stop and should_stop():
                raise Stop('parent_gone', 'The warmer is stopping.')
            if deadline is not None and time.monotonic() > deadline:
                holders = self.building_locks_held_elsewhere()
                if holders:
                    raise LockHeld(f'Insights preparation is held by another process: {holders[:3]}')
                raise TaskTimeout('Insights preparation did not finish within 30 minutes.')
            sleep(insights_poll_delay(polls, result.get('retry_after_ms')))
            polls += 1
        request_key = self.insights_request_key()
        # The company select is filled from this result's options.companies (insights.js installOptions()).
        companies = (result.get('options') or {}).get('companies')
        self._drop_finished_jobs()
        return {'status': status, 'key': request_key,
                'response_path': str(self.insights_response_path(request_key)) if request_key else None,
                'as_of': (result.get('scope') or {}).get('as_of'), 'polls': polls, 'statuses': _compress(statuses),
                'companies': len(companies) if isinstance(companies, list) else None,
                'companies_list': [item for item in companies if isinstance(item, str)] if isinstance(companies, list) else None}

    def building_locks_held_elsewhere(self):
        """Live foreign holders of the console's cross-process Insights build markers, by the rule
        analysis_data itself applies (proclock.inspect: a running, non-recycled process)."""
        locate = getattr(self.analysis, 'building_dir', None)
        directory = Path(locate()) if callable(locate) else Path(self.analysis.ROOT) / 'building'
        holders = []
        for path in directory.glob('*.lock') if directory.is_dir() else ():
            found = proclock.inspect(path)
            if found is not None and found.live and found.info and found.pid != os.getpid():
                holders.append({'pid': found.pid, 'key': found.info.get('key'),
                                'started_utc': found.info.get('started_utc')})
        return holders

    def _drop_finished_jobs(self):
        # analysis_data keeps up to 12 finished results in memory for the console's polls; the warmer
        # asks once per scope, so it releases them (memory only; the disk cache is the product).
        analysis = self.analysis
        with contextlib.suppress(Exception):
            with analysis._lock:
                for key in [key for key, job in analysis._jobs.items() if job.done()]:
                    del analysis._jobs[key]

    def warm_view_only(self, dataset):
        """What the sparkline query builds before its response (contribution_query._query_uncached ->
        store.view): the query projection, after the route's strict source check. No response is stored,
        because the response cache has no room for a warm entry."""
        self.guard.verify_sources(self.report_directory, dataset)   # Handler.contribution_result's check
        source = self.data._source(self.report_directory, {'dataset': dataset})
        path, pending = self.data._ready(source)
        if pending:
            if not self.allow_index_builds:
                raise IndexMissing(INDEX_REFUSED)
            path, _ = self.data._ready(source, wait=True)
        view = self.store.view(path)
        return {'status': 'ready', 'rows': None, 'key': None, 'response_path': None, 'polls': 0,
                'statuses': ['projection only'], 'as_of': None, 'view_path': str(view)}

    def warm(self, task, deadline=None, should_stop=None, retry=False, sleep=time.sleep):
        writes = getattr(self, 'writes', None)
        if writes is not None:
            writes.reset()
        if task['kind'] == 'query_view' and task.get('view_only'):
            info = self.warm_view_only(task['dataset'])
        elif task['kind'] in ARTIFACT_KINDS:
            info = getattr(self, 'warm_' + task['kind'])(task['dataset'])
        elif task['kind'] in QUERY_KINDS:
            info = self.warm_query_view(task['body'], deadline, should_stop, retry, sleep)
        else:
            info = self.warm_insights(task['body'], deadline, should_stop, retry, sleep)
        info['written'] = writes.responses() if writes is not None else []
        return info

    # -- the response-cache budget (module docstring) -------------------------------------------------
    def response_dir(self, namespace):
        return Path(self.store.ROOT) / 'responses' / namespace

    def response_capacity(self, namespace, today):
        # The store's own prune limits (RESPONSE_KEEP_* once builder "value"'s raised cap is live; the
        # 128-file / 128 MB defaults before it).
        return warm_capacity(response_files(self.response_dir(namespace)), today,
                             keep_files=getattr(self.store, 'RESPONSE_KEEP_FILES', PRUNE_KEEP_FILES),
                             keep_bytes=getattr(self.store, 'RESPONSE_KEEP_BYTES', PRUNE_KEEP_BYTES))

    def response_room(self, kind, today):
        group = response_group(kind)
        if group is None:
            return None
        return min(self.response_capacity(namespace, today) for namespace in RESPONSE_NAMESPACES[group])

    # -- the other builders' caches (their receipts' hooks) ---------------------------------------
    def artifact_hooks(self):
        """The ARTIFACT_KINDS whose hook is installed in this checkout."""
        found = []
        with contextlib.suppress(Exception):
            import comparison_data
            import response_cache  # noqa: F401 - flows_entry stores through it
            if callable(getattr(comparison_data, 'flows_entry', None)):
                found.append('comparison_flows')
        with contextlib.suppress(Exception):
            import drawer_sidecars
            if callable(getattr(drawer_sidecars, 'build_sidecars', None)):
                found.append('drawer_sidecars')
        with contextlib.suppress(Exception):
            import report_insights
            if callable(getattr(report_insights, 'worksheet_entry', None)):
                found.append('worksheets')
        return tuple(found)

    def _configure_caches(self):
        """Root the other builders' disk caches in this run's state root, where the console roots them
        (cache receipt section 8: response_cache.configure(); the drawer sidecars' state root)."""
        with contextlib.suppress(ImportError):
            import response_cache
            response_cache.configure(self.state_root / 'response-cache')
            self.notes.append('response_cache disk tier: ' + str(self.state_root / 'response-cache'))
        with contextlib.suppress(ImportError):
            import drawer_sidecars
            drawer_sidecars.configure(state_root=self.state_root / 'drawer-sidecars')

    def _industry_reports(self, industry):
        """(label, report paths) exactly as Console.comparison selects them for the route's label."""
        stub = self._console_stub()
        registry = self.snapshot_registry.registry(self.report_directory)
        label = self.industry_scope.label_or_value(registry, industry) if registry else industry
        catalog = stub.graph_catalog('published-reports', stub.published_reports())
        group = next((item for item in catalog['industries'] if item['name'] == label), None)
        if group is None:
            raise ValueError('Choose an industry with completed reports.')
        return label, [stub.report_path('published-reports', index) for index in group['indices']]

    def warm_comparison_flows(self, industry):
        """comparison_data.flows_entry (the cache receipt's hook, the body of warm_flows()): one industry's
        /api/comparison-flows answer, built once and stored in the response cache's disk tier."""
        import comparison_data
        import response_cache
        label, paths = self._industry_reports(industry)
        try:
            entry = comparison_data.flows_entry(paths, label)
        finally:
            self.release_graph_memory()
        root = getattr(response_cache, 'ROOT', None)
        meta = Path(root) / entry.namespace / (entry.key + '.json') if root is not None else None
        return {'status': 'ready', 'key': entry.key, 'polls': 0, 'statuses': [entry.hit], 'as_of': None,
                'response_path': str(meta) if meta is not None and meta.is_file() else None}

    def warm_worksheets(self, industry):
        """report_insights.worksheet_entry (the /api/report-data route's cache): the sheet list and every sheet
        of each of this industry's published workbooks, so Chart Studio's pick of a sheet is a cache hit. A
        sheet the route refuses (WorksheetRefused) is stored as its refusal and skipped."""
        import report_insights
        stub = self._console_stub()
        registry = self.snapshot_registry.registry(self.report_directory)
        label = self.industry_scope.label_or_value(registry, industry) if registry else industry
        sheets = refused = 0
        for row in stub.published_reports():
            if row['industry'] != label:
                continue
            path = stub.report_path('published-reports', row['index'])
            for sheet in json.loads(report_insights.worksheet_entry(path).body)['sheets']:
                try:
                    report_insights.worksheet_entry(path, sheet)
                    sheets += 1
                except report_insights.WorksheetRefused:
                    refused += 1
        return {'status': 'ready', 'key': None, 'polls': 0, 'statuses': ['warm'], 'as_of': None,
                'response_path': None, 'sheets': sheets, 'refused': refused}

    def warm_drawer_sidecars(self, industry):
        """drawer_sidecars.build_sidecars (the drawer receipt's hook): every sidecar a Graph drawer reads for
        one industry, through the click's own entry points; graph_data's caches are released afterwards."""
        import drawer_sidecars
        summary = drawer_sidecars.build_sidecars(industry, report_directory=self.report_directory, release=True)
        if not summary.get('ok'):
            failed = [f"{step['step']}: {step['error']}" for step in summary.get('steps', []) if step.get('error')]
            raise RuntimeError('Drawer sidecars incomplete: ' + ('; '.join(failed) or summary.get('error') or 'unknown'))
        return {'status': 'ready', 'key': None, 'polls': 0, 'statuses': ['built'], 'as_of': None,
                'response_path': str(drawer_sidecars.state_root()), 'sidecars': summary.get('sidecars')}

    def demote_written(self, paths, day, position):
        return demote(paths, day, position)

    def promote_hinted(self, hints):
        entries = [(self.insights_response_path(hint['key']), hint.get('seen_utc'))
                   for hint in hints if hint.get('key')]
        return promote([(path, seen) for path, seen in entries if path is not None])


def _compress(statuses):
    result = []
    for status in statuses:
        if result and result[-1][0] == status:
            result[-1][1] += 1
        else:
            result.append([status, 1])
    return [f'{status} x{count}' if count > 1 else str(status) for status, count in result]


def default_data_root():
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Mentat' / 'console'   # server.py:2891


# ----------------------------------------------------------------------------- console probe
def console_health(console_url, timeout=5.0):
    """GET /api/health: read-only, answers `busy` from Console.busy() (collection work; D-029 uses the
    same flag to refuse a restart). Returns (busy True/False, detail), or (None, error) when the
    console cannot answer; the caller treats an unanswered probe as busy."""
    try:
        parsed = urllib.parse.urlparse(console_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    except (ValueError, TypeError) as error:
        return None, str(error)
    try:
        connection.request('GET', '/api/health', headers={'Host': f'{parsed.hostname}:{parsed.port}'})
        response = connection.getresponse()
        raw = response.read()
        if response.status != 200:
            return None, f'HTTP {response.status}'
        value = json.loads(raw.decode('utf-8'))
        return bool(value.get('busy')), {'pid': value.get('pid'), 'read_only': value.get('read_only')}
    except (OSError, ValueError, http.client.HTTPException) as error:
        return None, f'{type(error).__name__}: {error}'
    finally:
        connection.close()


# ----------------------------------------------------------------------------- the warmer loop
class Warmer:
    """One task at a time, below-normal priority, guarded, re-planned on date/hints/source changes."""

    def __init__(self, backend, state, *, op='run', run_id=None, parent_pid=None, console=None,
                 once=False, max_tasks=None, start_delay=0.0, allow_index_builds=False, industries=None,
                 max_company_neighbors=DEFAULT_MAX_COMPANY_NEIGHBORS, today=local_today, monotonic=time.monotonic,
                 sleep=time.sleep, free_bytes=free_physical_bytes, console_probe=console_health,
                 parent_alive=None, code_stamps=watched_code_stamps, emitter=emit, set_priority=True,
                 access_top=None):
        self.backend, self.state, self.op = backend, state.ensure(), op
        # access_stats.top over the request kinds (warmer_access_top) for the value ranking; None keeps the
        # hints-only order.
        self.access_top = access_top
        self.ledger = Ledger(state)
        self.run_id = run_id or f'prewarm-{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}-{os.getpid()}'
        self.parent_pid, self.console = parent_pid, console
        self.once, self.max_tasks, self.start_delay = once, max_tasks, start_delay
        self.allow_index_builds, self.industries = allow_index_builds, industries
        self.max_company_neighbors = max_company_neighbors
        self.today, self.monotonic, self.sleep = today, monotonic, sleep
        self.free_bytes, self.console_probe, self.code_stamps = free_bytes, console_probe, code_stamps
        self.emitter, self.set_priority = emitter, set_priority
        self.parent_started = process_started(parent_pid) if parent_pid else None
        self._parent_alive = parent_alive or (lambda: pid_alive(self.parent_pid, self.parent_started))
        self.start_stamps = code_stamps()
        self.runtime = {}
        self.plan = None
        self.last_free = None
        self._options = None
        self._last_sources_sig = None
        self.last_source_check = None
        self.last_hints_check = None
        self.paused = None
        self.executed = 0
        self.succeeded = 0
        self.failed = 0
        self.skipped = 0
        self.quarantined_now = []
        self.stop_reason = None
        self.last_promotion = None
        self.current = None                  # (task, monotonic start) of the task running now
        self._ending = threading.Lock()      # the thread that ends the run holds it (_watch_parent)
        self._run_over = False

    # -- helpers --------------------------------------------------------------------------------
    def _emit(self, kind, status, summary, **fields):
        return self.emitter(kind, status, summary, run_id=self.run_id, **fields)

    def parent_ok(self):
        return self.parent_pid is None or bool(self._parent_alive())

    def code_ok(self):
        return self.code_stamps() == self.start_stamps

    def check_stop(self):
        if not self.parent_ok():
            raise Stop('parent_gone', f'Parent console pid {self.parent_pid} is gone.')
        if not self.code_ok():
            changed = [name for name, value in self.code_stamps().items() if self.start_stamps.get(name) != value]
            raise Stop('code_changed', 'Watched code changed on disk: ' + ', '.join(changed))

    def nap(self, seconds):
        """Sleep in short slices, re-checking the stop guards."""
        deadline = self.monotonic() + seconds
        while True:
            self.check_stop()
            left = deadline - self.monotonic()
            if left <= 0:
                return
            self.sleep(min(5.0, left))

    def load_quarantine(self):
        value = read_json(self.state.quarantine, {})
        return value if isinstance(value, dict) else {}

    def save_quarantine(self, quarantine):
        atomic_write_json(self.state.quarantine, quarantine)

    def options_cache(self):
        """dataset -> basis -> {source_fp, companies}: the company options each warmed result carried."""
        if self._options is None:
            cached = read_json(self.state.options, {})
            self._options = cached if isinstance(cached, dict) else {}
        return self._options

    def remember_options(self, task, companies):
        basis = task['body'].get('basis') or 'receipt'
        self.options_cache().setdefault(task['dataset'], {})[basis] = {'source_fp': task['source_fp'],
                                                                      'companies': companies}
        atomic_write_json(self.state.options, self._options)

    def options_known(self, dataset, basis, source_fp):
        entry = (self.options_cache().get(dataset) or {}).get(basis) or {}
        return entry.get('source_fp') == source_fp and isinstance(entry.get('companies'), list)

    # -- planning -------------------------------------------------------------------------------
    def replan(self, reason):
        hints = read_hints(self.state.hints)
        rows = self.ledger.read()
        code_fp = self.backend.code_fingerprint()
        access_rows = None
        if callable(self.access_top):
            try:
                access_rows = self.access_top()
            except Exception as error:   # the ranking falls back to hints only
                log(f'access ranking unavailable: {type(error).__name__}: {error}')
        # RACF 3.6: the industries whose source fingerprint changed re-prime their hot items first.
        reprime = ()
        if reason == 'source_changed' and self._last_sources_sig:
            with contextlib.suppress(Exception):
                now = sources_signature(self.backend.sources())
                reprime = tuple(sorted(name for name, value in now.items() if self._last_sources_sig.get(name) != value))
        plan = build_plan(self.backend, hints=hints, today=self.today(), code_fp=code_fp,
                          industries=self.industries, allow_index_builds=self.allow_index_builds,
                          max_company_neighbors=self.max_company_neighbors, options_cache=self.options_cache(),
                          successes=last_successes(rows), quarantine=self.load_quarantine(),
                          hints_file_stamp=hints_stamp(self.state.hints), access_rows=access_rows, reprime=reprime)
        with contextlib.suppress(Exception):
            self.backend.release_graph_memory()
        # The ledger is the record of every attempt, this process's and earlier ones'.
        failures = consecutive_failures(rows)
        for task in plan['tasks']:
            runtime = self.runtime.setdefault(task['id'], {'attempts': 0, 'not_before': 0.0, 'consecutive': 0})
            count, source_fp, task_code = failures.get(task['id'], (0, None, None))
            runtime['consecutive'] = count if (source_fp, task_code) == (task['source_fp'], code_fp) else 0
        if plan['released_quarantine']:
            self.save_quarantine(plan['quarantine'])
        plan['reason'] = reason
        plan['run_id'] = self.run_id
        self.plan = plan
        self._last_sources_sig = {name: [value.get('available'), value.get('source_fp'), value.get('index_present')]
                                  for name, value in plan['sources'].items()}
        self.last_source_check = self.monotonic()
        self.last_hints_check = self.monotonic()
        atomic_write_json(self.state.plan, plan)
        counts = plan_counts(plan)
        pending = sum(1 for task in plan['tasks'] if task['state'] == 'pending')
        log(f'plan ({reason}): {len(plan["tasks"])} tasks, {pending} pending; {json.dumps(counts)}')
        self._emit('mentat.prewarm.plan', 'info', f'Plan ({reason}): {len(plan["tasks"])} tasks, {pending} to warm',
                   stage='plan', total=len(plan['tasks']),
                   metadata={'counts': counts, 'order': plan['order'], 'skipped': len(plan['skipped']),
                             'budget': plan.get('budget'), 'pending_companies': plan.get('pending_companies')})
        return plan

    def replan_trigger(self):
        plan = self.plan
        if plan is None:
            return 'start'
        if self.today() != plan['as_of']:
            return 'date_changed'
        now = self.monotonic()
        if hints_stamp(self.state.hints) != plan.get('hints_stamp') and \
                (self.last_hints_check is None or now - self.last_hints_check >= HINTS_DEBOUNCE_SECONDS):
            self.last_hints_check = now
            hints = read_hints(self.state.hints)
            newest = hints[0] if hints else None
            current = plan.get('newest_hint')
            order = industry_order(plan['order'], hints)
            if (canonical(newest['body']) if newest else None) != (canonical(current['body']) if current else None) \
                    or order != plan['order']:
                return 'hints_changed'
            plan['hints_stamp'] = hints_stamp(self.state.hints)
        expansion = plan.get('pending_expansion')
        if expansion and self.options_known(expansion['dataset'], expansion['basis'],
                                            plan['sources'].get(expansion['dataset'], {}).get('source_fp')):
            return 'neighbors_ready'
        if any(self.options_known(dataset, 'receipt', plan['sources'].get(dataset, {}).get('source_fp'))
               for dataset in plan.get('pending_companies') or []):
            return 'companies_ready'
        return None

    def sources_changed(self):
        self.last_source_check = self.monotonic()
        sources = self.backend.sources()
        return sources_signature(sources) != self._last_sources_sig or \
            self.backend.code_fingerprint() != self.plan['code_fp']

    def _sources_changed_safe(self):
        try:
            return self.sources_changed()
        except Stop:
            raise
        except Exception as error:
            if classify(error) == 'code_changed':
                raise Stop('code_changed', str(error)) from error
            log(f'source check failed ({type(error).__name__}: {error}); re-planning')
            return True

    def next_task(self):
        now = self.monotonic()
        for task in self.plan['tasks']:
            if task['state'] != 'pending':
                continue
            if self.runtime.get(task['id'], {}).get('not_before', 0.0) <= now:
                return task
        return None

    def pending_later(self):
        now = self.monotonic()
        waits = [self.runtime[task['id']]['not_before'] - now for task in self.plan['tasks']
                 if task['state'] == 'pending' and self.runtime.get(task['id'], {}).get('not_before', 0.0) > now]
        return min(waits) if waits else None

    # -- guards ---------------------------------------------------------------------------------
    def resource_guard(self, task=None):
        """(root_cause_hint, detail) when a task must not start now; None when it may."""
        try:
            free = self.free_bytes()
        except Exception as error:
            self.last_free = None
            return 'low_memory', f'memory probe failed: {error}'
        self.last_free = free
        if free < MIN_FREE_BYTES:
            return 'low_memory', f'{free / 1024 ** 2:.0f} MB free physical memory < {MIN_FREE_BYTES / 1024 ** 2:.0f} MB'
        if task is not None and task.get('kind') in ARTIFACT_KINDS and free < MIN_FREE_BYTES + ARTIFACT_EXTRA_FREE_BYTES:
            return 'low_memory', (f'{free / 1024 ** 2:.0f} MB free physical memory < '
                                  f'{(MIN_FREE_BYTES + ARTIFACT_EXTRA_FREE_BYTES) / 1024 ** 2:.0f} MB for {task["kind"]}')
        if self.console:
            busy, detail = self.console_probe(self.console)
            if busy is None:
                return 'console_busy', f'console did not answer /api/health ({detail}); treated as busy'
            if busy:
                return 'console_busy', 'console reports busy (collection work in progress)'
        return None

    # -- observations ---------------------------------------------------------------------------
    def observe(self, task, outcome, *, seconds=None, hint=None, retry=None, stop=None, error=None, info=None,
                consecutive=None, extra=None):
        runtime = self.runtime.get(task['id'], {}) if task else {}
        observation = {
            'schema': SCHEMA, 'ts_utc': utc_iso(), 'run_id': self.run_id, 'pid': os.getpid(), 'op': self.op,
            'task_id': task['id'] if task else None, 'kind': task['kind'] if task else None,
            'dataset': task['dataset'] if task else None, 'outcome': outcome,
            'attempt': runtime.get('attempts'), 'consecutive_failures': consecutive if consecutive is not None
            else runtime.get('consecutive'), 'seconds': round(seconds, 3) if seconds is not None else None,
            'root_cause_hint': hint, 'retry': retry, 'stop': stop, 'error': error,
            'key': (info or {}).get('key'), 'response_path': (info or {}).get('response_path'),
            'as_of': (info or {}).get('as_of'), 'source_fp': task.get('source_fp') if task else None,
            'code_fp': self.plan['code_fp'] if self.plan else None,
            'polls': (info or {}).get('polls'), 'statuses': (info or {}).get('statuses'),
            # The free memory the last resource guard measured (the observation does not probe again).
            'free_mb': round(self.last_free / 1024 ** 2) if self.last_free is not None else None,
            'detail': extra or {}}
        if info and info.get('companies') is not None:
            observation['detail']['companies'] = info['companies']
        self.ledger.append(observation)
        return observation

    def heartbeat(self, **values):
        with contextlib.suppress(Exception):
            atomic_write_json(self.state.heartbeat, {'pid': os.getpid(), 'run_id': self.run_id, 'op': self.op,
                                                     'updated_utc': utc_iso(), **values})

    # -- the response-cache budget --------------------------------------------------------------
    def room_for(self, task):
        """How many responses the task's namespaces still take today; None without budget information
        (a backend that stores nothing), and for a query view already reduced to its projection."""
        room = getattr(self.backend, 'response_room', None)
        if not callable(room) or (task['kind'] == 'query_view' and task.get('view_only')):
            return None
        try:
            return room(task['kind'], self.today())
        except Exception as error:   # an unreadable namespace is treated as full: never push anything out
            log(f'response budget check failed ({type(error).__name__}: {error}); treated as no room')
            return 0

    def over_budget(self, task):
        """True (after recording it) when the task must not write a response now. A query view keeps its
        projection warm and only drops the response."""
        room = self.room_for(task)
        if room is None or room >= 1:
            return False
        if task['kind'] == 'query_view':
            task['view_only'] = True
            return False
        task['state'] = 'over_budget'
        task['root_cause_hint'] = 'cache_budget'
        retry, stop = recovery('cache_budget')
        self.skipped += 1
        self.observe(task, 'skipped', hint='cache_budget', retry=retry, stop=stop,
                     error='The response cache has no room for another warm entry today.')
        self._emit('mentat.prewarm.task', 'skipped', f'Over budget: {task["kind"]} {task["dataset"]}',
                   stage=task['kind'], metadata={'task_id': task['id'], 'root_cause_hint': 'cache_budget'})
        return True

    def promote_hints(self):
        """Hints name the responses clicks were answered with: a demoted warm entry among them is promoted
        to the click's time, so it is kept like a response the click stored itself."""
        promote_hinted = getattr(self.backend, 'promote_hinted', None)
        now = self.monotonic()
        if not callable(promote_hinted) or (self.last_promotion is not None and now - self.last_promotion < IDLE_SECONDS):
            return []
        self.last_promotion = now
        try:
            moved = promote_hinted(read_hints(self.state.hints))
        except Exception as error:
            log(f'promotion failed: {type(error).__name__}: {error}')
            return []
        if moved:
            log(f'promoted {len(moved)} warm responses a click has used')
            self._emit('mentat.prewarm.budget', 'info', f'Promoted {len(moved)} warm responses a click used',
                       stage='budget', metadata={'promoted': len(moved)})
        return moved

    # -- one task -------------------------------------------------------------------------------
    def execute(self, task):
        if self.over_budget(task):
            return 'skipped'
        runtime = self.runtime.setdefault(task['id'], {'attempts': 0, 'not_before': 0.0, 'consecutive': 0})
        runtime['attempts'] += 1
        position = self.plan['tasks'].index(task) + 1 if task in self.plan['tasks'] else -1
        total = len(self.plan['tasks'])
        summary = f'{task["kind"]} {task["dataset"]} ({task["reason"]})'
        self._emit('mentat.prewarm.task', 'running', 'Start ' + summary, stage=task['kind'], current=position,
                   total=total, metadata={'task_id': task['id'], 'attempt': runtime['attempts']})
        self.heartbeat(current_task=task['id'], started_utc=utc_iso())
        started = self.monotonic()
        retry_request = runtime['consecutive'] > 0
        self.current = (task, started)
        try:
            info = self.backend.warm(task, deadline=time.monotonic() + TASK_TIMEOUT_SECONDS,
                                     should_stop=lambda: not self.parent_ok(), retry=retry_request, sleep=self.sleep)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as error:
            seconds = self.monotonic() - started
            hint = classify(error)
            if hint in ('code_changed', 'parent_gone'):
                self.observe(task, 'failed', seconds=seconds, hint=hint, retry=recovery(hint)[0], stop=recovery(hint)[1],
                             error=f'{type(error).__name__}: {error}', consecutive=runtime['consecutive'])
                raise Stop(hint, str(error)) from error
            if hint == 'index_missing':
                task['state'] = 'blocked'
                task['root_cause_hint'] = hint
                retry, stop = recovery(hint)
                self.observe(task, 'failed', seconds=seconds, hint=hint, retry=retry, stop=stop,
                             error=f'{type(error).__name__}: {error}', consecutive=runtime['consecutive'])
                self.failed += 1
                self._emit('mentat.prewarm.task', 'blocked', 'Blocked ' + summary + ': analytical index missing',
                           stage=task['kind'], current=position, total=total, metadata={'task_id': task['id']})
                return 'failed'
            runtime['consecutive'] += 1
            retry, stop = recovery(hint, runtime['consecutive'])
            detail = {'traceback': traceback.format_exc(limit=6)[-1500:]}
            self.observe(task, 'failed', seconds=seconds, hint=hint, retry=retry, stop=stop,
                         error=f'{type(error).__name__}: {error}', extra=detail)
            self.failed += 1
            self._emit('mentat.prewarm.task', 'failed', f'Failed {summary}: {hint}', stage=task['kind'],
                       current=position, total=total, detail=f'{type(error).__name__}: {error}',
                       metadata={'task_id': task['id'], 'root_cause_hint': hint, 'retry': retry})
            if runtime['consecutive'] >= MAX_CONSECUTIVE_FAILURES:
                self.quarantine(task, hint, f'{type(error).__name__}: {error}')
            else:
                delay = retry_delay(retry)
                runtime['not_before'] = self.monotonic() + (delay if delay is not None else PAUSE_SECONDS)
            return 'failed'
        finally:
            self.current = None
        seconds = self.monotonic() - started
        runtime['consecutive'] = 0
        task['state'] = 'warm'
        self.succeeded += 1
        extra = {}
        written = info.get('written') or []
        if written:
            extra['written'] = len(written)
            demote_written = getattr(self.backend, 'demote_written', None)
            if callable(demote_written):
                try:
                    extra['demoted'] = len(demote_written(written, self.today(), max(0, position - 1)))
                except Exception as error:   # the entry then counts as a click's: the budget only shrinks
                    extra['demote_error'] = f'{type(error).__name__}: {error}'
        if info.get('view_path'):
            extra['view_path'] = info['view_path']
        self.observe(task, 'succeeded', seconds=seconds, info=info, consecutive=0, extra=extra)
        self._emit('mentat.prewarm.task', 'completed', f'Warm {summary} in {seconds:.1f} s', stage=task['kind'],
                   current=position, total=total, duration_ms=int(seconds * 1000),
                   metadata={'task_id': task['id'], 'key': info.get('key'), 'polls': info.get('polls')})
        if isinstance(info.get('companies_list'), list):
            self.remember_options(task, info['companies_list'])
        return 'succeeded'

    def quarantine(self, task, hint, error):
        quarantine = self.load_quarantine()
        quarantine[task['id']] = {'source_fp': task['source_fp'], 'code_fp': self.plan['code_fp'],
                                  'since_utc': utc_iso(), 'failures': self.runtime[task['id']]['consecutive'],
                                  'root_cause_hint': hint, 'last_error': error, 'kind': task['kind'],
                                  'dataset': task['dataset']}
        self.save_quarantine(quarantine)
        task['state'] = 'quarantined'
        self.quarantined_now.append(task['id'])
        self.observe(task, 'quarantined', hint=hint, retry='do not retry until the source fingerprint or code changes',
                     stop=f'quarantined after {MAX_CONSECUTIVE_FAILURES} consecutive failures', error=error)
        self._emit('mentat.prewarm.quarantine', 'blocked', f'Quarantined {task["kind"]} {task["dataset"]}: {hint}',
                   stage=task['kind'], detail=error, metadata={'task_id': task['id']})

    def pause(self, task, hint, detail):
        retry, stop = recovery(hint)
        if self.paused != (hint, task['id']):
            self.paused = (hint, task['id'])
            self.pause_started = self.monotonic()
            self.observe(task, 'paused', hint=hint, retry=retry, stop=stop, error=detail)
            self._emit('mentat.prewarm.pause', 'waiting', f'Paused before {task["kind"]} {task["dataset"]}: {detail}',
                       stage='guard', metadata={'root_cause_hint': hint, 'task_id': task['id']})
            log(f'pause: {hint}: {detail}')
        self.heartbeat(paused=hint, detail=detail, next_task=task['id'])
        self.nap(PAUSE_SECONDS)

    def resume_if_paused(self, task):
        if self.paused:
            waited = self.monotonic() - getattr(self, 'pause_started', self.monotonic())
            self.observe(task, 'resumed', seconds=waited, hint=self.paused[0],
                         extra={'paused_for': self.paused[0]})
            self._emit('mentat.prewarm.pause', 'running', f'Resumed after {waited:.0f} s ({self.paused[0]})',
                       stage='guard', duration_ms=int(waited * 1000))
            self.paused = None

    # -- the loop -------------------------------------------------------------------------------
    def run(self):
        lock = WarmerLock(self.state.lock)
        info = {'pid': os.getpid(), 'pid_started': process_started(os.getpid()), 'started_utc': utc_iso(),
                'parent_pid': self.parent_pid, 'parent_started': self.parent_started,
                'console': self.console, 'run_id': self.run_id, 'op': self.op,
                'data_root': str(getattr(self.backend, 'data_root', '')),
                'state_root': str(self.state.state_root)}
        acquired, holder = lock.acquire(info)
        waited_for, grace_until = None, None
        # A holder whose own console is gone is an orphan on its way out (it exits after its current
        # task: a console restart leaves the old warmer finishing a build). Wait for it rather than
        # leave the new console with no warmer; once it has died, Windows releases its lock a moment
        # later, so a dead holder gets a short grace. A holder whose console is alive is a real peer.
        while not acquired:
            if self._orphaned(holder):
                waited_for, grace_until = (holder or {}).get('pid'), None
            elif waited_for and not pid_alive((holder or {}).get('pid'), (holder or {}).get('pid_started')):
                grace_until = grace_until or self.monotonic() + LOCK_RELEASE_GRACE_SECONDS
                if self.monotonic() > grace_until:
                    break
            else:
                break
            try:
                self.nap(1.0)
            except Stop as stop:
                return op_result('success', f'Stopped while waiting for orphaned warmer pid {waited_for}: {stop}',
                                 metrics={'root_cause_hint': stop.root_cause, 'lock_holder': holder})
            acquired, holder = lock.acquire(info)
        if not acquired:
            return op_result('warning', f'Another warmer holds the lock (pid {(holder or {}).get("pid")}); exiting.',
                             metrics={'root_cause_hint': 'lock_held_by_other_process', 'lock_holder': holder},
                             artifacts=[str(self.state.lock)])
        if waited_for:
            log(f'acquired the warmer lock after orphaned warmer pid {waited_for} exited')
        try:
            if holder:
                log(f'took over a stale warmer lock from pid {holder.get("pid")}')
            scheduling = None
            if self.set_priority:
                try:
                    scheduling = set_below_normal_priority()
                    # Its native scanners inherit the priority but not the EcoQoS opt-out (review round 2).
                    scheduling['native_scanners'] = unthrottle_native_scanners()
                except Exception as error:   # the warmer runs at whatever it was given
                    scheduling = {'error': f'{type(error).__name__}: {error}'}
            self._emit('mentat.prewarm.run', 'running', f'Warmer started (pid {os.getpid()})', stage='start',
                       metadata={'parent_pid': self.parent_pid, 'console': self.console,
                                 'state_root': str(self.state.state_root), 'stale_lock_from': (holder or {}).get('pid'),
                                 'scheduling': scheduling})
            if _CLI_RUN is not None and self.parent_pid:
                threading.Thread(target=self._watch_parent, args=(lock,), name='mentat-prewarm-parent-watch',
                                 daemon=True).start()
            store = getattr(self.backend, 'store', None)
            if self.op == 'run' and getattr(store, 'ROOT', None) is not None and callable(getattr(store, '_lock', None)):
                threading.Thread(target=self._sweep_loop, args=(store,), name='mentat-prewarm-pending-sweep',
                                 daemon=True).start()
            self.heartbeat(state='starting')
            return self._loop()
        except Stop as stop:
            return self._exit(stop.root_cause, str(stop))
        except Exception as error:
            self._emit('mentat.prewarm.run', 'failed', f'Warmer crashed: {type(error).__name__}: {error}',
                       stage='exit', detail=traceback.format_exc(limit=8)[-1800:])
            return op_result('error', f'Warmer stopped on {type(error).__name__}: {error}',
                          next_actions=['read the ledger and warmer log', 'rerun: prewarm.py status'],
                          metrics=self._metrics({'root_cause_hint': classify(error)}),
                          artifacts=[str(self.state.ledger)])
        finally:
            with self._ending:   # once the watchdog is ending the process it holds this until os._exit
                self._run_over = True
            lock.release()
            self.heartbeat(state='stopped')

    def _sweep_loop(self, store):
        """sweep_pending at the start of a run and every PENDING_SWEEP_SECONDS while it runs, on a daemon
        thread: a live build of a key makes it wait, and the run never waits for it. Only a `run` warmer
        sweeps, after it holds the warmer lock, so one process per state root does it; the console never does."""
        while True:   # the first pass runs even when the run itself ends first
            try:
                result = sweep_pending(store)
                if result['removed'] or result['errors']:
                    summary = (f'Removed {len(result["removed"])} projection file(s) left by an ended build '
                               f'({result["bytes"] / 1024 ** 2:.0f} MB)')
                    log(summary + (f'; {len(result["errors"])} error(s): {result["errors"][:3]}' if result['errors'] else ''))
                    self._emit('mentat.prewarm.sweep', 'failed' if result['errors'] else 'completed', summary,
                               stage='sweep', metadata={'removed': result['removed'][:20], 'bytes': result['bytes'],
                                                        'errors': result['errors'][:10]})
            except Exception as error:   # the sweep never ends the warmer; the next pass retries
                log(f'pending sweep failed: {type(error).__name__}: {error}')
            if self._run_over:
                return
            end = time.monotonic() + PENDING_SWEEP_SECONDS
            while not self._run_over and time.monotonic() < end:
                time.sleep(min(5.0, PENDING_SWEEP_SECONDS))

    def _watch_parent(self, lock):
        """End an orphaned warmer within PARENT_WATCH_SECONDS, whatever it is doing (review round 1 of
        batch 1, after H3). The loop checks its console only between steps (nap, check_stop) and while a
        task polls (should_stop), so a synchronous step kept an orphan running until the step ended: the
        verifier's kills measured 38.2 s during a query view computed in-line and 14.8 s during planning;
        a comparison-flows or drawer build never checks at all. This daemon thread checks the console on
        its own clock and, once it is gone, ends the process from here (_end_orphaned). Only the CLI
        warmer runs it (main(hard_exit=True) sets _CLI_RUN); an in-process caller is never ended."""
        while True:
            time.sleep(PARENT_WATCH_SECONDS)
            if self._run_over or self.stop_reason is not None:
                return   # the run is ending by itself
            try:
                if self.parent_ok():
                    continue
            except Exception:   # a failed probe is no verdict; the loop's own guards still run
                continue
            self._ending.acquire()   # never released: the process ends in _end_orphaned
            if self._run_over or self.stop_reason is not None:
                self._ending.release()
                return
            try:
                self._end_orphaned(lock)
            except BaseException:   # the console is gone: the process ends even if recording its exit failed
                os._exit(EXIT_CODES['success'])
            return

    def _end_orphaned(self, lock):
        """The watchdog's exit, as the loop's own parent_gone exit ends: the interrupted task is recorded
        (a parent_gone failure, which never counts toward quarantine), the exit is emitted and logged
        (_exit), the warmer lock is released with released_utc, the op's one JSON report is printed, and
        exit_now releases every proclock lock and calls os._exit(0). The ledger's lock is held to the end,
        so no ledger line is cut in half. Files are written by os.replace and SQLite rolls back an open
        write transaction, so a step cut short leaves nothing half-written that is ever served."""
        message = f'Parent console pid {self.parent_pid} is gone.'
        current = self.current
        try:
            if current is not None:
                task, started = current
                retry, stop = recovery('parent_gone')
                self.observe(task, 'failed', seconds=self.monotonic() - started, hint='parent_gone', retry=retry,
                             stop=stop, error=f'Stop: {message} The warmer ended during this task.',
                             consecutive=self.runtime.get(task['id'], {}).get('consecutive'))
                message += f' The warmer ended during {task["id"]}.'
            report = self._exit('parent_gone', message)
        except Exception as error:   # the process ends all the same
            report = op_result('success', f'Warmer exited: {message}',
                               metrics={'root_cause_hint': 'parent_gone', 'run_id': self.run_id,
                                        'exit_error': f'{type(error).__name__}: {error}'})
        self.ledger._lock.acquire(timeout=10)
        with contextlib.suppress(Exception):
            lock.release()
        self.heartbeat(state='stopped')
        cli = _CLI_RUN
        if cli is not None:
            with contextlib.suppress(Exception):
                report.setdefault('metrics', {})['op_seconds'] = round(time.perf_counter() - cli['started'], 3)
                cli['stdout'].write(json.dumps(report, ensure_ascii=False, default=str) + '\n')
                cli['stdout'].flush()
        exit_now(EXIT_CODES.get(report.get('status'), 2), 'parent_gone', self.run_id)

    def _orphaned(self, holder):
        """True when the lock holder is alive but the console that launched it is not. Creation times
        recorded in the lock make a reused pid count as gone, not as the same process."""
        if not holder or holder.get('released_utc') or not holder.get('parent_pid'):
            return False
        return (pid_alive(holder.get('pid'), holder.get('pid_started'))
                and not pid_alive(holder.get('parent_pid'), holder.get('parent_started')))

    def safe_replan(self, reason):
        """Re-plan; a failure to plan (a report folder mid-sync, a locked file) pauses and retries
        instead of ending the warmer. Only the stop guards end it. Planning reads each industry's first
        Graph view, so it waits for the same free-memory floor as a task."""
        while True:
            try:
                free = self.free_bytes()
            except Exception:
                free = None
            if free is not None and free < MIN_FREE_BYTES:
                log(f'plan deferred: {free / 1024 ** 2:.0f} MB free physical memory')
                self._emit('mentat.prewarm.pause', 'waiting', 'Plan deferred: low memory', stage='guard',
                           metadata={'root_cause_hint': 'low_memory', 'free_mb': round(free / 1024 ** 2)})
                self.nap(PAUSE_SECONDS)
                continue
            try:
                return self.replan(reason)
            except Stop:
                raise
            except Exception as error:
                hint = classify(error)
                if hint == 'code_changed':
                    raise Stop(hint, str(error)) from error
                retry, stop = recovery(hint)
                self.ledger.append({'schema': SCHEMA, 'ts_utc': utc_iso(), 'run_id': self.run_id, 'pid': os.getpid(),
                                    'op': self.op, 'task_id': None, 'kind': 'plan', 'dataset': None,
                                    'outcome': 'failed', 'root_cause_hint': hint, 'retry': 'retry after 60 s',
                                    'stop': 'planning failures never quarantine; the warmer re-plans every 60 s',
                                    'error': f'{type(error).__name__}: {error}'})
                self._emit('mentat.prewarm.plan', 'failed', f'Plan failed ({hint}): {type(error).__name__}: {error}',
                           stage='plan')
                log(f'plan failed: {type(error).__name__}: {error}')
                self.nap(PAUSE_SECONDS)

    def _loop(self):
        if self.parent_pid and self.start_delay:
            self.nap(self.start_delay)
        self.check_stop()
        self.safe_replan('start')
        idle_announced = False
        while True:
            self.check_stop()
            self.promote_hints()
            trigger = self.replan_trigger()
            if trigger is None and self.last_source_check is not None and \
                    self.monotonic() - self.last_source_check >= SOURCE_CHECK_SECONDS and self._sources_changed_safe():
                trigger = 'source_changed'
            if trigger:
                self.safe_replan(trigger)
                idle_announced = False
            task = self.next_task()
            if task is None:
                later = self.pending_later()
                if later is None and self.once:
                    return self._exit(None, 'Plan complete.')
                if not idle_announced:
                    self._emit('mentat.prewarm.run', 'waiting', 'Idle: every planned task is warm, blocked or waiting',
                               stage='idle', metadata={'counts': plan_counts(self.plan)})
                    self.heartbeat(state='idle', counts=plan_counts(self.plan))
                    idle_announced = True
                self.nap(min(IDLE_SECONDS, later) if later is not None else IDLE_SECONDS)
                if later is None and self._sources_changed_safe():
                    self.safe_replan('source_changed')
                    idle_announced = False
                continue
            guard = self.resource_guard(task)
            if guard:
                self.pause(task, *guard)
                continue
            self.resume_if_paused(task)
            idle_announced = False
            if self.execute(task) == 'skipped':   # over budget: nothing ran
                atomic_write_json(self.state.plan, self.plan)
                continue
            self.executed += 1
            # plan.json carries each task's state as it changes, for status and `warm --task`.
            atomic_write_json(self.state.plan, self.plan)
            if self.max_tasks and self.executed >= self.max_tasks:
                return self._exit(None, f'Reached --max-tasks {self.max_tasks}.')

    def _metrics(self, extra=None):
        metrics = {'executed': self.executed, 'succeeded': self.succeeded, 'failed': self.failed,
                   'skipped_over_budget': self.skipped, 'quarantined_now': self.quarantined_now, 'run_id': self.run_id}
        if self.plan:
            metrics['plan_counts'] = plan_counts(self.plan)
        metrics.update(extra or {})
        return metrics

    def _exit(self, root_cause, message):
        self.stop_reason = root_cause or 'done'
        status = 'success' if root_cause in (None, 'code_changed', 'parent_gone') else 'warning'
        retry, stop = recovery(root_cause) if root_cause else (None, 'none')
        self._emit('mentat.prewarm.run', 'completed' if status == 'success' else 'failed',
                   f'Warmer exit: {message}', stage='exit',
                   metadata={'root_cause_hint': root_cause, 'executed': self.executed, 'succeeded': self.succeeded,
                             'failed': self.failed})
        log(f'exit: {root_cause or "done"}: {message}')
        return op_result(status, f'Warmer exited: {message}',
                      next_actions=[stop] if root_cause else [],
                      metrics=self._metrics({'root_cause_hint': root_cause, 'retry': retry}),
                      artifacts=[str(self.state.ledger), str(self.state.plan)])


# ----------------------------------------------------------------------------- op results
def op_result(status, summary, next_actions=(), artifacts=(), metrics=None):
    return {'status': status, 'summary': summary, 'next_actions': list(next_actions), 'artifacts': list(artifacts),
            'metrics': metrics or {}}


# ----------------------------------------------------------------------------- status
def status_report(state, today=None):
    """Everything from the harness's own files; no app module is imported."""
    today = today or local_today()
    plan = read_json(state.plan) or {}
    rows = Ledger(state).read()
    quarantine = read_json(state.quarantine, {}) or {}
    verify = read_json(state.verify) or {}
    heartbeat = read_json(state.heartbeat) or {}
    holder = WarmerLock(state.lock).holder() or {}
    tasks = plan.get('tasks') or []
    task_ids = {task['id'] for task in tasks}
    created = plan.get('created_utc') or ''
    # "Current" = the plan's tasks, observed on the plan's day (a re-plan does not reset the window).
    plan_day = plan.get('as_of') or today
    current_rows = [row for row in rows if row.get('task_id') in task_ids and _local_day(row.get('ts_utc')) == plan_day]
    successes = last_successes(rows)
    code_fp = plan.get('code_fp')

    def warm_now(task):
        return already_warm(task, successes.get(task['id']), plan.get('as_of') or today, code_fp)

    states = {}
    for task in tasks:
        state_name = task.get('state')
        if state_name == 'pending' and warm_now(task):
            state_name = 'warm'
        if task['id'] in quarantine:
            state_name = 'quarantined'
        states[task['id']] = state_name
    total = len(tasks)
    warm = sum(1 for value in states.values() if value == 'warm')
    attempts = {}
    for row in current_rows:
        if row.get('outcome') in ('succeeded', 'failed'):
            attempts.setdefault(row['task_id'], []).append(row['outcome'])
    retries = {task: sum(1 for outcome in outcomes if outcome == 'failed') for task, outcomes in attempts.items()}
    seconds = [row['seconds'] for row in current_rows if row.get('outcome') == 'succeeded' and row.get('seconds') is not None]
    all_seconds = [row['seconds'] for row in rows if row.get('outcome') == 'succeeded' and row.get('seconds') is not None]
    errors = [row for row in rows if row.get('outcome') in ('failed', 'quarantined')][-5:][::-1]
    per_industry = {}
    for task in tasks:
        entry = per_industry.setdefault(task['dataset'], {'query_view': None, 'insights_default': None,
                                                           'neighbors_warm': 0, 'neighbors_planned': 0})
        state_name = states[task['id']]
        label = {'warm': 'warm', 'pending': 'cold', 'blocked': task.get('root_cause_hint') or 'blocked',
                 'quarantined': 'quarantined'}.get(state_name, state_name)
        if task['kind'] in ('query_view', 'insights_default'):
            entry[task['kind']] = label
        elif task['kind'] in ('insights_company', 'graph_spark'):
            prefix = 'companies' if task['kind'] == 'insights_company' else 'graph_spark'
            entry[prefix + '_planned'] = entry.get(prefix + '_planned', 0) + 1
            entry[prefix + '_warm'] = entry.get(prefix + '_warm', 0) + int(state_name == 'warm')
            entry[prefix + '_over_budget'] = entry.get(prefix + '_over_budget', 0) + int(state_name == 'over_budget')
        else:
            entry['neighbors_planned'] += 1
            entry['neighbors_warm'] += int(state_name == 'warm')
    next_tasks = [{'id': task['id'], 'kind': task['kind'], 'dataset': task['dataset']}
                  for task in tasks if states[task['id']] == 'pending'][:5]
    running = bool(holder.get('pid')) and not holder.get('released_utc') and pid_alive(holder.get('pid'),
                                                                                    holder.get('pid_started'))
    metrics = {
        'plan_created_utc': created or None, 'plan_as_of': plan.get('as_of'), 'plan_reason': plan.get('reason'),
        'plan_stale': bool(plan) and plan.get('as_of') != today,
        'tasks_planned': total, 'tasks_warm': warm,
        'completion_rate': round(warm / total, 4) if total else None,
        'states': {name: sum(1 for value in states.values() if value == name) for name in sorted(set(states.values()))},
        'retries_per_task': {'mean': round(statistics.mean(retries.values()), 3) if retries else 0.0,
                             'max': max(retries.values()) if retries else 0,
                             'by_task': {task: count for task, count in retries.items() if count}},
        'seconds_per_success': {'current_plan': _summary(seconds), 'all_time': _summary(all_seconds)},
        'quarantined': [{'task_id': task, **{key: value for key, value in entry.items() if key != 'last_error'},
                         'last_error': str(entry.get('last_error'))[:300]} for task, entry in quarantine.items()],
        'last_errors': [{'ts_utc': row.get('ts_utc'), 'task_id': row.get('task_id'), 'outcome': row.get('outcome'),
                         'root_cause_hint': row.get('root_cause_hint'), 'retry': row.get('retry'),
                         'error': str(row.get('error'))[:300]} for row in errors],
        'pass_at_1': verify.get('pass_at_1'), 'verify_utc': verify.get('created_utc'),
        'per_industry': per_industry, 'next_tasks': next_tasks,
        'warmer': {'running': running, 'pid': holder.get('pid'), 'started_utc': holder.get('started_utc'),
                   'parent_pid': holder.get('parent_pid'), 'heartbeat': heartbeat},
        'ledger_rows': len(rows),
    }
    return metrics


def _summary(values):
    if not values:
        return {'count': 0, 'mean': None, 'max': None}
    return {'count': len(values), 'mean': round(statistics.mean(values), 3), 'max': round(max(values), 3)}


# ----------------------------------------------------------------------------- verify
VOLATILE_PATHS = (('history', 'inspected_at'), ('history', 'newly_inspected_versions'), ('source_history',))


def strip_volatile(payload):
    """Drop the journal's own clock and counters: they describe when the journal inspected the scope,
    not the analysis (history.inspected_at, history.newly_inspected_versions, source_history)."""
    value = copy.deepcopy(payload)
    for path in VOLATILE_PATHS:
        node = value
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(path[-1], None)
    return value


def first_difference(left, right, path='$'):
    """The first JSON path (sorted keys, list order) where two values differ, or None."""
    if type(left) is not type(right) and not (isinstance(left, (int, float)) and isinstance(right, (int, float))
                                            and not isinstance(left, bool) and not isinstance(right, bool)):
        return path
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right), key=str):
            if key not in left or key not in right:
                return f'{path}.{key}'
            found = first_difference(left[key], right[key], f'{path}.{key}')
            if found:
                return found
        return None
    if isinstance(left, list):
        for index in range(max(len(left), len(right))):
            if index >= len(left) or index >= len(right):
                return f'{path}[{index}]'
            found = first_difference(left[index], right[index], f'{path}[{index}]')
            if found:
                return found
        return None
    return None if left == right else path


def read_cached_payload(path):
    """The payload store.cached published at `path`, integrity-checked like store.cached reads it."""
    envelope = json.loads(Path(path).read_bytes())
    payload = envelope['payload'].encode('utf-8')
    if hashlib.sha256(payload).hexdigest() != envelope['sha256']:
        raise ValueError('The cached response failed its own sha256 check.')
    return envelope['key'], json.loads(payload)


def link_or_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return 'present'
    try:
        os.link(source, target)
        return 'hardlink'
    except OSError:
        shutil.copy2(source, target)
        return 'copy'


def verify_samples(rows, today, sample, task_filter=None):
    """Newest succeeded Insights observation per task for today, round-robin over datasets."""
    newest = {}
    for row in rows:
        if row.get('outcome') != 'succeeded' or row.get('kind') not in INSIGHTS_KINDS:
            continue
        if task_filter and row.get('task_id') not in task_filter:
            continue
        if row.get('as_of') != today or not row.get('response_path') or not row.get('key'):
            continue
        newest[row['task_id']] = row
    by_dataset = {}
    for row in sorted(newest.values(), key=lambda item: (item['dataset'] or '', item['task_id'])):
        by_dataset.setdefault(row['dataset'], []).append(row)
    chosen = []
    while len(chosen) < sample and any(by_dataset.values()):
        for dataset in sorted(by_dataset):
            if by_dataset[dataset] and len(chosen) < sample:
                chosen.append(by_dataset[dataset].pop(0))
    return chosen


def run_verify(options):
    """Recompute sampled warmed Insights scopes FRESH in a temporary sandbox and compare them with the
    cached responses; check keys against hints and the insights.js default body."""
    target = StateDir(Path(options.state_root).resolve() if options.state_root else DEFAULT_STATE_ROOT)
    rows = Ledger(target).read()
    today = local_today()
    plan = read_json(target.plan) or {}
    task_bodies = {task['id']: task for task in plan.get('tasks', [])}
    filters = set(options.task.split(',')) if options.task else None
    samples = verify_samples(rows, today, options.sample, filters)
    contract = client_contract_check()
    sandbox = Path(tempfile.mkdtemp(prefix='mentat-prewarm-verify-'))
    backend = AppBackend(data_root=options.data_root, state_root=sandbox,
                         guard_extra_allowed=[target.dir])
    started = time.monotonic()
    results, key_checks = [], []
    emit('mentat.prewarm.verify', 'running', f'Verify: recomputing {len(samples)} warmed scopes fresh',
         stage='verify', total=len(samples))
    try:
        backend.load()
        real_store = target.state_root / 'contribution-query-cache'
        linked = {}
        for index, row in enumerate(samples, 1):
            task = task_bodies.get(row['task_id'])
            body = (task or {}).get('body')
            entry = {'task_id': row['task_id'], 'dataset': row['dataset'], 'warmed_key': row['key'],
                     'cached_path': row['response_path']}
            if body is None:
                entry.update(passed=False, reason='task body not in the current plan.json')
                results.append(entry)
                continue
            try:
                cached_key, cached = read_cached_payload(row['response_path'])
                linked.update(_link_projection(backend, row['dataset'], real_store, sandbox / 'contribution-query-cache'))
                began = time.monotonic()
                info = backend.warm_insights(body, deadline=time.monotonic() + TASK_TIMEOUT_SECONDS)
                entry['fresh_seconds'] = round(time.monotonic() - began, 3)
                entry['fresh_key'] = info['key']
                entry['key_match'] = info['key'] == row['key']
                fresh_key, fresh = read_cached_payload(info['response_path'])
                entry['store_key_match'] = fresh_key == cached_key
                difference = first_difference(strip_volatile(cached), strip_volatile(fresh))
                entry['first_difference'] = difference
                entry['passed'] = bool(entry['key_match'] and entry['store_key_match'] and difference is None)
            except Exception as error:
                entry.update(passed=False, error=f'{type(error).__name__}: {error}', root_cause_hint=classify(error))
            results.append(entry)
            emit('mentat.prewarm.verify', 'running', f'Verified {row["task_id"]}: {"pass" if entry.get("passed") else "FAIL"}',
                 stage='verify', current=index, total=len(samples))
        key_checks = _key_checks(backend, target, rows, today, plan)
    finally:
        with contextlib.suppress(Exception):
            backend.release_graph_memory()
        if not options.keep_sandbox:
            shutil.rmtree(sandbox, ignore_errors=True)
    passed = sum(1 for entry in results if entry.get('passed'))
    report = {'created_utc': utc_iso(), 'today': today, 'sampled': len(results), 'passed': passed,
              'pass_at_1': round(passed / len(results), 4) if results else None, 'results': results,
              'key_checks': key_checks, 'client_contract': contract,
              'volatile_paths_excluded': ['$.' + '.'.join(path) for path in VOLATILE_PATHS],
              'sandbox': str(sandbox) if options.keep_sandbox else None,
              'seconds': round(time.monotonic() - started, 3)}
    atomic_write_json(target.verify, report)
    key_failures = [check for check in key_checks if check.get('match') is False]
    failed = [entry for entry in results if not entry.get('passed')]
    status = 'success'
    if failed or key_failures or not contract['ok']:
        status = 'error'
    elif not results:
        status = 'warning'
    summary = (f'pass@1 {report["pass_at_1"]} ({passed}/{len(results)} fresh recomputes identical); '
               f'{sum(1 for c in key_checks if c.get("match"))}/{sum(1 for c in key_checks if c.get("match") is not None)} key checks match; '
               f'client contract {"ok" if contract["ok"] else "DRIFT"}')
    emit('mentat.prewarm.verify', 'completed' if status == 'success' else 'failed', summary, stage='verify')
    actions = []
    if not results:
        actions.append('warm some Insights scopes first (prewarm.py run or warm --task ID), then verify')
    if failed:
        actions.append('inspect verify.json results[].first_difference; do not trust the warmed scope')
    if key_failures:
        actions.append('a warmed key differs from the key the client body produces; compare the bodies in verify.json key_checks')
    if not contract['ok']:
        actions.append('the client changed a pinned request line; update prewarm.CLIENT_CONTRACT and the body builders')
    return op_result(status, summary, actions, [str(target.verify)],
                  {'pass_at_1': report['pass_at_1'], 'sampled': len(results), 'passed': passed,
                   'key_checks': len(key_checks), 'key_mismatches': len(key_failures),
                   'client_contract_ok': contract['ok'], 'seconds': report['seconds'],
                   'results': [{key: entry.get(key) for key in ('task_id', 'passed', 'first_difference',
                                                                  'key_match', 'fresh_seconds', 'error')}
                               for entry in results]})


def _link_projection(backend, dataset, real_store, sandbox_store):
    """Hard-link the existing Insights projection (and its proof) into the sandbox, so the fresh
    recompute reuses the verified narrow copy instead of rebuilding it (analysis_scan.view re-hashes
    it against its proof before use)."""
    data, store, scan = backend.data, backend.store, backend.scan
    source = next(item for item in data._sources(backend.report_directory) if item['id'] == dataset)
    index = data.CACHE_ROOT / (data._token(source) + '.sqlite')
    identity = {'source_stamp': list(data._stamp(index)), 'insights_projection': scan.CODE_SHA}
    name = store._key(identity)
    done = {}
    for suffix in ('.sqlite', '.json'):
        real = Path(real_store) / 'insights-views' / (name + suffix)
        if real.is_file():
            done[real.name] = link_or_copy(real, Path(sandbox_store) / 'insights-views' / real.name)
    return done


class _KeyProbe(Exception):
    pass


def probe_insights_key(backend, body):
    """The request key the real route derives for `body`, without building it (sandbox only).

    The key is read where query() reports it (_hint, or the store call on older code); a build the
    request would start is stopped at the store before it computes anything, and its failed job is
    discarded so it cannot answer a later request."""
    store, analysis = backend.store, backend.analysis
    captured = {}
    event = threading.Event()
    wrapped = store.cached

    def probe(namespace, identity, compute, stable=lambda: True):
        if namespace == 'insights':
            captured['identity'] = copy.deepcopy(identity)
            event.set()
            raise _KeyProbe()
        return wrapped(namespace, identity, compute, stable)

    if backend.hints is not None:
        backend.hints.reset()
    store.cached = probe
    try:
        for _ in range(50):
            try:
                backend.route_post('/api/insights/query', {**body, 'retry': True})
            except _KeyProbe:
                break
            if backend.hints is not None and backend.hints.last():
                break
            if event.wait(0.2):
                break
        # Let a job this probe started reach the store (and fail there) before it is discarded.
        with contextlib.suppress(Exception):
            with analysis._lock:
                pending = [job for job in analysis._jobs.values() if not job.done()]
            for job in pending:
                with contextlib.suppress(Exception):
                    job.exception(timeout=10)
    finally:
        store.cached = wrapped
        with contextlib.suppress(Exception):
            with analysis._lock:
                for key in [key for key, job in analysis._jobs.items()
                            if job.done() and isinstance(job.exception(), _KeyProbe)]:
                    del analysis._jobs[key]
    if backend.hints is not None and backend.hints.last():
        return backend.hints.last()
    identity = captured.get('identity')
    return identity.get('request_key') if isinstance(identity, dict) else None


def _key_checks(backend, target, rows, today, plan):
    """(1) each hint recorded today: the key the harness path derives for the hint's own body equals the
    key the console recorded; (2) each insights_default warmed today: the key of the insights.js default
    body equals the warmed key."""
    checks = []
    successes = last_successes(rows)
    for hint in read_hints(target.hints):
        seen = (hint.get('seen_utc') or '')
        seen_day = _local_day(seen)
        entry = {'check': 'hint', 'dataset': hint['dataset'], 'body': hint['body'], 'recorded_key': hint['key'],
                 'seen_utc': seen}
        if seen_day != today:
            entry.update(match=None, reason='hint recorded on another day; its as_of (and key) differ by design')
        elif not hint['key']:
            entry.update(match=None, reason='hint has no recorded key')
        else:
            try:
                probed = probe_insights_key(backend, hint['body'])
                entry.update(probed_key=probed, match=probed == hint['key'])
            except Exception as error:
                entry.update(match=False, error=f'{type(error).__name__}: {error}')
        checks.append(entry)
    for task in plan.get('tasks', []):
        if task['kind'] != 'insights_default':
            continue
        success = successes.get(task['id'])
        if not success or success.get('as_of') != today or not success.get('key'):
            continue
        body = insights_default_body(task['dataset'])
        entry = {'check': 'insights.js default', 'dataset': task['dataset'], 'body': body,
                 'warmed_key': success['key'], 'task_body_is_client_default': canonical(task['body']) == canonical(body)}
        try:
            probed = probe_insights_key(backend, body)
            entry.update(probed_key=probed, match=probed == success['key'] and entry['task_body_is_client_default'])
        except Exception as error:
            entry.update(match=False, error=f'{type(error).__name__}: {error}')
        checks.append(entry)
    return checks


def _local_day(iso):
    try:
        moment = dt.datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone().date().isoformat()


# ----------------------------------------------------------------------------- bench
class Http:
    """A browser-like client for one console: Host, Origin and token headers, gzip, new connection
    per request (as the perf harness measured)."""

    def __init__(self, base, timeout=900):
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or not parsed.port:
            raise ValueError('--base must be http://127.0.0.1:<port> or http://localhost:<port>.')
        self.host, self.port, self.timeout = parsed.hostname, parsed.port, timeout
        self.token = None

    def request(self, method, path, body=None):
        headers = {'Host': f'{self.host}:{self.port}', 'Accept-Encoding': 'gzip', 'Accept': '*/*'}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode('utf-8')
            headers.update({'Content-Type': 'application/json', 'X-Mentat-Token': self.token or '',
                            'Origin': f'http://{self.host}:{self.port}'})
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        started = time.perf_counter()
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        finally:
            connection.close()
        elapsed = (time.perf_counter() - started) * 1000
        if response.getheader('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
        try:
            value = json.loads(raw.decode('utf-8')) if raw else None
        except ValueError:
            value = None
        if response.status >= 400:
            message = value.get('error') if isinstance(value, dict) else raw[:200]
            raise RuntimeError(f'{method} {path} -> HTTP {response.status}: {message}')
        return value, elapsed


def _parallel(calls):
    """Run callables in parallel threads (the browser's Promise.all); returns results and the wall ms."""
    results, errors = [None] * len(calls), [None] * len(calls)

    def run(index, call):
        try:
            results[index] = call()
        except Exception as error:
            errors[index] = error

    started = time.perf_counter()
    threads = [threading.Thread(target=run, args=(index, call)) for index, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = (time.perf_counter() - started) * 1000
    for error in errors:
        if error:
            raise error
    return results, wall


def bench_insights(client, dataset, sleep=time.sleep):
    """insights.js first open: catalog || session; as soon as the session token arrives, and only if the
    catalog has not, the body is sent early (sendEarly); then POST until ready at pollDelay(), with
    time already spent waiting counted toward the first delay (queryReady)."""
    started = time.perf_counter()
    catalog_box = {}

    def read_catalog():
        try:
            catalog_box['value'] = client.request('GET', '/api/insights/catalog')
        except Exception as error:
            catalog_box['error'] = error
        catalog_box['at'] = time.perf_counter()

    catalog_thread = threading.Thread(target=read_catalog)
    catalog_thread.start()
    session, session_ms = client.request('GET', '/api/contributions/session')
    client.token = session['token']
    body = insights_default_body(dataset)
    early = None
    query_started = time.perf_counter()
    if 'at' not in catalog_box:
        early = client.request('POST', '/api/insights/query', body)
        early_at = time.perf_counter()
    catalog_thread.join()
    if 'error' in catalog_box:
        raise catalog_box['error']
    open_ms = (catalog_box['at'] - started) * 1000
    polls, statuses, first_ms = 0, [], None
    value, elapsed = early if early else client.request('POST', '/api/insights/query', body)
    held_since = early_at if early else None
    while True:
        first_ms = first_ms if first_ms is not None else elapsed
        statuses.append(value.get('status'))
        if value.get('status') not in POLL_STATES:
            break
        if polls >= 220:
            raise TaskTimeout('Insights still preparing after 220 polls.')
        waited = (time.perf_counter() - held_since) if held_since else 0.0
        sleep(max(0.0, insights_poll_delay(polls, value.get('retry_after_ms')) - waited))
        held_since = None
        polls += 1
        value, elapsed = client.request('POST', '/api/insights/query', body)
    query_ms = (time.perf_counter() - query_started) * 1000
    return {'open_ms': round((time.perf_counter() - started) * 1000, 1), 'catalog_ms': round(open_ms, 1),
            'session_ms': round(session_ms, 1), 'early_query': bool(early), 'query_ms': round(query_ms, 1),
            'first_response_ms': round(first_ms, 1), 'polls': polls, 'statuses': _compress(statuses),
            'status': statuses[-1]}


def bench_graph(client, industry, sleep=time.sleep):
    """graph.js's open chain for one industry: report-sources, graph-catalog, /api/graph (first load),
    then contributions session || catalog, then the sparkline query until not preparing."""
    steps = {}
    started = time.perf_counter()
    _, steps['report_sources_ms'] = client.request('GET', '/api/report-sources')
    catalog, steps['graph_catalog_ms'] = client.request('GET', '/api/graph-catalog?id=published-reports')
    if industry not in [item['name'] for item in catalog.get('industries', [])]:
        raise ValueError(f'{industry} is not in the Graph catalog.')
    view, steps['graph_ms'] = client.request('GET', '/api/graph?' + graph_first_load_query(industry))
    (session, contributions), steps['session_catalog_ms'] = _parallel([
        lambda: client.request('GET', '/api/contributions/session?industry=' + urllib.parse.quote(industry)),
        lambda: client.request('GET', '/api/contributions/catalog')])
    client.token = session[0]['token']
    dataset = next((item['id'] for item in contributions[0].get('datasets', [])
                    if item.get('available') is not False and (item.get('id') == industry or item.get('label') == industry)), None)
    body = spark_request(dataset, view.get('filters')) if dataset else None
    polls, statuses = 0, []
    spark_started = time.perf_counter()
    if body:
        while True:
            value, elapsed = client.request('POST', '/api/contributions/query', body)
            statuses.append(value.get('status'))
            if value.get('status') not in POLL_STATES:
                break
            polls += 1
            if polls >= 120:
                raise TaskTimeout('Sparkline query still preparing after 120 polls.')
            sleep(spark_poll_delay(value.get('retry_after_ms')))
    steps['spark_query_ms'] = round((time.perf_counter() - spark_started) * 1000, 1) if body else None
    steps = {key: round(value, 1) if isinstance(value, float) else value for key, value in steps.items()}
    return {**steps, 'total_ms': round((time.perf_counter() - started) * 1000, 1), 'spark_polls': polls,
            'spark_statuses': _compress(statuses), 'dataset': dataset, 'cycle': (view.get('filters') or {}).get('cycle')}


def run_bench(options):
    parsed = urllib.parse.urlparse(options.base or '')
    if parsed.port == PAUL_CONSOLE_PORT and not options.allow_paul_console:
        return op_result('error', f'Refusing to benchmark Paul\'s console on port {PAUL_CONSOLE_PORT}.',
                      ['start your own read-only instance and pass its --base, or pass --allow-paul-console'])
    client = Http(options.base)
    health, _ = client.request('GET', '/api/health')   # build_status only: no source hashing
    setup_ms = None
    if options.industries:
        # No catalog read before the first timed sample: on a fresh console that read pays the
        # restart hashing (perf.md H1) and would hide it from repeat 1.
        industries = [name.strip() for name in options.industries.split(',') if name.strip()]
    else:
        catalog, setup_ms = client.request('GET', '/api/insights/catalog')
        industries = [item['id'] for item in catalog.get('datasets', []) if item.get('available') is not False]
    out = Path(options.out) if options.out else StateDir(options.state_root or DEFAULT_STATE_ROOT).dir / \
        f'bench-{time.strftime("%Y%m%dT%H%M%S")}'
    out.mkdir(parents=True, exist_ok=True)
    samples = []
    emit('mentat.prewarm.bench', 'running', f'Bench {options.base}: {", ".join(industries)} x{options.repeats}',
         stage='bench', total=len(industries) * options.repeats * 2)
    count = 0
    for repeat in range(1, options.repeats + 1):
        for industry in industries:
            for kind, runner in (('graph_open', bench_graph), ('insights_default', bench_insights)):
                count += 1
                entry = {'repeat': repeat, 'industry': industry, 'kind': kind, 'started_utc': utc_iso()}
                try:
                    entry.update(runner(client, industry))
                except Exception as error:
                    entry['error'] = f'{type(error).__name__}: {error}'
                samples.append(entry)
                emit('mentat.prewarm.bench', 'running', f'Bench {kind} {industry} repeat {repeat}',
                     stage='bench', current=count, total=len(industries) * options.repeats * 2)
    summary = {}
    for entry in samples:
        metric = 'total_ms' if entry['kind'] == 'graph_open' else 'open_ms'
        bucket = summary.setdefault(entry['industry'], {}).setdefault(entry['kind'], {'values': [], 'errors': 0})
        if 'error' in entry:
            bucket['errors'] += 1
        else:
            bucket['values'].append(entry[metric])
    for industry in summary.values():
        for bucket in industry.values():
            values = bucket.pop('values')
            bucket.update(p50_ms=round(statistics.median(values), 1) if values else None,
                          max_ms=round(max(values), 1) if values else None, first_ms=values[0] if values else None,
                          n=len(values))
    report = {'created_utc': utc_iso(), 'base': options.base, 'console_pid': health.get('pid'),
              'console_started_at': health.get('started_at'), 'read_only': health.get('read_only'),
              'repeats': options.repeats, 'industries': industries,
              'setup_catalog_ms': round(setup_ms, 1) if setup_ms is not None else None,
              'summary': summary, 'samples': samples}
    (out / 'bench.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    (out / 'bench.md').write_text(_bench_markdown(report), encoding='utf-8')
    errors = sum(1 for entry in samples if 'error' in entry)
    emit('mentat.prewarm.bench', 'completed' if not errors else 'failed',
         f'Bench done: {len(samples)} samples, {errors} errors', stage='bench')
    return op_result('warning' if errors else 'success', f'{len(samples)} samples over {len(industries)} industries; {errors} errors',
                  ['read bench.md'] if not errors else ['read bench.json samples[].error'],
                  [str(out / 'bench.json'), str(out / 'bench.md')], {'summary': summary, 'errors': errors})


def _bench_markdown(report):
    lines = [f'# Mentat prewarm bench ({report["created_utc"]})', '',
             f'Console {report["base"]} (pid {report["console_pid"]}, read_only {report["read_only"]}), '
             f'{report["repeats"]} repeats. Graph = graph.js open chain to sparklines; Insights = insights.js first open '
             '(catalog || session, early query, then POST until ready at pollDelay).',
             f'Untimed setup catalog read: {report.get("setup_catalog_ms")} ms '
             '(none when --industries is given, so repeat 1 carries the after-restart cost).', '',
             '| industry | kind | first ms | p50 ms | max ms | n | errors |', '|---|---|---|---|---|---|---|']
    for industry, kinds in report['summary'].items():
        for kind, bucket in kinds.items():
            lines.append(f'| {industry} | {kind} | {bucket["first_ms"]} | {bucket["p50_ms"]} | {bucket["max_ms"]} | '
                         f'{bucket["n"]} | {bucket["errors"]} |')
    lines += ['', '## Samples', '', '| repeat | industry | kind | total ms | steps |', '|---|---|---|---|---|']
    for entry in report['samples']:
        total = entry.get('total_ms', entry.get('open_ms'))
        steps = {key: value for key, value in entry.items() if key.endswith('_ms') and key not in ('total_ms', 'open_ms')}
        extra = entry.get('error') or ', '.join(f'{key}={value}' for key, value in steps.items())
        polls = entry.get('polls', entry.get('spark_polls'))
        lines.append(f'| {entry["repeat"]} | {entry["industry"]} | {entry["kind"]} | {total} | {extra}; polls {polls} |')
    return '\n'.join(lines) + '\n'


# ----------------------------------------------------------------------------- in-console API
_startup_lock = threading.Lock()
_startup = {'thread': None, 'state': 'not started', 'steps': [], 'errors': [], 'skipped': []}


def start_startup_warm(read_only=False, app=None):
    """Fill the console's in-memory caches on one daemon thread, so the first click after a restart is
    warm. Idempotent (the first call wins). It never raises, and it never blocks a request: every step
    runs the function the route itself runs, under the route's own locks and caches, and every error is
    logged and emitted. It runs on read-only consoles too, because it only reads (source_guard.verify
    may publish a missing source proof, exactly as the first analytical click would)."""
    try:
        with _startup_lock:
            if _startup['thread'] is not None:
                return _startup['thread']
            thread = threading.Thread(target=_startup_warm, args=(bool(read_only), app),
                                      name='mentat-startup-warm', daemon=True)
            _startup['thread'] = thread
            _startup['state'] = 'running'
            _prime.update(phase='starting', started_utc=utc_iso())
            thread.start()
            return thread
    except Exception as error:
        log(f'startup warm could not start: {type(error).__name__}: {error}')
        return None


def startup_warm_status():
    return {'state': _startup['state'], 'steps': list(_startup['steps']), 'errors': list(_startup['errors']),
            'skipped': list(_startup.get('skipped') or [])}


# The startup prime (RACF 3.5): the hot set is primed into memory before any other startup step.
# phase: disabled (no startup warm in this console) -> starting -> hot -> background -> done | failed.
_prime = {'phase': 'disabled', 'started_utc': None, 'primed_utc': None, 'finished_utc': None, 'items': [],
          'background': 0, 'estimate_s': 0.0, 'ranking': None, 'error': None}
PRIME_TERMINAL_PHASES = ('background', 'done', 'failed', 'disabled')


def prime_status():
    """GET /api/prime-status: read-only, from this process's memory only. An item is ready when the
    console answers it from memory or disk without a build."""
    items = [dict(item) for item in _prime['items']]
    ready = sum(1 for item in items if item.get('state') == 'ready')
    phase = _prime['phase']
    return {'primed': phase in ('background', 'done') and ready == len(items),
            'hot_total': len(items), 'hot_ready': ready, 'phase': phase,
            'started_utc': _prime['started_utc'], 'primed_utc': _prime['primed_utc'],
            'finished_utc': _prime['finished_utc'], 'background_total': _prime['background'],
            'estimate_s': _prime['estimate_s'], 'ranking': _prime['ranking'], 'error': _prime['error'],
            'items': [{key: item.get(key) for key in ('id', 'kind', 'industry', 'state', 'value', 'seconds', 'reason')}
                      for item in items]}


def _route_graph_params(app, directory, params, snapshot_registry, industry_scope):
    """server.py do_GET for /api/graph with recorded parameters (record_access's graph_view body)."""
    wanted = params.get('industry', '')
    registry = snapshot_registry.registry(directory) if wanted else None
    label = industry_scope.label_or_value(registry, wanted) if registry else wanted
    return app.graph(params.get('id', ''), label, {**params, 'industry': label}, registry)


def _route_comparison(app, directory, industry, snapshot_registry, industry_scope):
    """server.py do_GET for /api/comparison-flows: the response_cache entry (memory, then disk, then build)."""
    registry = snapshot_registry.registry(directory) if industry else None
    label = industry_scope.label_or_value(registry, industry) if registry else industry
    return app.comparison_entry('published-reports', label)


def _probe_insights(item, pinned=None):
    """(state, reason) of a hinted Insights scope: analysis_data._probe (memory, then the hash-verified
    disk envelope; never computes, never waits) loads it into memory when it is stored under the key the
    console recorded today and still carries the running analysis code and the verified source."""
    import analysis_data
    payload = analysis_data._probe({'request_key': item['key']})
    if payload is None:
        return 'needs_build', 'not stored: the disk warmer builds it'
    provenance = ((payload.get('metadata') or {}).get('provenance') or {}) if isinstance(payload, dict) else {}
    code = provenance.get('analysis_code_sha256')
    if code and getattr(analysis_data, '_code_hash', code) != code:
        return 'needs_build', 'stored under earlier analysis code'
    stored = payload.get('provenance') or {} if isinstance(payload, dict) else {}
    for key in ('workbook_sha256', 'database_sha256'):
        if (pinned or {}).get(key) and stored.get(key) and stored[key] != pinned[key]:
            return 'needs_build', 'stored under an earlier source'
    return 'ready', 'stored'


def _startup_step(name, function, run_id, current=-1, total=-1):
    started = time.perf_counter()
    try:
        value = function()
        seconds = time.perf_counter() - started
        _startup['steps'].append({'step': name, 'seconds': round(seconds, 3)})
        emit('mentat.prewarm.startup', 'running', f'Startup warm: {name} ({seconds:.1f} s)', run_id=run_id,
             stage='startup', current=current, total=total, duration_ms=int(seconds * 1000))
        return value
    except Exception as error:
        seconds = time.perf_counter() - started
        _startup['errors'].append({'step': name, 'error': f'{type(error).__name__}: {error}', 'seconds': round(seconds, 3)})
        log(f'startup warm step {name} failed: {type(error).__name__}: {error}')
        emit('mentat.prewarm.startup', 'failed', f'Startup warm: {name} failed: {type(error).__name__}: {error}',
             run_id=run_id, stage='startup', current=current, total=total, duration_ms=int(seconds * 1000))
        return None


def _startup_warm(read_only, app):
    run_id = f'mentat-startup-{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}-{os.getpid()}'
    began = time.perf_counter()
    try:
        emit('mentat.prewarm.startup', 'running', f'Startup warm started (read_only={read_only})', run_id=run_id,
             stage='startup', metadata={'read_only': read_only, 'graph_views': app is not None})
        import contribution_data
        import contribution_workbook_guard
        import report_insights
        import snapshot_registry
        import industry_scope
        directory = getattr(app, 'report_directory', None)
        if directory is None:
            if str(PROJECT / 'tools') not in sys.path:
                sys.path.insert(0, str(PROJECT / 'tools'))
            from report_directory import report_directory
            directory = report_directory(PROJECT)
        # 1. Source discovery (perf.md H1/H6: 2.6 s of workbook and hydrated-DB hashing on first use).
        #    Through the module attribute, so an installed source_memo is the function warmed.
        sources = _startup_step('sources', lambda: contribution_data._sources(directory), run_id) or []
        available = [item['id'] for item in sources if item.get('available')]
        hints = read_hints(StateDir(DEFAULT_STATE_ROOT).hints)
        ordered = industry_order(available, hints)
        verified, pinned = set(), {}

        def verify(dataset, index=-1, total=-1):
            # 2. The source-guard verification each analytical route runs first (server.py:2438; H1: ~5 s
            #    of SHA-256 on first use): the same function, per industry, never strict, so one bad
            #    source cannot stop the rest.
            if dataset in verified or dataset not in available:
                return
            verified.add(dataset)
            checked = _startup_step(f'verify_sources {dataset}',
                                    lambda: contribution_workbook_guard.verify_sources(directory, dataset, strict=False),
                                    run_id, index, total) or []
            pinned[dataset] = next((item for item in checked if item.get('dataset') == dataset), None) or {}

        if app is None:
            for index, dataset in enumerate(ordered, 1):
                verify(dataset, index, len(ordered))
            # The Insights route imports analysis_data lazily (server.py:2757); import it now, off the click.
            _startup_step('import analysis_data', lambda: __import__('analysis_data'), run_id)
            _prime.update(phase='done', primed_utc=utc_iso(), finished_utc=utc_iso())
            _startup['state'] = 'done (no console app: graph views skipped)'
            return
        reports = _startup_step('published_reports', app.published_reports, run_id) or []
        catalog = _startup_step('graph_catalog', lambda: app.graph_catalog('published-reports', reports), run_id) or {}
        groups = catalog.get('industries') or []
        names = [group['name'] for group in groups]
        receipted = set()

        def receipts(name, index=-1, total=-1):
            # 3. Workbook receipts (report_insights.embedded_receipts: perf.md H2, 0.1-0.6 s per workbook).
            group = next((item for item in groups if item['name'] == name), None)
            if group is None or name in receipted:
                return
            receipted.add(name)
            for report_index in group['indices']:
                _startup_step(f'embedded_receipts {name}',
                              lambda report_index=report_index: report_insights.embedded_receipts(
                                  report_insights.stamp(app.report_path('published-reports', report_index))),
                              run_id, index, total)

        # THE HOT SET FIRST (RACF 3.5): the highest-value items that fit the startup budget, each primed
        # through the route's own code, which looks in memory, then the verified disk tier, then builds.
        access_rows = load_access_top(kinds=ACCESS_GRAPH_KINDS + ('comparison_flows', 'insights_query'))
        candidates, ranking = prime_candidates(names, hints, access_rows, local_today())
        hot, background, estimate = select_hot_set(candidates, HOT_MAX_ITEMS, HOT_BUDGET_SECONDS)
        _prime.update(items=[{**item, 'state': 'pending'} for item in hot], background=len(background),
                      estimate_s=estimate, ranking={**ranking, 'access_rows': len(access_rows),
                                                    'basis': 'access+hints' if access_rows else 'hints'},
                      phase='hot')
        emit('mentat.prewarm.startup', 'running', f'Prime: {len(hot)} hot items (est. {estimate:.1f} s), '
             f'{len(background)} background', run_id=run_id, stage='prime', total=len(hot),
             metadata={'hot': [item['id'] for item in hot], 'ranking': _prime['ranking']})
        primers = {'graph_default': lambda item: _route_graph(app, directory, item['industry'], snapshot_registry, industry_scope),
                   'graph_view': lambda item: _route_graph_params(app, directory, item['body'], snapshot_registry, industry_scope),
                   'graph_drawer': lambda item: _route_graph_params(app, directory, item['body'], snapshot_registry, industry_scope),
                   'comparison_flows': lambda item: _route_comparison(app, directory, item['industry'], snapshot_registry, industry_scope)}
        for index, item in enumerate(_prime['items'], 1):
            if free_physical_bytes() < MIN_FREE_BYTES:
                item.update(state='skipped', reason='low memory: left to the first click')
                continue
            verify(item['industry'])
            started = time.perf_counter()
            try:
                if item['kind'] == 'insights_scope':
                    _startup_step('import analysis_data', lambda: __import__('analysis_data'), run_id)
                    state, reason = _probe_insights(item, pinned.get(item['industry']))
                else:
                    receipts(item['industry'])
                    primers[item['kind']](item)
                    state, reason = 'ready', 'in memory'
            except Exception as error:
                state, reason = 'failed', f'{type(error).__name__}: {error}'
                _startup['errors'].append({'step': f'prime {item["id"]}', 'error': reason})
            item.update(state=state, reason=reason, seconds=round(time.perf_counter() - started, 3))
            emit('mentat.prewarm.startup', 'completed' if state == 'ready' else 'waiting',
                 f'Prime {item["kind"]} {item["industry"]}: {state} ({item["seconds"]:.2f} s)', run_id=run_id,
                 stage='prime', current=index, total=len(_prime['items']), duration_ms=int(item['seconds'] * 1000))
        _prime.update(phase='background', primed_utc=utc_iso())
        emit('mentat.prewarm.startup', 'completed', f'Prime: {prime_status()["hot_ready"]}/{len(hot)} hot items ready',
             run_id=run_id, stage='prime', metadata={'status': {key: value for key, value in prime_status().items()
                                                                 if key != 'items'}})
        # Background priority: every other industry's verification, then the remaining default views.
        for index, dataset in enumerate(ordered, 1):
            verify(dataset, index, len(ordered))
        # The Insights route imports analysis_data lazily (server.py:2757); import it now, off the click.
        _startup_step('import analysis_data', lambda: __import__('analysis_data'), run_id)
        primed = {item['industry'] for item in _prime['items'] if item['kind'] == 'graph_default' and item.get('state') == 'ready'}
        first = names[:1]
        graph_order = [name for name in first + [name for name in industry_order(names, hints) if name not in first]
                       if name not in primed]
        for index, name in enumerate(graph_order, 1):
            if free_physical_bytes() < MIN_FREE_BYTES:
                # A guard, not a failure: the views not warmed here are built by the first click, as before.
                _startup.setdefault('skipped', []).append({'step': f'graph {name} and later', 'reason': 'low memory'})
                emit('mentat.prewarm.startup', 'skipped', f'Startup warm: graph views stopped at {name}: low memory',
                     run_id=run_id, stage='startup', current=index, total=len(graph_order))
                break
            receipts(name, index, len(graph_order))
            # 4. The default /api/graph view, through the route's own glue and Console.graph.
            _startup_step(f'graph {name}', lambda name=name: _route_graph(app, directory, name,
                                                                        snapshot_registry, industry_scope),
                          run_id, index, len(graph_order))
        _startup['state'] = 'done'
        _prime.update(phase='done', finished_utc=utc_iso())
    except Exception as error:
        _startup['state'] = 'failed'
        _startup['errors'].append({'step': 'startup', 'error': f'{type(error).__name__}: {error}'})
        _prime.update(phase='failed', error=f'{type(error).__name__}: {error}', finished_utc=utc_iso())
        log(f'startup warm failed: {type(error).__name__}: {error}')
    finally:
        seconds = time.perf_counter() - began
        skipped = _startup.get('skipped') or []
        emit('mentat.prewarm.startup', 'completed' if not _startup['errors'] else 'failed',
             f'Startup warm {_startup["state"]} in {seconds:.1f} s; {len(_startup["errors"])} errors, '
             f'{len(skipped)} skipped', run_id=run_id, stage='startup', duration_ms=int(seconds * 1000),
             metadata={'steps': len(_startup['steps']), 'errors': _startup['errors'][:10], 'skipped': skipped})


def _route_graph(app, directory, industry, snapshot_registry, industry_scope):
    """server.py do_GET for /api/graph (:2625-2636) with graph.js's first-load query."""
    query = urllib.parse.parse_qs(graph_first_load_query(industry))
    wanted = query.get('industry', [''])[0]
    registry = snapshot_registry.registry(directory) if wanted else None
    label = industry_scope.label_or_value(registry, wanted) if registry else wanted
    return app.graph(query.get('id', [''])[0], label, {**{key: value[0] for key, value in query.items()},
                                                       'industry': label}, registry)


def launch_warmer(data_root, read_only, console_port, state_root=None):
    """Start the disk warmer beside a console and return its pid (None when not started). Never on a
    read-only console. Never waits on the child: its output goes to real file handles, and the single-
    instance rule is enforced by the warmer's own lock, not here."""
    if read_only:
        return None
    try:
        parent = os.getpid()
        state = StateDir(state_root or DEFAULT_STATE_ROOT).ensure()
        # One process in this console's environment (python_child): under Mentat's venv, sys.executable is
        # CPython's venv launcher, which would put a second process in front of the warmer, and the EcoQoS
        # opt-out below would land on that launcher instead of the warmer.
        import python_child
        executable, environment = python_child.command()
        command = [executable, str(Path(__file__).resolve()), 'run', '--data-root', str(data_root),
                   '--parent-pid', str(parent), '--console', f'http://127.0.0.1:{int(console_port)}']
        if state_root:
            command += ['--state-root', str(state_root)]
        flags = (BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW) if os.name == 'nt' else 0
        with open(state.dir / f'warmer-{parent}.log', 'ab') as output:
            child = subprocess.Popen(command, cwd=os.getcwd(), stdin=subprocess.DEVNULL, stdout=output,
                                     stderr=output, close_fds=True, creationflags=flags, env=environment)
        # The warmer turns EcoQoS off itself (set_below_normal_priority), but only once its imports and
        # module loading are done; turned off here, its start-up runs at full speed too (review H1).
        unthrottled = None
        if os.name == 'nt' and getattr(child, '_handle', None) is not None:
            try:
                unthrottled = execution_speed_unthrottled(child._handle)
            except Exception as error:   # never fails the launch: the child still sets it itself
                unthrottled = f'{type(error).__name__}: {error}'
        emit('mentat.prewarm.launch', 'info', f'Console pid {parent} launched warmer pid {child.pid}',
             stage='launch', metadata={'console_port': console_port, 'command': command, 'ecoqos_off': unthrottled,
                                       'pyvenv_launcher': (environment or {}).get(python_child.LAUNCHER_VARIABLE)})
        return child.pid
    except Exception as error:
        log(f'warmer launch failed: {type(error).__name__}: {error}')
        emit('mentat.prewarm.launch', 'failed', f'Warmer launch failed: {type(error).__name__}: {error}', stage='launch')
        return None


# ----------------------------------------------------------------------------- CLI
def _parser():
    parser = argparse.ArgumentParser(description='Mentat prewarm harness (one JSON object on stdout).')
    parser.add_argument('op', choices=('plan', 'run', 'warm', 'verify', 'bench', 'status', 'hotset'))
    parser.add_argument('--data-root', default=None, help='The console data root (server.py --data-root).')
    parser.add_argument('--state-root', default=None, help='Scratch state root outside mentat/ (default: mentat/state).')
    parser.add_argument('--industries', default=None, help='Comma-separated dataset ids to restrict the plan.')
    parser.add_argument('--allow-index-builds', action='store_true', help='Build a missing analytical index (multi-GB).')
    parser.add_argument('--max-neighbors', type=int, default=DEFAULT_MAX_COMPANY_NEIGHBORS,
                        help='Most company neighbours warmed from the newest hint.')
    parser.add_argument('--parent-pid', type=int, default=None)
    parser.add_argument('--console', default=None, help='The parent console URL, for the read-only busy probe.')
    parser.add_argument('--once', action='store_true', help='run: exit when the plan is exhausted instead of idling.')
    parser.add_argument('--max-tasks', type=int, default=None)
    parser.add_argument('--start-delay', type=float, default=None)
    parser.add_argument('--task', default=None, help='warm: the task id; verify: comma-separated task ids.')
    parser.add_argument('--sample', type=int, default=3)
    parser.add_argument('--keep-sandbox', action='store_true')
    parser.add_argument('--base', default=None)
    parser.add_argument('--out', default=None)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--allow-paul-console', action='store_true')
    return parser


def _industries(options):
    return [item.strip() for item in options.industries.split(',') if item.strip()] if options.industries else None


def op_plan(options):
    state = StateDir(Path(options.state_root).resolve() if options.state_root else DEFAULT_STATE_ROOT)
    holder = WarmerLock(state.lock).holder() or {}
    if holder.get('pid') and not holder.get('released_utc') and pid_alive(holder.get('pid'), holder.get('pid_started')):
        # A running warmer owns plan.json; a CLI plan beside it is written next to it, not over it.
        state.plan = state.dir / 'plan.cli.json'
    backend = AppBackend(data_root=options.data_root, state_root=options.state_root,
                         allow_index_builds=options.allow_index_builds).load()
    warmer = Warmer(backend, state, op='plan', industries=_industries(options),
                    allow_index_builds=options.allow_index_builds, max_company_neighbors=options.max_neighbors,
                    set_priority=False, access_top=warmer_access_top)
    plan = warmer.replan('cli plan')
    blocked = [task for task in plan['tasks'] if task['state'] == 'blocked']
    actions = []
    if blocked:
        datasets = sorted({task['dataset'] for task in blocked})
        actions.append('index_missing for ' + ', '.join(datasets) + ': open the industry in the console to build its '
                       'analytical index, or rerun a task with --allow-index-builds')
    pending = [task for task in plan['tasks'] if task['state'] == 'pending']
    return op_result('warning' if blocked or plan['skipped'] else 'success',
                  f'{len(plan["tasks"])} tasks ({len(pending)} to warm, {len(blocked)} blocked, '
                  f'{len(plan["skipped"])} skipped) over {len(plan["order"])} industries',
                  actions, [str(state.plan)],
                  {'counts': plan_counts(plan), 'order': plan['order'], 'as_of': plan['as_of'],
                   'code_fp': plan['code_fp'], 'skipped': plan['skipped'], 'pending_expansion': plan['pending_expansion'],
                   'pending_companies': plan.get('pending_companies'), 'budget': plan.get('budget'),
                   'tasks': [{'id': task['id'], 'state': task['state'], 'reason': task['reason']} for task in plan['tasks']],
                   'sources': plan['sources'], 'notes': backend.notes})


def op_run(options):
    state = StateDir(Path(options.state_root).resolve() if options.state_root else DEFAULT_STATE_ROOT)
    backend = AppBackend(data_root=options.data_root, state_root=options.state_root,
                         allow_index_builds=options.allow_index_builds)
    delay = options.start_delay if options.start_delay is not None else (START_DELAY_SECONDS if options.parent_pid else 0)
    warmer = Warmer(backend, state, op='run', parent_pid=options.parent_pid, console=options.console,
                    once=options.once, max_tasks=options.max_tasks, start_delay=delay,
                    allow_index_builds=options.allow_index_builds, industries=_industries(options),
                    max_company_neighbors=options.max_neighbors, access_top=warmer_access_top)
    try:
        backend.load()
    except Exception as error:
        return op_result('error', f'Could not load the Mentat modules: {type(error).__name__}: {error}',
                      ['fix the startup error above; the warmer does not run without the real modules'],
                      metrics={'root_cause_hint': classify(error)})
    return warmer.run()


def op_warm(options):
    if not options.task:
        return op_result('error', 'warm needs --task ID (see plan).', ['run: prewarm.py plan'])
    state = StateDir(Path(options.state_root).resolve() if options.state_root else DEFAULT_STATE_ROOT)
    backend = AppBackend(data_root=options.data_root, state_root=options.state_root,
                         allow_index_builds=options.allow_index_builds).load()
    warmer = Warmer(backend, state, op='warm', industries=_industries(options),
                    allow_index_builds=options.allow_index_builds, max_company_neighbors=options.max_neighbors,
                    set_priority=False)
    lock = WarmerLock(state.lock)
    acquired, holder = lock.acquire({'pid': os.getpid(), 'pid_started': process_started(os.getpid()),
                                     'started_utc': utc_iso(), 'op': 'warm', 'task': options.task})
    if not acquired:
        return op_result('warning', f'A warmer (pid {(holder or {}).get("pid")}) holds the lock; not warming.',
                      ['retry after that warmer exits', 'or read prewarm.py status'],
                      metrics={'root_cause_hint': 'lock_held_by_other_process', 'lock_holder': holder})
    try:
        plan = warmer.replan('cli warm')
        task = next((item for item in plan['tasks'] if item['id'] == options.task), None)
        note = None
        if task is None:
            stored = read_json(state.plan) or {}
            task = next((item for item in stored.get('tasks', []) if item['id'] == options.task), None)
            note = 'task taken from the stored plan.json' if task else None
        if task is None:
            return op_result('error', f'No task {options.task} in the plan.', ['run: prewarm.py plan'])
        if task['state'] == 'blocked' and not options.allow_index_builds:
            retry, stop = recovery('index_missing')
            warmer.observe(task, 'failed', hint='index_missing', retry=retry, stop=stop,
                           error='analytical index missing; not attempted')
            return op_result('warning', f'{task["id"]}: analytical index missing; not warmed.', [stop],
                          metrics={'root_cause_hint': 'index_missing', 'retry': retry})
        if task['state'] == 'quarantined':
            return op_result('warning', f'{task["id"]} is quarantined.',
                          ['it is released when its source fingerprint or the code changes; read quarantine.json'],
                          metrics={'root_cause_hint': (read_json(state.quarantine, {}) or {}).get(task['id'], {}).get('root_cause_hint')})
        task['state'] = 'pending'
        outcome = warmer.execute(task)
        row = Ledger(state).read()[-1]
        status = 'success' if outcome == 'succeeded' else 'warning' if outcome == 'skipped' else 'error'
        return op_result(status, f'{task["id"]}: {outcome} in {row.get("seconds")} s' + (f' ({note})' if note else ''),
                      [row['retry']] if row.get('retry') else [], [str(state.ledger)],
                      {key: row.get(key) for key in ('outcome', 'seconds', 'key', 'response_path', 'as_of',
                                                      'root_cause_hint', 'retry', 'stop', 'error', 'polls', 'statuses')})
    finally:
        lock.release()


def op_status(options):
    state = StateDir(Path(options.state_root).resolve() if options.state_root else DEFAULT_STATE_ROOT)
    metrics = status_report(state)
    status = 'success'
    actions = []
    if not metrics['tasks_planned']:
        status, actions = 'warning', ['no plan yet: run prewarm.py plan (or start the console, which launches the warmer)']
    elif metrics['quarantined'] or metrics['plan_stale']:
        status = 'warning'
        if metrics['quarantined']:
            actions.append('inspect quarantine.json; tasks return when their source fingerprint or code changes')
        if metrics['plan_stale']:
            actions.append('the plan is from an earlier day; the warmer re-plans at the next check')
    summary = (f'{metrics["tasks_warm"]}/{metrics["tasks_planned"]} warm (completion {metrics["completion_rate"]}), '
               f'{len(metrics["quarantined"])} quarantined, pass@1 {metrics["pass_at_1"]}, '
               f'warmer {"running" if metrics["warmer"]["running"] else "not running"}')
    return op_result(status, summary, actions, [str(state.plan), str(state.ledger)], metrics)


HOT_ROW_FIELDS = ('id', 'kind', 'industry', 'value', 'access', 'importance', 'cost_s', 'size', 'est_s', 'source',
                  'tier', 'why')


def op_hotset(options):
    """The startup hot set, ranked with its scores (RACF 3.4-3.5), exactly as the console's prime ranks it."""
    state = StateDir(Path(options.state_root).resolve() if options.state_root else DEFAULT_STATE_ROOT)
    backend = AppBackend(data_root=options.data_root, state_root=options.state_root).load()
    industries = [item['dataset'] for item in backend.sources() if item.get('available')]
    wanted = _industries(options)
    if wanted:
        industries = [name for name in industries if name in wanted]
    access_rows = load_access_top(kinds=ACCESS_GRAPH_KINDS + ('comparison_flows', 'insights_query'))
    candidates, ranking = prime_candidates(industries, read_hints(state.hints), access_rows, local_today())
    hot, background, estimate = select_hot_set(candidates, HOT_MAX_ITEMS, HOT_BUDGET_SECONDS)
    basis = 'access+hints' if access_rows else 'hints'
    return op_result('success', f'{len(hot)} hot items (estimated {estimate} s of startup work), {len(background)} '
                     f'background; ranked by {basis}', [], [str(state.hints)],
                     {'hot': [{key: item.get(key) for key in HOT_ROW_FIELDS} for item in hot],
                      'background': [{key: item.get(key) for key in HOT_ROW_FIELDS} for item in background[:40]],
                      'background_total': len(background), 'estimate_s': estimate, 'ranking': ranking,
                      'basis': basis, 'access_rows': len(access_rows),
                      'budget': {'max_items': HOT_MAX_ITEMS, 'budget_seconds': HOT_BUDGET_SECONDS,
                                 'memory_floor_mb': round(MIN_FREE_BYTES / 1024 ** 2)},
                      'formula': 'value = (access_score + importance) * log2(2 + rebuild_s) / (1 + size_MB / 64); '
                                 'importance = 1/(1 + use rank) + 1.0 for the most recently used industry '
                                 '(x0.25 for non-default views; + 1/(1 + hint position) for Insights scopes)'})


OPS = {'plan': op_plan, 'run': op_run, 'warm': op_warm, 'verify': run_verify, 'bench': run_bench, 'status': op_status,
       'hotset': op_hotset}
# The guard stops after which the process ends at once (exit_now) when it runs as the CLI.
GUARD_STOPS = ('parent_gone', 'code_changed')
# While main(hard_exit=True) runs its op: {'stdout': the stream of its one JSON report, 'started': its
# perf_counter}. Only then does Warmer.run watch its console from a second thread (Warmer._watch_parent).
_CLI_RUN = None


def exit_now(code, root_cause, run_id=None):
    """End this process at once after a guard stop (review finding H3). sys.exit() joins every non-daemon
    thread at interpreter shutdown, analysis_data's Insights pool worker included: a warmer whose console
    was gone kept building after it logged its exit (orphan 58192 held the live P&C journal's write
    transaction and its build marker for hours). First every lock this process still holds is released
    (release-if-unchanged: Insights build markers, response-cache and sidecar locks; Warmer.run, or the
    watchdog's Warmer._end_orphaned, has released the warmer lock), then the exit is recorded in the log
    and Glass Box (both write synchronously; the ledger was appended to before this) and the streams are
    flushed. SQLite rolls back a write transaction left open by a process that ends; a half-written file
    is never served."""
    released = []
    with contextlib.suppress(Exception):
        released = proclock.release_all()
    running = [thread.name for thread in threading.enumerate()
               if thread is not threading.current_thread() and thread.is_alive() and not thread.daemon]
    message = (f'exiting now after {root_cause}: released {len(released)} held lock(s); '
               f'{len(running)} non-daemon thread(s) stopped with it: {", ".join(running[:6]) or "none"}')
    log(message)
    emit('mentat.prewarm.run', 'info', 'Warmer ' + message, run_id=run_id or '', stage='exit',
         metadata={'root_cause_hint': root_cause, 'released_locks': released, 'running_threads': running})
    logging_module = sys.modules.get('logging')
    if logging_module is not None:
        with contextlib.suppress(Exception):
            logging_module.shutdown()
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(code)


def main(argv=None, *, hard_exit=False):
    global _CLI_RUN
    options = _parser().parse_args(argv)
    real_stdout = sys.stdout
    started = time.perf_counter()
    previous, _CLI_RUN = _CLI_RUN, ({'stdout': real_stdout, 'started': started} if hard_exit else None)
    try:
        # Anything a library prints goes to stderr, so stdout carries exactly one JSON object.
        with contextlib.redirect_stdout(sys.stderr):
            report = OPS[options.op](options)
    except Stop as stop:
        report = op_result('success', f'Stopped: {stop}', [recovery(stop.root_cause)[1]],
                        metrics={'root_cause_hint': stop.root_cause})
    except Exception as error:
        report = op_result('error', f'{options.op} failed: {type(error).__name__}: {error}',
                        ['read the traceback on stderr'], metrics={'root_cause_hint': classify(error)})
        traceback.print_exc(file=sys.stderr)
    finally:
        _CLI_RUN = previous
    report.setdefault('metrics', {})['op_seconds'] = round(time.perf_counter() - started, 3)
    real_stdout.write(json.dumps(report, ensure_ascii=False, default=str) + '\n')
    real_stdout.flush()
    code = EXIT_CODES.get(report.get('status'), 2)
    cause = (report.get('metrics') or {}).get('root_cause_hint')
    if hard_exit and cause in GUARD_STOPS:
        exit_now(code, cause, (report.get('metrics') or {}).get('run_id'))
    return code


if __name__ == '__main__':
    sys.exit(main(hard_exit=True))

"""Collect and verify the established media sources before the daily pipeline.

The native parent runner owns scheduling and the final Excel/dashboard chain.
This helper owns source collection, independent press acceptance, and the media
inputs that the canonical chain consumes. A failed source never produces PASS.

The press collector budgets body retrieval per run and exits 3 when bodies
remain queued for the next pass. That is a designed partial outcome, so it is
recorded as PARTIAL rather than counted as a collection failure, and the
terminal receipt always names the partial sources alongside its status.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT
from lane_file_io import replace_atomic
from pipeline_lock import child_lock_environment, pipeline_run_lock

CODE = Path(__file__).resolve().parents[1]
RECEIPT = Path('data/engine/daily_media_refresh_receipt.json')
SUCCESS_RECEIPT = Path('data/engine/daily_media_refresh_success.json')
SCHEMA = 'daily-media-refresh-receipt-v1'
BLUESKY_RECEIPT = Path('data/engine/social_source_receipts/bluesky.json')
PRESS_PARTIAL_RETURNCODE = 3
PRESS_RETRY_RECEIPT = 'press_network_retry_receipt.json'
# Smoke keeps the two Congressional Record collectors and Bluesky: all three are
# credential-free against their own official sources, and together they cover the
# congress_record and bluesky validation branches. The interview and Washington
# Journal collectors are the slow, browser-driven ones and are left out.
SMOKE_SOURCES = ('crec', 'chrg', 'bluesky')
# Derived steps that hand the intake gate this run's press directory by path.
# audit_collection.py --scope all still audits press_delta (:1273-1275), and
# build_press_engine_feed.validate_body_completion:99-101 refuses any receipt
# whose body backlog is open regardless of require_complete, so pointing it at
# an incomplete generation is a guaranteed rejection. When the chain runs the
# same tool without --press-delta-root it discovers a run instead, and
# discover_run:300-307 skips the refused one, so the canonical chain still
# audits the last complete press generation.
PRESS_GATED_DERIVED = ('all_intake',)


@dataclass(frozen=True)
class Step:
    name: str
    script: str
    args: tuple[str, ...] = ()
    timeout: int = 1800
    kind: str = 'producer'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, candidate = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
        # The receipt is polled by the watcher while the run is in flight.
        replace_atomic(candidate, path)
    finally:
        if os.path.exists(candidate):
            os.unlink(candidate)


def press_run_root(step, root):
    """The press collector's own dated run directory, as passed on its command line."""
    if '--run-root' in step.args:
        return Path(step.args[step.args.index('--run-root') + 1])
    return Path(root) / 'outputs'


def press_partial_details(step, root):
    """Record what the budgeted press retry pass has left for the next run."""
    run_root = press_run_root(step, root)
    receipt = run_root / PRESS_RETRY_RECEIPT
    if not receipt.is_file():
        return {'note': 'retry receipt absent', 'run_root': str(run_root)}
    try:
        document = json.loads(receipt.read_text(encoding='utf-8'))
    except Exception as error:
        return {'note': 'retry receipt unreadable: ' + type(error).__name__ + ': ' + str(error),
                'run_root': str(run_root)}
    return {'pending_body_urls': document.get('pending_body_urls'),
            'failed_bodies': document.get('failed_bodies'),
            'status': document.get('status'),
            'run_root': str(run_root)}


def smoke_sources(steps):
    """The credential-free source subset a smoke pass collects."""
    return [step for step in steps if step.name in SMOKE_SOURCES]


def skipped_row(name, kind='press', status='SKIPPED_SMOKE', note=None):
    """A step deliberately not run. Never a PASS, never a FAIL, never an error."""
    row = {'source': name, 'kind': kind, 'status': status, 'error_count': 0,
           'started_at': utc_now(), 'completed_at': utc_now(), 'duration_seconds': 0}
    if note:
        row['note'] = note
    return row


def deferred_intake_note(press_row):
    """Say why the intake gate was not handed this run's press directory."""
    if press_row.get('status') == 'PARTIAL':
        pending = (press_row.get('partial') or {}).get('pending_body_urls')
        return ('press generation incomplete (pending_body_urls=%s); '
                'intake deferred until a complete run' % pending)
    return 'press lane did not run; intake deferred until a complete run'


def park_remaining(steps, note):
    """Rows for steps that must not consume an incomplete press generation."""
    return [skipped_row(step.name, step.kind, 'SKIPPED_PARTIAL', note) for step in steps]


def summarize_partials(state):
    """A PASS receipt must name partial coverage and everything that did not run."""
    rows = [row for row in state.get('sources') or [] if row.get('status') == 'PARTIAL']
    state['partial'] = bool(rows)
    state['partial_sources'] = [row['source'] for row in rows]
    state['press_partial'] = next((row.get('partial') for row in rows if row['source'] == 'press'), None)
    state['parked_steps'] = [row['source'] for row in state.get('sources') or []
                             if row.get('status') in ('SKIPPED_PARTIAL', 'SKIPPED_SMOKE')]


def source_steps(today, last_success=None):
    yesterday = (today - timedelta(days=1)).isoformat()
    def floor(days):
        default = today - timedelta(days=days)
        return min(default, last_success - timedelta(days=1)) if last_success else default
    def window(days):
        return ('--since', floor(days).isoformat(), '--until', yesterday)
    def budget(seconds, days):
        # Longer offline gaps receive proportionally more time, while each
        # child still has a finite bound and incomplete pagination fails closed.
        duration = (today - floor(days)).days
        return min(86400, max(seconds, (seconds * duration + days - 1) // days))
    steps = [
        Step('crec', 'collect/fetch_congressional_record.py', ('crec', *window(14)), budget(3000, 14), 'congress_record'),
        Step('chrg', 'collect/fetch_congressional_record.py', ('chrg', *window(400)), budget(3000, 400), 'congress_record'),
    ]
    for name, days, timeout in (
        ('cnn', 14, 1800), ('mtp', None, 1200), ('cbs', None, 1200), ('abc', None, 1200),
        ('pbs', 21, 1800), ('npr', 14, 1800), ('fox', None, 1800), ('msnow', None, 3600),
    ):
        steps.append(Step(name, 'members/fetch_interviews.py',
                          (name, *(window(days) if days else ()), '--strict'),
                          budget(timeout, days) if days else timeout, 'interview'))
    steps.extend([
        Step('washington_journal', 'collect/refresh_cspan_wj.py', window(21), budget(5400, 21), 'washington_journal'),
        Step('bluesky', 'collect/tweet_collector.py',
             ('fetch', '--platform', 'bluesky', '--verified-only', '--strict',
              '--since', floor(14).isoformat(), '--pages', '50',
              '--receipt', str(BLUESKY_RECEIPT)), budget(14400, 14), 'bluesky'),
    ])
    return steps


def press_steps(root, today):
    press_root = Path(root) / 'outputs' / ('press_refresh_' + today.strftime('%Y%m%d'))
    intake = 'data/engine/press_collection_intake_receipt.json'
    return [
        Step('press', 'collect/refresh_press_stream.py',
             ('--run-root', str(press_root), '--date-floor', '2026-06-25', '--workers', '4'), 129600, 'press'),
        Step('press_intake', 'audit_collection.py',
             ('--scope', 'press', '--press-delta-root', str(press_root), '--output', intake)),
        Step('press_feed', 'collect/build_press_engine_feed.py', ('--intake-receipt', intake)),
        Step('press_baseline_fold', 'collect/fold_promoted_press_runs.py'),
    ]


def derived_steps(root, today):
    press_root = Path(root) / 'outputs' / ('press_refresh_' + today.strftime('%Y%m%d'))
    return [
        Step('all_intake', 'audit_collection.py',
             ('--scope', 'all', '--press-delta-root', str(press_root),
              '--output', 'data/engine/collection_intake_receipt.json')),
        Step('hearing_extract', 'stance/extract_cu_hearings.py', timeout=600),
        Step('interview_extract', 'stance/extract_cu_interviews.py', timeout=600),
        Step('bluesky_extract', 'stance/extract_cu_bluesky.py', timeout=600),
        Step('social_master', 'network/build_social_master.py', timeout=1800),
        Step('big5_traits', 'members/big5_traits.py', timeout=1800),
        Step('interaction_features', 'members/interaction_features.py', timeout=900),
        Step('behavior_panel', 'members/behavior_panel.py', timeout=900),
        Step('behavioral_dispositions', 'members/behavioral_dispositions.py', timeout=900),
        Step('big5_lexcal', 'members/big5_lexcal_v2.py', timeout=900),
        Step('big5_hybrid', 'members/big5_hybrid.py', timeout=900),
        Step('social_interactions', 'network/social_interaction_layer.py', timeout=900),
        Step('co_appearance', 'network/co_appearance_layer.py', timeout=600),
        Step('reference_classification', 'members/member_reference_layer.py', ('--score', '--go'), 14400),
    ]


def channel_scope(environment):
    return {
        'bluesky': {'status': 'SCHEDULED', 'scope': 'registered verified handles; minimum 14-day window, extended to catch up since last successful collection'},
        'x_direct': {'status': 'NOT_INTEGRATED', 'credential_configured': bool(environment.get('X_BEARER_TOKEN')),
                     'reason': 'Direct X API posts do not yet feed the canonical social workbook; licensed Quorum inputs remain active.'},
        'truth_direct': {'status': 'NOT_INTEGRATED', 'credential_configured': bool(environment.get('TRUTH_TOKEN')),
                         'reason': 'Direct Truth API posts do not yet feed the canonical social workbook; licensed Quorum inputs remain active.'},
        'quorum': {'status': 'EXISTING_LICENSED_INPUT', 'automatic_remote_collection': False,
                   'reason': 'The supplied export is reprocessed by Social Master; no unattended licensed export downloader is configured.'},
        'audio_identity': {'status': 'EXISTING_ACCEPTED_INPUT', 'automatic_identity_admission': False,
                           'reason': 'The canonical pipeline retains the established speaker-review gate.'},
    }


def child_environment(root, lease):
    environment = {**os.environ, **child_lock_environment(lease),
                   'MONEYBALL_ROOT': str(root), 'PYTHONUNBUFFERED': '1', 'PYTHONUTF8': '1'}
    for key in ('BSKY_SKIP_FETCHED', 'DESK_OUT', 'DASH_OUT'):
        environment.pop(key, None)
    return environment


def successful_collection_date(root, today):
    path = Path(root) / SUCCESS_RECEIPT
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding='utf-8'))
    if document.get('schema') != SCHEMA or document.get('status') != 'PASS':
        raise RuntimeError('saved media catch-up anchor is not a successful collection receipt')
    result = date.fromisoformat(document['local_date'])
    if result > today:
        raise RuntimeError('saved media catch-up anchor is later than the current local run date')
    return result


def effective_windows(steps):
    result = {}
    for step in steps:
        window = {}
        for flag in ('--since', '--until', '--date-floor'):
            if flag in step.args:
                window[flag[2:].replace('-', '_')] = step.args[step.args.index(flag) + 1]
        result[step.name] = window or {'scope': 'source archive or existing accepted input'}
    return result


def fetch_log_snapshot(root, table):
    if table not in ('cr_fetch_log', 'iv_fetch_log'):
        raise ValueError('unsupported collector log table')
    path = Path(root) / 'social_media.db'
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=60) as connection:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
            return {}
        return {str(key): (str(status), str(stamp)) for key, status, stamp in
                connection.execute('SELECT key,status,ts FROM ' + table)}


def changed_log_errors(before, after):
    # Empty/member-free days and source-attested terminal losses are explicit
    # outcomes; anything else besides ok is an unresolved collection item.
    terminal = {'ok', 'nosession', 'noep', 'empty', 'nopage', 'gone'}
    return [{'key': key, 'status': status} for key, (status, stamp) in after.items()
            if before.get(key) != (status, stamp) and status.lower() not in terminal]


def require_receipt(path, schema, started):
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    completed = datetime.fromisoformat(str(document.get('completed_at') or '').replace('Z', '+00:00'))
    if (document.get('schema') != schema or document.get('status') != 'COMPLETE'
            or completed.timestamp() < started - 1):
        raise RuntimeError('source completion receipt is not current/COMPLETE: ' + str(path))
    return document


def validate_source(step, root, started, before):
    root = Path(root)
    result = {'error_count': 0}
    if step.kind in ('congress_record', 'interview'):
        table = 'cr_fetch_log' if step.kind == 'congress_record' else 'iv_fetch_log'
        after = fetch_log_snapshot(root, table)
        errors = changed_log_errors(before, after)
        if step.name == 'crec':
            start = date.fromisoformat(step.args[step.args.index('--since') + 1])
            end = date.fromisoformat(step.args[step.args.index('--until') + 1])
            for offset in range((end - start).days + 1):
                key = 'crec:CREC-' + (start + timedelta(days=offset)).isoformat()
                if after.get(key, ('', ''))[0] not in ('ok', 'nosession'):
                    if not any(row['key'] == key for row in errors):
                        errors.append({'key': key, 'status': after.get(key, ('MISSING', ''))[0]})
        result.update(error_count=len(errors), item_errors=errors, changed_items=sum(before.get(k) != v for k, v in after.items()))
        if step.kind == 'interview':
            sentinel = root / 'collector_sentinels' / (step.name + '.done')
            if not sentinel.is_file() or sentinel.stat().st_mtime < started - 1:
                raise RuntimeError('interview collector did not write its current completion sentinel: ' + step.name)
            if step.name in ('fox', 'msnow'):
                receipt = root / 'data/engine/interview_source_receipts' / (step.name + '.json')
                document = require_receipt(receipt, 'interview-source-receipt-v1', started)
                result['source_terminal_date'] = document.get('source_terminal_date')
                result['receipt'] = {'path': str(receipt), 'sha256': digest(receipt)}
    elif step.kind == 'washington_journal':
        receipt = root / 'data/engine/interview_source_receipts/washington_journal.json'
        document = require_receipt(receipt, 'washington-journal-collection-v1', started)
        if (document.get('search_window_start') != step.args[step.args.index('--since') + 1]
                or document.get('collector_horizon') != step.args[step.args.index('--until') + 1]):
            raise RuntimeError('Washington Journal completion window differs from requested dates')
        result['receipt'] = {'path': str(receipt), 'sha256': digest(receipt)}
    elif step.kind == 'bluesky':
        receipt = root / BLUESKY_RECEIPT
        document = require_receipt(receipt, 'bluesky-source-receipt-v1', started)
        if (document.get('verified_only') is not True or document.get('strict') is not True
                or document.get('since') != step.args[step.args.index('--since') + 1]
                or document.get('error_count') != 0
                or len(document.get('results') or []) != document.get('selected_handles')
                or any(row.get('status') != 'COMPLETE' for row in document.get('results') or [])):
            raise RuntimeError('Bluesky collection did not verify every selected registered handle')
        result.update(selected_handles=document['selected_handles'],
                      unverified_handles_excluded=document.get('unverified_handles_excluded'),
                      receipt={'path': str(receipt), 'sha256': digest(receipt)})
    return result


def failure_diagnostics(step, root, started, before):
    """Retain current item counts even when a collector correctly exits nonzero."""
    result = {}
    if step.kind in ('congress_record', 'interview'):
        table = 'cr_fetch_log' if step.kind == 'congress_record' else 'iv_fetch_log'
        after = fetch_log_snapshot(root, table)
        errors = changed_log_errors(before, after)
        result.update(item_errors=errors, error_count=len(errors))
    receipt = None
    if step.kind == 'bluesky':
        receipt = Path(root) / BLUESKY_RECEIPT
        schema = 'bluesky-source-receipt-v1'
    elif step.kind == 'interview' and step.name in ('fox', 'msnow'):
        receipt = Path(root) / 'data/engine/interview_source_receipts' / (step.name + '.json')
        schema = 'interview-source-receipt-v1'
    if receipt is not None and receipt.is_file():
        document = json.loads(receipt.read_text(encoding='utf-8'))
        completed = datetime.fromisoformat(str(document.get('completed_at') or '').replace('Z', '+00:00'))
        if document.get('schema') == schema and completed.timestamp() >= started - 1:
            count = sum(int(document.get(key) or 0) for key in
                        ('error_count', 'archive_page_failures', 'new_url_failures'))
            result.update(error_count=max(count, result.get('error_count', 0)),
                          source_status=document.get('status'),
                          receipt={'path': str(receipt), 'sha256': digest(receipt)})
    return result


def terminate_tree(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def run_process(step, root, log_path, environment):
    command = [sys.executable, str(CODE / step.script), *step.args]
    with log_path.open('w', encoding='utf-8') as handle:
        process = subprocess.Popen(command, cwd=root, env=environment, stdout=handle, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                   start_new_session=os.name != 'nt')
        try:
            return process.wait(timeout=step.timeout)
        except BaseException:
            if process.poll() is None:
                terminate_tree(process)
            raise


def execute_step(step, root, run_dir, environment, runner=run_process):
    started = time.time()
    log_path = run_dir / (step.name + '.log')
    row = {'source': step.name, 'kind': step.kind, 'status': 'FAIL', 'started_at': utc_now(),
           'command': [sys.executable, str(CODE / step.script), *step.args],
           'timeout_seconds': step.timeout, 'log': str(log_path), 'error_count': 0}
    before = {}
    try:
        table = 'cr_fetch_log' if step.kind == 'congress_record' else 'iv_fetch_log'
        before = fetch_log_snapshot(root, table) if step.kind in ('congress_record', 'interview') else {}
        code = runner(step, root, log_path, environment)
        row['returncode'] = code
        if step.name == 'press' and code == PRESS_PARTIAL_RETURNCODE:
            # refresh_press_delta.R exits 3 when body retrieval is still queued
            # for the next pass. That per-run budget is deliberate, so the pass
            # is partial coverage rather than a failure: it neither raises nor
            # contributes to error_count, and the lane continues behind it.
            row['partial'] = press_partial_details(step, root)
            row['status'] = 'PARTIAL'
        elif code:
            raise RuntimeError('source process exited ' + str(code))
        else:
            row.update(validate_source(step, root, started, before))
            if row['error_count']:
                raise RuntimeError('%d unresolved collector items despite process exit 0' % row['error_count'])
            row['status'] = 'PASS'
    except Exception as error:
        try:
            diagnostics = failure_diagnostics(step, root, started, before)
            diagnostics['error_count'] = max(row['error_count'], diagnostics.get('error_count', 0))
            if row.get('item_errors'):
                diagnostics['item_errors'] = list({item['key']: item for item in
                    [*row['item_errors'], *diagnostics.get('item_errors', [])]}.values())
            row.update(diagnostics)
        except Exception as diagnostic_error:
            row['diagnostic_error'] = type(diagnostic_error).__name__ + ': ' + str(diagnostic_error)
        row['error_count'] = max(1, row['error_count'])
        row['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        row.update(completed_at=utc_now(), duration_seconds=round(time.time() - started, 2))
        if log_path.is_file():
            row['log_sha256'] = digest(log_path)
    return row


def preflight(root):
    from collect.refresh_press_stream import find_rscript
    from collect.tweet_collector import verified_bluesky_handle
    find_rscript()
    required = ['social_media.db', 'Quorum Master.xlsx', 'data/engine/congestion.json',
                'data/stance/quorum_member_tweets.csv', 'data/stance/stance_clf_composed.pkl',
                'cspan/cspan_scrape.R']
    for relative in required:
        if not (Path(root) / relative).is_file():
            raise RuntimeError('required existing media input is missing: ' + relative)
    draft = Path(root).parent / 'Draft Systems'
    for name in ('cu_press_release_scraper.R', 'all_press_release_index.csv'):
        if not (draft / name).is_file():
            raise RuntimeError('required existing press input is missing: ' + str(draft / name))
    with sqlite3.connect((Path(root) / 'social_media.db').resolve().as_uri() + '?mode=ro', uri=True) as connection:
        verified = sum(verified_bluesky_handle(row[0]) for row in connection.execute('SELECT bluesky FROM members'))
    if not verified:
        raise RuntimeError('no verified Bluesky handles are configured for the scheduled scope')


def run(root=ROOT, *, today=None, runner=run_process, preflight_check=preflight, smoke=False):
    root = Path(root).resolve()
    today = today or date.today()  # fixed once, in the laptop's local timezone
    with pipeline_run_lock(root / '.pipeline_run.lock', owner_kind='daily_media_refresh') as lease:
        run_dir = root / 'audits/daily_media_refresh' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        run_dir.mkdir(parents=True, exist_ok=False)
        environment = child_environment(root, lease)
        state = {'schema': SCHEMA, 'status': 'RUNNING', 'local_date': today.isoformat(),
                 'mode': 'smoke' if smoke else 'full',
                 'owner_pid': os.getpid(), 'started_at': utc_now(), 'run_dir': str(run_dir),
                 'channels': channel_scope(environment), 'sources': [], 'error_count': 0,
                 'partial': False, 'partial_sources': [], 'press_partial': None,
                 'parked_steps': [], 'dashboard_published_by_this_helper': False}
        mutex = threading.Lock()

        def record(row):
            with mutex:
                state['sources'].append(row)
                state['error_count'] += row['error_count']
                state['heartbeat_at'] = utc_now()
                summarize_partials(state)
                write_json(root / RECEIPT, state)
                pending = (row.get('partial') or {}).get('pending_body_urls')
                note = '' if pending is None else ' pending_body_urls=%s' % pending
                print('%s %s: %s (%ss)%s' % (utc_now(), row['source'], row['status'],
                                             row['duration_seconds'], note), flush=True)

        def lane(steps, continue_after_failure, park_on_partial=False):
            for position, step in enumerate(steps):
                row = execute_step(step, root, run_dir, environment, runner)
                record(row)
                if row['status'] == 'PARTIAL' and park_on_partial:
                    # Partial coverage is not a failure, but it is also not a
                    # generation the intake gate may be handed. Park the rest of
                    # the lane rather than feed it an open body backlog.
                    for parked in park_remaining(steps[position + 1:], deferred_intake_note(row)):
                        record(parked)
                    break
                if row['status'] not in ('PASS', 'PARTIAL') and not continue_after_failure:
                    break

        try:
            write_json(root / RECEIPT, state)
            preflight_check(root)
            last_success = successful_collection_date(root, today)
            sources = source_steps(today, last_success)
            press = press_steps(root, today)
            if smoke:
                # A smoke pass collects a credential-free source subset and runs
                # every derived producer. The press lane is recorded as skipped:
                # its collector is the 4-hour one and its intake gate governs the
                # published feed, neither of which a shakeout should touch.
                sources = smoke_sources(sources)
                skipped = [skipped_row(step.name) for step in press]
                press = []
            state.update(last_successful_collection_date=last_success.isoformat() if last_success else None,
                         effective_windows=effective_windows([*sources, *press]))
            if last_success:
                state['catch_up_anchor'] = {'path': str(root / SUCCESS_RECEIPT),
                                           'sha256': digest(root / SUCCESS_RECEIPT)}
            write_json(root / RECEIPT, state)
            if smoke:
                for row in skipped:
                    record(row)
            # Press uses its own files/R workers; the other lane serializes all
            # SQLite writers. Successful press acceptance survives an unrelated
            # interview outage without authorizing a mixed-generation dashboard.
            with ThreadPoolExecutor(max_workers=2) as pool:
                work = [pool.submit(lane, sources, True),
                        pool.submit(lane, press, False, True)]
                for future in work:
                    future.result()
            if not state['error_count']:
                derived = derived_steps(root, today)
                press_row = next((row for row in state['sources'] if row['source'] == 'press'
                                  and row['status'] in ('PARTIAL', 'SKIPPED_SMOKE')), None)
                if press_row is not None:
                    # Same reason as the press lane: these steps name this run's
                    # press directory on their command line.
                    note = deferred_intake_note(press_row)
                    gated = [step for step in derived if step.name in PRESS_GATED_DERIVED]
                    for parked in park_remaining(gated, note):
                        record(parked)
                    derived = [step for step in derived if step.name not in PRESS_GATED_DERIVED]
                lane(derived, False)
            state['status'] = 'FAIL' if state['error_count'] else 'PASS'
        except Exception as error:
            state.update(status='FAIL', error=type(error).__name__ + ': ' + str(error),
                         error_count=max(1, state['error_count']))
        summarize_partials(state)
        state['completed_at'] = utc_now()
        write_json(run_dir / 'receipt.json', state)
        write_json(root / RECEIPT, state)
        # The success receipt is the catch-up anchor for source windows. A smoke
        # pass collected a subset, so it must never become that anchor.
        if state['status'] == 'PASS' and not smoke:
            write_json(root / SUCCESS_RECEIPT, state)
        print(json.dumps({'status': state['status'], 'mode': state['mode'],
                          'error_count': state['error_count'],
                          'partial_sources': state['partial_sources'],
                          'parked_steps': state['parked_steps'],
                          'receipt': str(root / RECEIPT)}), flush=True)
        return 0 if state['status'] == 'PASS' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(ROOT))
    parser.add_argument('--local-date', type=date.fromisoformat,
                        help='Parent runner local date, fixed for the complete daily transaction')
    parser.add_argument('--smoke', action='store_true',
                        help='Collect only the credential-free source subset, skip the press lane, '
                             'run every derived producer, and write no success anchor')
    args = parser.parse_args(argv)
    try:
        return run(args.root, today=args.local_date, smoke=args.smoke)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 75


if __name__ == '__main__':
    raise SystemExit(main())

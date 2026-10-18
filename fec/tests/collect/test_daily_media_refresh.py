from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from collect import build_press_engine_feed as feed
from collect import daily_media_refresh as M


@pytest.fixture(autouse=True)
def quiet_interrupt_state():
    """The stop flag and the live-child registry are module state; no test may leak them."""
    M.STOP.clear()
    M._LIVE.clear()
    yield
    M.STOP.clear()
    M._LIVE.clear()


def test_source_windows_and_press_root_are_bound_to_local_run_date(tmp_path):
    today = date(2026, 9, 13)
    sources = {step.name: step for step in M.source_steps(today)}
    assert sources['crec'].args == ('crec', '--since', '2026-08-30', '--until', '2026-09-12')
    assert sources['chrg'].args == ('chrg', '--since', '2025-08-09', '--until', '2026-09-12')
    assert sources['washington_journal'].args == ('--since', '2026-08-23', '--until', '2026-09-12')
    for name in ('cnn', 'mtp', 'cbs', 'abc', 'pbs', 'npr', 'fox', 'msnow'):
        assert '--strict' in sources[name].args
    assert '--strict' in sources['bluesky'].args and '--verified-only' in sources['bluesky'].args
    press = M.press_steps(tmp_path, today)
    assert press[0].timeout == 72000
    assert press[0].args[1] == str(tmp_path / 'outputs/press_refresh_20260913')
    assert press[0].args[press[0].args.index('--date-floor') + 1] == '2026-06-25'
    assert press[1].args[press[1].args.index('--press-delta-root') + 1] == press[0].args[1]
    assert [step.name for step in press] == ['press', 'press_intake', 'press_feed', 'press_baseline_fold']
    assert not any('weekly_refresh' in step.script for step in M.source_steps(today) + press + M.derived_steps(tmp_path, today))


def test_native_environment_does_not_skip_previously_fetched_bluesky_handles(tmp_path, monkeypatch):
    monkeypatch.setenv('BSKY_SKIP_FETCHED', '1')
    monkeypatch.setenv('DESK_OUT', 'different-path')
    monkeypatch.setenv('DASH_OUT', 'different-path')
    environment = M.child_environment(tmp_path, {'token': 'lease', 'pid': 17})
    assert all(key not in environment for key in ('BSKY_SKIP_FETCHED', 'DESK_OUT', 'DASH_OUT'))
    assert environment['MELANGE_PIPELINE_LOCK_TOKEN'] == 'lease'
    scope = M.channel_scope({'X_BEARER_TOKEN': 'secret-value'})
    assert scope['x_direct']['credential_configured'] is True
    assert scope['x_direct']['status'] == 'NOT_INTEGRATED'
    assert scope['truth_direct']['status'] == 'NOT_INTEGRATED'
    assert 'secret-value' not in json.dumps(scope)


@pytest.mark.parametrize('offline_days', [5, 60])
def test_windows_catch_up_after_extended_offline_period(offline_days):
    today = date(2026, 9, 13)
    anchor = today - timedelta(days=offline_days)
    sources = {step.name: step for step in M.source_steps(today, anchor)}
    for name, days in (('cnn', 14), ('bluesky', 14), ('washington_journal', 21), ('chrg', 400)):
        step = sources[name]
        expected = min(today - timedelta(days=days), anchor - timedelta(days=1))
        assert step.args[step.args.index('--since') + 1] == expected.isoformat()
        assert 0 < step.timeout <= 86400
    if offline_days == 60:
        assert sources['bluesky'].timeout > 14400
        assert sources['washington_journal'].timeout > 5400
    else:
        assert sources['bluesky'].timeout == 14400


def test_item_errors_are_not_hidden_by_process_exit_zero(tmp_path):
    database = tmp_path / 'social_media.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE cr_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)')
    step = M.Step('chrg', 'unused.py', kind='congress_record')

    def runner(step, root, log, environment):
        log.write_text('process exit zero despite item error')
        with sqlite3.connect(database) as connection:
            connection.execute('INSERT INTO cr_fetch_log VALUES(?,?,?)', ('chrg:CHRG-119hhrg7', 'err:timeout', 'new'))
        return 0

    row = M.execute_step(step, tmp_path, tmp_path, {}, runner)
    assert row['returncode'] == 0 and row['status'] == 'FAIL'
    assert row['error_count'] == 1
    assert row['item_errors'] == [{'key': 'chrg:CHRG-119hhrg7', 'status': 'err:timeout'}]
    assert row['log_sha256'] == M.digest(tmp_path / 'chrg.log')


def test_crec_requires_every_requested_day_to_have_terminal_checkpoint(tmp_path):
    with sqlite3.connect(tmp_path / 'social_media.db') as connection:
        connection.execute('CREATE TABLE cr_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)')
        connection.execute('INSERT INTO cr_fetch_log VALUES(?,?,?)', ('crec:CREC-2026-09-10', 'nosession', 'old'))
    step = M.Step('crec', 'unused.py', ('crec', '--since', '2026-09-10', '--until', '2026-09-11'), kind='congress_record')
    result = M.validate_source(step, tmp_path, time.time(), {})
    assert result['item_errors'] == [{'key': 'crec:CREC-2026-09-11', 'status': 'MISSING'}]


def test_nonzero_source_preserves_current_receipt_failure_count(tmp_path):
    step = M.Step('bluesky', 'unused.py', ('--since', '2026-08-30'), kind='bluesky')
    def runner(step, root, log, environment):
        log.write_text('three handles failed')
        M.write_json(root / M.BLUESKY_RECEIPT, {
            'schema': 'bluesky-source-receipt-v1', 'status': 'FAIL',
            'completed_at': datetime.now(timezone.utc).isoformat(), 'error_count': 3,
        })
        return 1
    row = M.execute_step(step, tmp_path, tmp_path, {}, runner)
    assert row['status'] == 'FAIL' and row['returncode'] == 1
    assert row['error_count'] == 3 and row['source_status'] == 'FAIL'
    assert row['receipt']['sha256'] == M.digest(tmp_path / M.BLUESKY_RECEIPT)


def test_old_irrelevant_errors_and_attested_terminal_losses_do_not_block_retry():
    before = {'old:error': ('err:old', 'yesterday')}
    after = {**before, 'abc:removed': ('nopage', 'today'), 'fox:removed': ('gone', 'today'),
             'day:none': ('noep', 'today')}
    assert M.changed_log_errors(before, after) == []


def test_bluesky_receipt_must_cover_every_selected_verified_handle(tmp_path):
    receipt = tmp_path / M.BLUESKY_RECEIPT
    receipt.parent.mkdir(parents=True)
    document = {'schema': 'bluesky-source-receipt-v1', 'status': 'COMPLETE',
                'completed_at': datetime.now(timezone.utc).isoformat(), 'verified_only': True,
                'strict': True, 'since': '2026-08-30', 'selected_handles': 2, 'error_count': 0,
                'results': [{'bioguide': 'A1', 'status': 'COMPLETE'}]}
    receipt.write_text(json.dumps(document))
    step = M.Step('bluesky', 'unused.py', ('--since', '2026-08-30'), kind='bluesky')
    with pytest.raises(RuntimeError, match='every selected'):
        M.validate_source(step, tmp_path, time.time(), {})


@pytest.mark.parametrize('source_failed', [False, True])
def test_parallel_press_finishes_but_derived_inputs_wait_for_all_sources(tmp_path, monkeypatch, source_failed):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}

    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [M.Step('db_one', 'fixture'), M.Step('db_two', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: [M.Step('press', 'fixture'), M.Step('press_accept', 'fixture')])
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [M.Step('derived', 'fixture')])
    barrier = threading.Barrier(2)
    completed = set()
    mutex = threading.Lock()
    anchor = {'schema': M.SCHEMA, 'status': 'PASS', 'local_date': '2026-09-01'}
    M.write_json(tmp_path / M.SUCCESS_RECEIPT, anchor)

    def runner(step, root, log, environment):
        if step.name in ('db_one', 'press'):
            barrier.wait(timeout=5)
        if step.name == 'db_two':
            assert 'db_one' in completed
        if step.name == 'derived':
            assert {'db_one', 'db_two', 'press', 'press_accept'} <= completed
        log.write_text(step.name)
        with mutex:
            completed.add(step.name)
        return 2 if source_failed and step.name == 'db_one' else 0

    code = M.run(tmp_path, today=date(2026, 9, 13), runner=runner, preflight_check=lambda root: None)
    receipt = json.loads((tmp_path / M.RECEIPT).read_text())
    assert {'db_one', 'db_two', 'press', 'press_accept'} <= completed
    assert ('derived' in completed) is not source_failed
    assert code == int(source_failed)
    assert receipt['status'] == ('FAIL' if source_failed else 'PASS')
    assert receipt['schema'] == 'daily-media-refresh-receipt-v1'
    assert receipt['local_date'] == '2026-09-13'
    assert receipt['dashboard_published_by_this_helper'] is False
    # Every step that ran carries its command and log hash; a skipped one never ran.
    assert all('command' in row and 'log_sha256' in row
               for row in receipt['sources'] if not row['status'].startswith('SKIPPED'))
    saved = json.loads((tmp_path / M.SUCCESS_RECEIPT).read_text())
    assert saved['local_date'] == ('2026-09-01' if source_failed else '2026-09-13')
    assert receipt['last_successful_collection_date'] == '2026-09-01'
    assert set(receipt['effective_windows']) == {'db_one', 'db_two', 'press', 'press_accept'}
    assert receipt['partial'] is False and receipt['partial_sources'] == []
    assert receipt['press_partial'] is None
    skipped = {row['source']: row for row in receipt['sources'] if row['status'] == 'SKIPPED_AFTER_FAILURE'}
    if source_failed:
        # The derived step that never ran is on the record, naming what failed.
        assert set(skipped) == {'derived'}
        assert 'db_one' in skipped['derived']['note'] and skipped['derived']['error_count'] == 0
        assert receipt['parked_steps'] == ['derived']
    else:
        assert not skipped and receipt['parked_steps'] == []


def test_press_collection_failure_cannot_promote_or_run_derivatives(tmp_path, monkeypatch):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: [M.Step('press', 'fixture'), M.Step('press_accept', 'fixture')])
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [M.Step('derived', 'fixture')])
    executed = []
    def runner(step, root, log, environment):
        executed.append(step.name)
        log.write_text('failed')
        return 1
    assert M.run(tmp_path, runner=runner, preflight_check=lambda root: None) == 1
    assert executed == ['press']


# --- Budgeted press PARTIAL (refresh_press_delta.R exits 3 with bodies queued) ---


def press_step(run_root):
    return M.Step('press', 'collect/refresh_press_stream.py',
                  ('--run-root', str(run_root), '--date-floor', '2026-06-25', '--workers', '4'),
                  129600, 'press')


def write_retry_receipt(run_root, **overrides):
    # Stamped a few minutes ahead so the receipt is current however long the test
    # takes to reach execute_step; the real collector stamps it after the run starts.
    document = {'schema_version': 2, 'mode': 'live_network_retry', 'status': 'PARTIAL',
                'pending_body_urls': 2200, 'failed_bodies': 2578,
                'completed_at': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
    document.update(overrides)
    M.write_json(Path(run_root) / M.PRESS_RETRY_RECEIPT, document)
    return document


def patch_inspect_run(monkeypatch, excused, error=None):
    """Stand in for the intake gate's read-only run test, so no press files are needed.

    excused=True accepts the run, False refuses it the way the gate does, and an
    error is raised as-is to model a failure that is not a refusal.
    """
    calls = []

    def fake(path):
        calls.append(Path(path))
        if error is not None:
            raise error
        if excused:
            return datetime.now(timezone.utc), {'status': 'PARTIAL', 'pending_body_urls': 2200}
        raise feed.ReceiptRejected('PENDING_BODY_ATTEMPTS', 'a pending body URL is not tombstoned')

    monkeypatch.setattr(feed, 'inspect_run', fake)
    return calls


def test_press_budgeted_partial_is_recorded_and_never_counted_as_a_failure(tmp_path, monkeypatch):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root)
    calls = patch_inspect_run(monkeypatch, excused=True)

    def runner(step, root, log, environment):
        log.write_text('status=PARTIAL pending_body_urls=2200')
        return M.PRESS_PARTIAL_RETURNCODE

    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {}, runner)
    assert row['returncode'] == 3 and row['status'] == 'PARTIAL'
    assert row['error_count'] == 0 and 'error' not in row
    assert row['partial'] == {'pending_body_urls': 2200, 'failed_bodies': 2578,
                              'status': 'PARTIAL', 'run_root': str(run_root),
                              'intake_excused': True}
    assert calls == [run_root]


def test_press_partial_the_gate_refuses_is_recorded_unexcused_with_the_reason(tmp_path, monkeypatch):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root)
    patch_inspect_run(monkeypatch, excused=False)
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
    # Still a designed partial: no failure, no error. It is only not excused.
    assert row['status'] == 'PARTIAL' and row['error_count'] == 0 and 'error' not in row
    assert row['partial']['intake_excused'] is False
    assert row['partial']['intake_refusal_reason'] == 'PENDING_BODY_ATTEMPTS'
    assert 'not tombstoned' in row['partial']['intake_refusal_detail']
    assert row['partial']['pending_body_urls'] == 2200


@pytest.mark.parametrize('error', [RuntimeError('boom'), OSError('disk gone'), AttributeError('odd receipt')])
def test_an_unexpected_inspect_run_error_means_not_excused_never_a_crash(tmp_path, monkeypatch, error):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root)
    patch_inspect_run(monkeypatch, excused=True, error=error)
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
    assert row['status'] == 'PARTIAL' and row['error_count'] == 0 and 'error' not in row
    assert row['partial']['intake_excused'] is False
    assert row['partial']['intake_refusal_reason'] == 'INSPECT_ERROR'
    assert type(error).__name__ in row['partial']['intake_refusal_detail']


@pytest.mark.parametrize('stamp', [
    lambda moment: moment.isoformat(),                                  # offset
    lambda moment: moment.isoformat().replace('+00:00', 'Z'),           # Z suffix
    lambda moment: moment.strftime('%Y-%m-%d %H:%M:%S') + ' UTC',       # refresh_press_delta.R's own form
    lambda moment: moment.replace(tzinfo=None).isoformat(),             # naive: UTC by the producer contract
])
def test_every_receipt_timestamp_dialect_counts_as_current(tmp_path, monkeypatch, stamp):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root, completed_at=stamp(datetime.now(timezone.utc) + timedelta(minutes=5)))
    patch_inspect_run(monkeypatch, excused=True)
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
    assert row['status'] == 'PARTIAL' and row['error_count'] == 0


def test_press_exit_three_without_a_retry_receipt_is_a_failure(tmp_path, monkeypatch):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    calls = patch_inspect_run(monkeypatch, excused=True)
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
    assert row['returncode'] == 3 and row['status'] == 'FAIL' and row['error_count'] >= 1
    assert row['error'].startswith('RuntimeError: press exited 3 without a readable retry receipt')
    # The absence is still on the record, and the gate was never consulted.
    assert row['partial'] == {'note': 'retry receipt absent', 'run_root': str(run_root)}
    assert calls == []


def test_press_exit_three_with_an_unreadable_or_non_object_receipt_is_a_failure(tmp_path, monkeypatch):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    calls = patch_inspect_run(monkeypatch, excused=True)
    run_root.mkdir(parents=True)
    receipt = run_root / M.PRESS_RETRY_RECEIPT
    for text in ('{not json', '[1, 2, 3]', '"PARTIAL"', ''):
        receipt.write_text(text, encoding='utf-8')
        row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                             lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
        assert row['status'] == 'FAIL' and row['error_count'] >= 1, text
        assert row['error'].startswith('RuntimeError: press exited 3'), text
    assert calls == []


@pytest.mark.parametrize('overrides, needle', [
    ({'completed_at': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}, 'predates this run'),
    ({'completed_at': '2026-01-01 00:00:00 UTC'}, 'predates this run'),
    ({'completed_at': ''}, 'no usable completed_at'),
    ({'completed_at': None}, 'no usable completed_at'),
    ({'completed_at': 'sometime yesterday'}, 'no usable completed_at'),
    ({'status': 'PASS', 'pending_body_urls': 0}, 'not a PARTIAL'),
    ({'status': 'PASS'}, 'not a PARTIAL'),
    ({'status': 'FAIL'}, 'not a PARTIAL'),
    ({'status': None}, 'not a PARTIAL'),
    ({'pending_body_urls': 0}, 'not a PARTIAL'),
    ({'pending_body_urls': -4}, 'not a PARTIAL'),
    ({'pending_body_urls': None}, 'not a PARTIAL'),
    ({'pending_body_urls': '2200'}, 'not a PARTIAL'),
    ({'pending_body_urls': 2200.0}, 'not a PARTIAL'),
    ({'pending_body_urls': True}, 'not a PARTIAL'),
])
def test_press_exit_three_needs_a_current_partial_receipt_with_a_backlog(tmp_path, monkeypatch, overrides, needle):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root, **overrides)
    calls = patch_inspect_run(monkeypatch, excused=True)
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
    # Exit 3 without proof is a FAIL, never a PARTIAL: it cannot advance the success anchor.
    assert row['returncode'] == 3 and row['status'] == 'FAIL', overrides
    assert row['error_count'] >= 1
    assert row['error'].startswith('RuntimeError: press exited 3') and needle in row['error']
    assert 'intake_excused' not in row.get('partial', {})
    assert calls == []


def test_press_exit_zero_reusing_a_partial_receipt_is_recorded_partial(tmp_path, monkeypatch):
    # current_source_run reuses a tombstone-excused same-day PARTIAL run and exits 0,
    # so the receipt (here older than the step) is the only place the gap is visible.
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root, completed_at=(datetime.now(timezone.utc) - timedelta(hours=6)).isoformat())
    calls = patch_inspect_run(monkeypatch, excused=True)
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: 0)
    assert row['returncode'] == 0 and row['status'] == 'PARTIAL'
    assert row['error_count'] == 0 and 'error' not in row
    assert row['partial'] == {'pending_body_urls': 2200, 'failed_bodies': 2578,
                              'status': 'PARTIAL', 'run_root': str(run_root),
                              'intake_excused': True, 'reused_same_day_receipt': True}
    assert calls == [run_root]


def test_a_reused_partial_press_run_reaches_the_receipt_and_runs_press_intake(tmp_path, monkeypatch):
    run_root = tmp_path / 'outputs/press_refresh_20260913'

    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    write_retry_receipt(run_root, completed_at=(datetime.now(timezone.utc) - timedelta(hours=6)).isoformat())
    patch_inspect_run(monkeypatch, excused=True)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [M.Step('crec', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: press_lane(run_root))
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [M.Step('derived', 'fixture')])
    executed = []
    mutex = threading.Lock()

    def runner(step, root, log, environment):
        log.write_text(step.name)
        with mutex:
            executed.append(step.name)
        return 0

    assert M.run(tmp_path, today=date(2026, 9, 13), runner=runner, preflight_check=lambda root: None) == 0
    assert {'press', 'press_intake', 'press_feed', 'press_baseline_fold'} <= set(executed)
    receipt = json.loads((tmp_path / M.RECEIPT).read_text())
    rows = {row['source']: row for row in receipt['sources']}
    assert rows['press']['status'] == 'PARTIAL' and rows['press']['returncode'] == 0
    assert receipt['partial'] is True and receipt['partial_sources'] == ['press']
    assert receipt['press_partial']['pending_body_urls'] == 2200
    assert receipt['press_partial']['intake_excused'] is True
    assert receipt['press_partial']['reused_same_day_receipt'] is True
    # An excused PARTIAL row still makes the terminal status PARTIAL: the gap is named, not hidden behind PASS.
    assert receipt['parked_steps'] == [] and receipt['status'] == 'PARTIAL' and receipt['error_count'] == 0


def test_an_unexcused_reused_partial_press_run_parks_the_lane(tmp_path, monkeypatch):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root)
    patch_inspect_run(monkeypatch, excused=False)
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {}, lambda step, root, log, environment: 0)
    assert row['status'] == 'PARTIAL' and row['partial']['reused_same_day_receipt'] is True
    assert row['partial']['intake_excused'] is False and M.parks_intake(row)


@pytest.mark.parametrize('text', [None, '{not json', '[1, 2]', '"PARTIAL"', '',
                                  '{"status": "PASS", "pending_body_urls": 0}'])
def test_press_exit_zero_without_a_partial_receipt_stays_pass(tmp_path, monkeypatch, text):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    calls = patch_inspect_run(monkeypatch, excused=True)
    if text is not None:
        run_root.mkdir(parents=True)
        (run_root / M.PRESS_RETRY_RECEIPT).write_text(text, encoding='utf-8')
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {}, lambda step, root, log, environment: 0)
    assert row['status'] == 'PASS' and row['error_count'] == 0 and 'partial' not in row
    assert calls == []


@pytest.mark.parametrize('code', [1, 2, 75])
def test_press_exit_codes_other_than_three_still_fail(tmp_path, code):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root)
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: code)
    assert row['status'] == 'FAIL' and row['returncode'] == code
    assert row['error_count'] == 1
    assert row['error'] == 'RuntimeError: source process exited %d' % code
    assert 'partial' not in row


@pytest.mark.parametrize('name', ['press_intake', 'press_feed', 'press_baseline_fold', 'all_intake'])
def test_exit_three_outside_the_press_collector_is_still_a_failure(tmp_path, name):
    row = M.execute_step(M.Step(name, 'unused.py'), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
    assert row['status'] == 'FAIL' and row['returncode'] == 3
    assert row['error_count'] == 1
    assert row['error'] == 'RuntimeError: source process exited 3'
    assert 'partial' not in row


def test_press_partial_closes_the_day_as_partial_while_naming_the_gap(tmp_path, monkeypatch, capsys):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    patch_inspect_run(monkeypatch, excused=False)
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [M.Step('db_one', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: [
        press_step(run_root), M.Step('press_intake', 'fixture'),
        M.Step('press_feed', 'fixture'), M.Step('press_baseline_fold', 'fixture')])
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [M.Step('derived', 'fixture')])
    executed = []
    mutex = threading.Lock()

    def runner(step, root, log, environment):
        log.write_text(step.name)
        with mutex:
            executed.append(step.name)
        return M.PRESS_PARTIAL_RETURNCODE if step.name == 'press' else 0

    code = M.run(tmp_path, today=date(2026, 9, 13), runner=runner, preflight_check=lambda root: None)
    assert code == 0
    # A PARTIAL press pass parks the steps that would hand the intake gate an
    # incomplete generation, and it does not hold back the derived lane the way
    # an error_count would.
    assert not {'press_intake', 'press_feed', 'press_baseline_fold'} & set(executed)
    assert executed[-1] == 'derived'
    receipt = json.loads((tmp_path / M.RECEIPT).read_text())
    assert receipt['parked_steps'] == ['press_intake', 'press_feed', 'press_baseline_fold']
    assert receipt['schema'] == 'daily-media-refresh-receipt-v1'
    assert receipt['status'] == 'PARTIAL' and receipt['error_count'] == 0
    assert receipt['partial'] is True and receipt['partial_sources'] == ['press']
    assert receipt['press_partial']['pending_body_urls'] == 2200
    assert receipt['press_partial']['failed_bodies'] == 2578
    assert receipt['press_partial']['status'] == 'PARTIAL'
    assert receipt['press_partial']['intake_excused'] is False
    assert [row['status'] for row in receipt['sources'] if row['source'] == 'press'] == ['PARTIAL']
    # The PARTIAL success anchor carries the same partial evidence, so no later run
    # can read it as a complete press generation.
    saved = json.loads((tmp_path / M.SUCCESS_RECEIPT).read_text())
    assert saved['status'] == 'PARTIAL'
    assert saved['local_date'] == '2026-09-13' and saved['partial_sources'] == ['press']
    out = capsys.readouterr().out
    assert re.search(r'press: PARTIAL \(\d+(\.\d+)?s\) pending_body_urls=2200 intake_excused=False', out)
    assert '"status": "PARTIAL"' in out   # the console summary line is as truthful as the receipt


# --- Smoke mode ---------------------------------------------------------


def test_smoke_collects_only_the_credential_free_source_subset():
    today = date(2026, 9, 13)
    full = M.source_steps(today)
    subset = M.smoke_sources(full)
    assert [step.name for step in subset] == ['crec', 'chrg', 'bluesky']
    # Every smoke source name really exists in the production source list.
    assert set(M.SMOKE_SOURCES) <= {step.name for step in full}
    # The slow browser-driven collectors are the ones left out.
    excluded = {step.name for step in full} - set(M.SMOKE_SOURCES)
    assert {'cnn', 'mtp', 'cbs', 'abc', 'pbs', 'npr', 'fox', 'msnow',
            'washington_journal'} == excluded
    # The subset keeps the real Step objects, arguments and validation kinds.
    assert [step.kind for step in subset] == ['congress_record', 'congress_record', 'bluesky']
    assert subset == [step for step in full if step.name in ('crec', 'chrg', 'bluesky')]


def test_smoke_skips_the_whole_press_lane_runs_derived_and_writes_no_success_anchor(tmp_path, monkeypatch):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    # Fixture sources named exactly like production, but of the default kind, so
    # this test measures lane selection rather than each collector's validator.
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [
        M.Step(name, 'fixture') for name in
        ('crec', 'chrg', 'cnn', 'mtp', 'cbs', 'abc', 'pbs', 'npr', 'fox', 'msnow',
         'washington_journal', 'bluesky')])
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [M.Step('derived', 'fixture')])
    executed = []
    mutex = threading.Lock()

    def runner(step, root, log, environment):
        log.write_text(step.name)
        with mutex:
            executed.append(step.name)
        return 0

    code = M.run(tmp_path, today=date(2026, 9, 13), runner=runner,
                 preflight_check=lambda root: None, smoke=True)
    assert code == 0
    # Nothing in the press lane was launched, including its three follow-ons.
    assert not {'press', 'press_intake', 'press_feed', 'press_baseline_fold'} & set(executed)
    assert set(executed) == {'crec', 'chrg', 'bluesky', 'derived'}
    receipt = json.loads((tmp_path / M.RECEIPT).read_text())
    assert receipt['status'] == 'PASS' and receipt['mode'] == 'smoke'
    assert receipt['error_count'] == 0
    rows = {row['source']: row['status'] for row in receipt['sources']}
    assert [rows[name] for name in ('press', 'press_intake', 'press_feed', 'press_baseline_fold')] \
        == ['SKIPPED_SMOKE'] * 4
    assert rows['crec'] == 'PASS' and rows['derived'] == 'PASS'
    # A skipped lane is neither a pass nor a partial, but the receipt still says
    # exactly what did not run.
    assert receipt['partial'] is False and receipt['partial_sources'] == []
    assert receipt['parked_steps'] == ['press', 'press_intake', 'press_feed', 'press_baseline_fold']
    # A subset collection must never become the catch-up anchor for source windows.
    assert not (tmp_path / M.SUCCESS_RECEIPT).exists()


def test_a_full_pass_still_writes_the_success_anchor(tmp_path, monkeypatch):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [M.Step('crec', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: [M.Step('press', 'fixture')])
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [M.Step('derived', 'fixture')])

    def runner(step, root, log, environment):
        log.write_text(step.name)
        return 0

    assert M.run(tmp_path, today=date(2026, 9, 13), runner=runner,
                 preflight_check=lambda root: None) == 0
    saved = json.loads((tmp_path / M.SUCCESS_RECEIPT).read_text())
    assert saved['mode'] == 'full' and saved['local_date'] == '2026-09-13'


# --- Parking the intake gate on an incomplete press generation ----------


def press_lane(run_root):
    return [press_step(run_root), M.Step('press_intake', 'audit_collection.py'),
            M.Step('press_feed', 'collect/build_press_engine_feed.py'),
            M.Step('press_baseline_fold', 'collect/fold_promoted_press_runs.py')]


def parked_world(tmp_path, monkeypatch, press_returncode):
    # The intake gate's verdict on an exit-3 press is the caller's to set first,
    # with patch_inspect_run; a complete press run (exit 0) never consults it.
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    if press_returncode == M.PRESS_PARTIAL_RETURNCODE:
        write_retry_receipt(run_root)
    else:
        # A complete run seals a PASS receipt; a PARTIAL one on exit 0 is the reuse case.
        write_retry_receipt(run_root, status='PASS', pending_body_urls=0)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [M.Step('crec', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: press_lane(run_root))
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [
        M.Step('all_intake', 'audit_collection.py'), M.Step('hearing_extract', 'fixture'),
        M.Step('social_master', 'fixture')])
    executed = []
    mutex = threading.Lock()

    def runner(step, root, log, environment):
        log.write_text(step.name)
        with mutex:
            executed.append(step.name)
        if step.name == 'press':
            return press_returncode
        return 0

    code = M.run(tmp_path, today=date(2026, 9, 13), runner=runner,
                 preflight_check=lambda root: None)
    return code, executed, json.loads((tmp_path / M.RECEIPT).read_text())


def test_partial_press_parks_the_intake_steps_instead_of_feeding_them(tmp_path, monkeypatch):
    calls = patch_inspect_run(monkeypatch, excused=False)
    code, executed, receipt = parked_world(tmp_path, monkeypatch, M.PRESS_PARTIAL_RETURNCODE)
    assert len(calls) == 1
    # Nothing that hands the intake gate this run's press directory was launched.
    assert 'press' in executed
    assert not {'press_intake', 'press_feed', 'press_baseline_fold', 'all_intake'} & set(executed)
    # Everything that does not touch the press generation still runs.
    assert {'crec', 'hearing_extract', 'social_master'} <= set(executed)
    rows = {row['source']: row for row in receipt['sources']}
    assert rows['press']['status'] == 'PARTIAL'
    assert rows['press']['partial']['intake_excused'] is False
    assert rows['press']['partial']['intake_refusal_reason'] == 'PENDING_BODY_ATTEMPTS'
    for name in ('press_intake', 'press_feed', 'press_baseline_fold', 'all_intake'):
        assert rows[name]['status'] == 'SKIPPED_PARTIAL'
        assert rows[name]['error_count'] == 0
        assert rows[name]['note'] == ('press generation incomplete (pending_body_urls=2200); '
                                      'intake deferred until a complete run')
    # A parked step is not an error, so the day still exits 0, but the PARTIAL press row makes it a PARTIAL that says so.
    assert code == 0
    assert receipt['status'] == 'PARTIAL' and receipt['error_count'] == 0
    assert receipt['partial'] is True and receipt['partial_sources'] == ['press']
    assert receipt['press_partial']['pending_body_urls'] == 2200
    assert receipt['parked_steps'] == ['press_intake', 'press_feed', 'press_baseline_fold', 'all_intake']


def test_a_complete_press_run_still_runs_every_intake_step_as_before(tmp_path, monkeypatch):
    code, executed, receipt = parked_world(tmp_path, monkeypatch, 0)
    assert {'press', 'press_intake', 'press_feed', 'press_baseline_fold',
            'all_intake', 'hearing_extract', 'social_master'} <= set(executed)
    assert code == 0 and receipt['status'] == 'PASS'
    assert receipt['partial'] is False and receipt['parked_steps'] == []
    assert all(row['status'] == 'PASS' for row in receipt['sources'])


def test_a_failing_press_run_still_stops_its_lane_without_parking(tmp_path, monkeypatch):
    code, executed, receipt = parked_world(tmp_path, monkeypatch, 1)
    assert 'press_intake' not in executed
    rows = {row['source']: row for row in receipt['sources']}
    assert rows['press']['status'] == 'FAIL'
    # A failure is not a park: the lane simply broke, and the day fails. What it
    # left unrun is still on the record, as skipped-after-failure, never as parked.
    skipped = ('press_intake', 'press_feed', 'press_baseline_fold', 'all_intake', 'hearing_extract', 'social_master')
    for name in skipped:
        assert rows[name]['status'] == 'SKIPPED_AFTER_FAILURE' and rows[name]['error_count'] == 0
        assert rows[name]['note'] == 'not run: press failed earlier in this run'
    assert receipt['parked_steps'] == list(skipped)
    assert receipt['partial'] is False and receipt['status'] == 'FAIL'
    assert receipt['error_count'] == 1
    assert code == 1
    assert 'all_intake' not in executed


def test_summarize_partials_reports_nothing_when_no_source_is_partial():
    state = {'sources': [{'source': 'press', 'status': 'PASS'}, {'source': 'crec', 'status': 'FAIL'}]}
    M.summarize_partials(state)
    assert state == {'sources': state['sources'], 'partial': False,
                     'partial_sources': [], 'press_partial': None, 'parked_steps': []}


# --- Excused PARTIAL: the intake gate accepts the generation -----------------


def test_excused_press_partial_keeps_the_lane_running_through_the_intake_steps(tmp_path, monkeypatch):
    calls = patch_inspect_run(monkeypatch, excused=True)
    code, executed, receipt = parked_world(tmp_path, monkeypatch, M.PRESS_PARTIAL_RETURNCODE)
    # The gate was asked about this run's own press directory, once.
    assert calls == [tmp_path / 'outputs/press_refresh_20260913']
    # Nothing is parked: the intake steps and the derived all_intake step all ran.
    assert {'press', 'press_intake', 'press_feed', 'press_baseline_fold', 'all_intake',
            'hearing_extract', 'social_master'} <= set(executed)
    assert executed.index('press_intake') > executed.index('press')
    rows = {row['source']: row for row in receipt['sources']}
    # The press row stays PARTIAL, so the receipt still names the gap.
    assert rows['press']['status'] == 'PARTIAL' and rows['press']['partial']['intake_excused'] is True
    for name in ('press_intake', 'press_feed', 'press_baseline_fold', 'all_intake'):
        assert rows[name]['status'] == 'PASS'
    assert code == 0 and receipt['status'] == 'PARTIAL' and receipt['error_count'] == 0
    assert receipt['partial'] is True and receipt['partial_sources'] == ['press']
    assert receipt['press_partial']['intake_excused'] is True
    assert receipt['press_partial']['pending_body_urls'] == 2200
    assert receipt['parked_steps'] == []


def test_unexcused_press_partial_parks_the_lane_and_the_derived_intake_step(tmp_path, monkeypatch):
    patch_inspect_run(monkeypatch, excused=False)
    code, executed, receipt = parked_world(tmp_path, monkeypatch, M.PRESS_PARTIAL_RETURNCODE)
    assert not {'press_intake', 'press_feed', 'press_baseline_fold', 'all_intake'} & set(executed)
    assert {'hearing_extract', 'social_master'} <= set(executed)
    assert receipt['parked_steps'] == ['press_intake', 'press_feed', 'press_baseline_fold', 'all_intake']
    assert receipt['partial_sources'] == ['press'] and code == 0


def write_sealed_partial(run_root, pairs):
    """A run sealed the way the walk seals a PARTIAL: required files, a pending artifact, a hash-bound receipt."""
    run_root = Path(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    for name in ('press_delta_fulltext.csv', 'press_delta_fulltext.rds', 'press_delta_host_articles.csv',
                 'press_delta_article_mentions.csv', 'press_delta_scored.csv'):
        (run_root / name).write_text('url\n', encoding='utf-8')
    (run_root / 'press_delta_pending.csv').write_text(
        'bioguide,url,date,collection_status\n'
        + ''.join('%s,%s,,body_pending_network_attempt\n' % pair for pair in pairs), encoding='utf-8')

    def digest(name):
        return hashlib.sha256((run_root / name).read_bytes()).hexdigest()

    return write_retry_receipt(
        run_root, schema_version=0, pending_body_urls=len(pairs),
        pending_body_urls_sha256=digest('press_delta_pending.csv'),
        fulltext_csv_sha256=digest('press_delta_fulltext.csv'),
        fulltext_rds_sha256=digest('press_delta_fulltext.rds'),
        completed_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0).isoformat())


def test_a_real_sealed_partial_is_accepted_by_the_gate_and_not_parked(tmp_path, monkeypatch):
    # No inspect_run stand-in: the intake gate's own run test reads real sealed files. Owner
    # ruling 2026-10-15: a sealed PARTIAL carries its pending URLs to the next run, so it is
    # promoted -- the lane is not parked and nothing waits for a tombstone streak.
    from collect import press_pending_tombstones as tombstones
    monkeypatch.setattr(tombstones, 'DEFAULT_TOMBSTONES', str(tmp_path / 'tombstones.json'))
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_sealed_partial(run_root, [('X000001', 'https://example.gov/a'),
                                    ('X000002', 'https://example.gov/b')])
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
    assert row['status'] == 'PARTIAL' and row['error_count'] == 0
    assert row['partial']['intake_excused'] is True and row['partial']['pending_body_urls'] == 2
    assert not M.parks_intake(row)


def test_a_sealed_partial_that_repeats_a_pending_key_is_refused_and_parks(tmp_path, monkeypatch):
    from collect import press_pending_tombstones as tombstones
    monkeypatch.setattr(tombstones, 'DEFAULT_TOMBSTONES', str(tmp_path / 'tombstones.json'))
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_sealed_partial(run_root, [('X000001', 'https://example.gov/a'),
                                    ('X000001', 'https://example.gov/a/')])
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
    assert row['status'] == 'PARTIAL' and row['partial']['intake_excused'] is False
    assert row['partial']['intake_refusal_reason'] == 'PENDING_BODY_ATTEMPTS'
    assert M.parks_intake(row)


# --- PARTIAL is a truthful, non-fatal, anchoring terminal status (DE-020) ---


def test_an_unexcused_partial_run_exits_zero_anchors_and_the_next_run_reads_that_anchor(tmp_path, monkeypatch):
    patch_inspect_run(monkeypatch, excused=False)
    code, executed, receipt = parked_world(tmp_path, monkeypatch, M.PRESS_PARTIAL_RETURNCODE)
    assert code == 0 and receipt['status'] == 'PARTIAL' and receipt['error_count'] == 0
    anchor = json.loads((tmp_path / M.SUCCESS_RECEIPT).read_text())
    assert anchor['schema'] == M.SCHEMA and anchor['status'] == 'PARTIAL'
    assert anchor['local_date'] == '2026-09-13' and anchor['partial_sources'] == ['press']
    # The reader accepts a PARTIAL anchor, so the next run's source windows start from it.
    assert M.successful_collection_date(tmp_path, date(2026, 9, 14)) == date(2026, 9, 13)
    seen = []
    monkeypatch.setattr(M, 'source_steps',
                        lambda today, last_success: seen.append(last_success) or [M.Step('crec', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: [])

    def runner(step, root, log, environment):
        log.write_text(step.name)
        return 0

    assert M.run(tmp_path, today=date(2026, 9, 14), runner=runner, preflight_check=lambda root: None) == 0
    assert seen == [date(2026, 9, 13)]
    follow_up = json.loads((tmp_path / M.RECEIPT).read_text())
    assert follow_up['status'] == 'PASS' and follow_up['last_successful_collection_date'] == '2026-09-13'


@pytest.mark.parametrize('status', ['FAIL', 'RUNNING', 'SKIPPED_SMOKE', None])
def test_the_catch_up_anchor_still_refuses_a_receipt_that_is_not_a_success(tmp_path, status):
    M.write_json(tmp_path / M.SUCCESS_RECEIPT, {'schema': M.SCHEMA, 'status': status, 'local_date': '2026-09-01'})
    with pytest.raises(RuntimeError, match='not a successful collection receipt'):
        M.successful_collection_date(tmp_path, date(2026, 9, 13))


def test_a_failed_source_beside_a_partial_press_is_a_fail_and_never_a_partial(tmp_path, monkeypatch):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    patch_inspect_run(monkeypatch, excused=False)
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [M.Step('crec', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: [press_step(run_root)])
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [M.Step('derived', 'fixture')])

    def runner(step, root, log, environment):
        log.write_text(step.name)
        return {'crec': 2, 'press': M.PRESS_PARTIAL_RETURNCODE}.get(step.name, 0)

    code = M.run(tmp_path, today=date(2026, 9, 13), runner=runner, preflight_check=lambda root: None)
    receipt = json.loads((tmp_path / M.RECEIPT).read_text())
    assert code == 1 and receipt['status'] == 'FAIL' and receipt['error_count'] >= 1
    assert receipt['partial'] is True and receipt['partial_sources'] == ['press']
    # A failed run never becomes the catch-up anchor, partial or not.
    assert not (tmp_path / M.SUCCESS_RECEIPT).exists()


def test_parks_intake_is_true_only_for_an_unexcused_partial_or_a_smoke_skip():
    assert M.parks_intake({'status': 'PARTIAL', 'partial': {'intake_excused': False}})
    assert M.parks_intake({'status': 'PARTIAL', 'partial': {}})          # no verdict recorded: stay parked
    assert M.parks_intake({'status': 'PARTIAL'})
    assert M.parks_intake({'status': 'SKIPPED_SMOKE'})
    assert not M.parks_intake({'status': 'PARTIAL', 'partial': {'intake_excused': True}})
    assert not M.parks_intake({'status': 'PASS'})
    assert not M.parks_intake({'status': 'FAIL'})


# --- Collector log errors: terminal statuses and the step's own keys ----------


def test_changed_log_errors_counts_only_the_steps_own_key_prefix():
    stamp = ('err:timeout', 'now')
    after = {'cnn:2026-09-10': stamp, 'cnnseg:abc': stamp, 'npr:1': stamp, 'nprfeed:1': stamp,
             'aei:7': stamp, 'antag2:1': stamp, 'cfr:9': stamp, 'hh:3': stamp, 'fox': stamp}

    def keys(step):
        return sorted(error['key'] for error in M.changed_log_errors({}, after, step))

    assert keys(M.Step('cnn', 'x.py', kind='interview')) == ['cnn:2026-09-10', 'cnnseg:abc']
    assert keys(M.Step('npr', 'x.py', kind='interview')) == ['npr:1', 'nprfeed:1']
    assert keys(M.Step('fox', 'x.py', kind='interview')) == ['fox']
    # Supervisor legs belong to nobody in this run, and no step name prefixes them.
    for name in ('abc', 'cbs', 'mtp', 'pbs', 'msnow', 'crec', 'chrg'):
        assert keys(M.Step(name, 'x.py')) == []
    assert keys(M.Step('cnn', 'x.py')) != keys(M.Step('npr', 'x.py'))
    # A bare step name works too, and omitting the step keeps the unfiltered behaviour.
    assert [error['key'] for error in M.changed_log_errors({}, after, 'aei')] == ['aei:7']
    assert len(M.changed_log_errors({}, after)) == len(after)


def test_a_supervisor_leg_error_in_the_shared_log_is_not_charged_to_the_collector(tmp_path):
    database = tmp_path / 'social_media.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE iv_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)')
    (tmp_path / 'collector_sentinels').mkdir()
    step = M.Step('cnn', 'members/fetch_interviews.py', ('cnn', '--strict'), kind='interview')

    def runner_for(*rows):
        def runner(step, root, log, environment):
            log.write_text('done')
            with sqlite3.connect(database) as connection:
                connection.executemany('INSERT INTO iv_fetch_log VALUES(?,?,?)', rows)
            (root / 'collector_sentinels' / 'cnn.done').write_text('done')
            return 0
        return runner

    clean = M.execute_step(step, tmp_path, tmp_path, {}, runner_for(
        ('cnn:2026-09-10', 'ok', 'new'), ('aei:7', 'err:supervisor', 'new'), ('antag:1', 'err:other', 'new')))
    assert clean['status'] == 'PASS' and clean['error_count'] == 0 and clean['item_errors'] == []
    broken = M.execute_step(step, tmp_path, tmp_path, {}, runner_for(
        ('cnnseg:2026-09-11', 'err:http', 'new'), ('asf:7', 'err:supervisor', 'new')))
    assert broken['status'] == 'FAIL' and broken['error_count'] == 1
    assert broken['item_errors'] == [{'key': 'cnnseg:2026-09-11', 'status': 'err:http'}]


@pytest.mark.parametrize('status', ['noqa', 'NOQA', 'NoQa', 'OK', 'Empty', 'NOPAGE', 'Gone', 'NoEp', 'NoSession'])
def test_terminal_log_statuses_are_compared_lowercased(status):
    assert M.changed_log_errors({}, {'chrg:CHRG-119hhrg1': (status, 'today')}) == []
    assert M.changed_log_errors({}, {'chrg:CHRG-119hhrg1': (status, 'today')}, M.Step('chrg', 'x.py')) == []


def test_noqa_is_terminal_and_an_unknown_status_still_is_not():
    assert 'noqa' in M.TERMINAL_LOG_STATUSES
    assert M.TERMINAL_LOG_STATUSES == {'ok', 'nosession', 'noep', 'empty', 'nopage', 'gone', 'noqa'}
    assert M.changed_log_errors({}, {'chrg:x': ('err:timeout', 't'), 'chrg:y': ('NOQA', 't')}) \
        == [{'key': 'chrg:x', 'status': 'err:timeout'}]


def test_crec_day_coverage_accepts_any_terminal_status_in_any_case(tmp_path):
    with sqlite3.connect(tmp_path / 'social_media.db') as connection:
        connection.execute('CREATE TABLE cr_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)')
        connection.executemany('INSERT INTO cr_fetch_log VALUES(?,?,?)', [
            ('crec:CREC-2026-09-09', 'OK', 'old'), ('crec:CREC-2026-09-10', 'NoSession', 'old'),
            ('crec:CREC-2026-09-11', 'empty', 'old'), ('crec:CREC-2026-09-12', 'NOPAGE', 'old'),
            ('crec:CREC-2026-09-13', 'err:timeout', 'old')])
    step = M.Step('crec', 'unused.py', ('crec', '--since', '2026-09-09', '--until', '2026-09-14'), kind='congress_record')
    result = M.validate_source(step, tmp_path, time.time(), {})
    # Only the errored day and the day with no checkpoint at all remain.
    assert result['item_errors'] == [{'key': 'crec:CREC-2026-09-13', 'status': 'err:timeout'},
                                     {'key': 'crec:CREC-2026-09-14', 'status': 'MISSING'}]


# --- Unknown pre-run snapshot, naive timestamps ------------------------------


def test_an_unknown_pre_run_snapshot_does_not_charge_history_to_the_step(tmp_path, monkeypatch):
    historical = {'chrg:CHRG-old1': ('err:timeout', 'long ago'), 'chrg:CHRG-old2': ('err:http', 'long ago')}
    calls = []

    def snapshot(root, table):
        calls.append(table)
        if len(calls) == 1:
            raise sqlite3.OperationalError('database is locked')
        return historical

    monkeypatch.setattr(M, 'fetch_log_snapshot', snapshot)
    ran = []
    step = M.Step('chrg', 'unused.py', kind='congress_record')
    row = M.execute_step(step, tmp_path, tmp_path, {}, lambda *args: ran.append(args) or 0)
    assert ran == []                      # the collector was never launched
    assert row['status'] == 'FAIL' and row['error_count'] == 1
    assert row['error'] == 'OperationalError: database is locked'
    # Diffing against an empty baseline would have charged both historical keys.
    assert 'item_errors' not in row
    assert calls == ['cr_fetch_log']      # the after-snapshot was not even taken
    assert 'log diff skipped' in row['diagnostic_error']


def test_unknown_snapshot_still_reads_the_current_source_receipt(tmp_path):
    step = M.Step('bluesky', 'unused.py', ('--since', '2026-08-30'), kind='bluesky')
    M.write_json(tmp_path / M.BLUESKY_RECEIPT, {
        'schema': 'bluesky-source-receipt-v1', 'status': 'FAIL',
        'completed_at': datetime.now(timezone.utc).isoformat(), 'error_count': 3})
    result = M.failure_diagnostics(step, tmp_path, time.time(), None)
    assert result['error_count'] == 3 and result['source_status'] == 'FAIL'
    assert 'diagnostic_error' not in result       # a receipt-only kind has no log diff to skip


def test_a_known_snapshot_charges_only_what_the_run_changed(tmp_path):
    with sqlite3.connect(tmp_path / 'social_media.db') as connection:
        connection.execute('CREATE TABLE cr_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)')
        connection.execute('INSERT INTO cr_fetch_log VALUES(?,?,?)', ('chrg:CHRG-old', 'err:old', 'long ago'))
    step = M.Step('chrg', 'unused.py', kind='congress_record')

    def runner(step, root, log, environment):
        with sqlite3.connect(root / 'social_media.db') as connection:
            connection.execute('INSERT INTO cr_fetch_log VALUES(?,?,?)', ('chrg:CHRG-new', 'err:http', 'now'))
        return 1

    row = M.execute_step(step, tmp_path, tmp_path, {}, runner)
    assert row['status'] == 'FAIL' and row['error_count'] == 1
    assert row['item_errors'] == [{'key': 'chrg:CHRG-new', 'status': 'err:http'}]
    assert 'diagnostic_error' not in row


def test_a_naive_completed_at_is_read_as_utc_at_both_receipt_sites(tmp_path):
    assert M.completed_at_utc({'completed_at': '2026-09-13T10:00:00'}) == datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    assert M.completed_at_utc({'completed_at': '2026-09-13T10:00:00Z'}) == datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    assert M.completed_at_utc({'completed_at': '2026-09-13T12:00:00+02:00'}) == datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    # require_receipt site
    receipt = tmp_path / 'source.json'
    M.write_json(receipt, {'schema': 's', 'status': 'COMPLETE', 'completed_at': naive})
    assert M.require_receipt(receipt, 's', time.time())['status'] == 'COMPLETE'
    # failure_diagnostics site
    step = M.Step('bluesky', 'unused.py', kind='bluesky')
    M.write_json(tmp_path / M.BLUESKY_RECEIPT, {'schema': 'bluesky-source-receipt-v1', 'status': 'FAIL',
                                                'completed_at': naive, 'error_count': 2})
    assert M.failure_diagnostics(step, tmp_path, time.time(), {})['error_count'] == 2


# --- Skipped-after-failure rows ----------------------------------------------


def test_a_failed_derived_step_records_the_rest_of_the_derived_lane_as_skipped(tmp_path, monkeypatch):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [M.Step('crec', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: [])
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [
        M.Step('first', 'fixture'), M.Step('second', 'fixture', kind='stance'), M.Step('third', 'fixture')])
    executed = []

    def runner(step, root, log, environment):
        log.write_text(step.name)
        executed.append(step.name)
        return 1 if step.name == 'first' else 0

    assert M.run(tmp_path, today=date(2026, 9, 13), runner=runner, preflight_check=lambda root: None) == 1
    assert executed == ['crec', 'first']
    receipt = json.loads((tmp_path / M.RECEIPT).read_text())
    rows = {row['source']: row for row in receipt['sources']}
    for name in ('second', 'third'):
        assert rows[name]['status'] == 'SKIPPED_AFTER_FAILURE' and rows[name]['error_count'] == 0
        assert rows[name]['note'] == 'not run: first failed earlier in this run'
    assert rows['second']['kind'] == 'stance'
    assert receipt['parked_steps'] == ['second', 'third']
    assert receipt['status'] == 'FAIL' and receipt['error_count'] == 1


def test_summarize_partials_counts_skipped_after_failure_as_parked():
    state = {'sources': [{'source': 'a', 'status': 'FAIL'}, {'source': 'b', 'status': 'SKIPPED_AFTER_FAILURE'},
                         {'source': 'c', 'status': 'SKIPPED_PARTIAL'}, {'source': 'd', 'status': 'SKIPPED_SMOKE'},
                         {'source': 'e', 'status': 'PASS'}]}
    M.summarize_partials(state)
    assert state['parked_steps'] == ['b', 'c', 'd'] and state['partial'] is False


# --- Interrupts: children, the stop flag, the receipt ---------------------------


class FakeChild:
    """A collector child that is still running; it has no real process behind it."""
    pid = 4242

    def poll(self):
        return None


def test_terminate_tree_is_bounded_when_taskkill_and_the_wait_both_hang(monkeypatch):
    class Stuck:
        pid = 4242
        killed = False

        def wait(self, timeout=None):
            assert timeout == 60                                  # never an unbounded wait
            raise subprocess.TimeoutExpired('stuck', timeout)

        def kill(self):
            self.killed = True

    seen = {}

    def hung_run(*args, **kwargs):
        seen['timeout'] = kwargs.get('timeout')
        raise subprocess.TimeoutExpired(args[0], kwargs.get('timeout'))

    monkeypatch.setattr(M.subprocess, 'run', hung_run)
    monkeypatch.setattr(M.os, 'killpg', lambda pid, sig: None, raising=False)
    process = Stuck()
    M.terminate_tree(process)                                     # returns instead of hanging
    assert process.killed
    if os.name == 'nt':
        assert seen['timeout'] == 60


def test_run_process_registers_the_child_while_it_runs_and_drops_it_afterwards(tmp_path, monkeypatch):
    seen = []

    class FakePopen:
        pid = 4321

        def __init__(self, argv, **kwargs):
            self.argv = argv

        def wait(self, timeout=None):
            seen.append([process for process, spec in M._LIVE])
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(M.subprocess, 'Popen', FakePopen)
    monkeypatch.setattr(M.compute_engine, 'launch',
                        lambda *args, **kwargs: M.compute_engine.Launch(['fixture'], None, None, 'collect'))
    assert M.run_process(M.Step('x', 'unused.py'), tmp_path, tmp_path / 'x.log', {}) == 0
    assert len(seen) == 1 and len(seen[0]) == 1 and isinstance(seen[0][0], FakePopen)
    assert M._LIVE == []


@pytest.mark.parametrize('stopped', [False, True])
def test_run_process_terminates_and_unregisters_when_interrupted(tmp_path, monkeypatch, stopped):
    terminated = []

    class FakePopen:
        pid = 4321

        def __init__(self, argv, **kwargs):
            pass

        def wait(self, timeout=None):
            raise KeyboardInterrupt

        def poll(self):
            return None

    monkeypatch.setattr(M.subprocess, 'Popen', FakePopen)
    monkeypatch.setattr(M.compute_engine, 'launch',
                        lambda *args, **kwargs: M.compute_engine.Launch(['fixture'], None, None, 'collect'))
    monkeypatch.setattr(M.compute_engine, 'reap', lambda spec: None)
    monkeypatch.setattr(M, 'terminate_tree', terminated.append)
    if stopped:
        M.STOP.set()          # the interrupt began before this child was registered
    with pytest.raises(RuntimeError if stopped else KeyboardInterrupt):
        M.run_process(M.Step('x', 'unused.py'), tmp_path, tmp_path / 'x.log', {})
    assert len(terminated) == 1 and isinstance(terminated[0], FakePopen)
    assert M._LIVE == []


def test_terminate_live_processes_reaches_every_child_even_when_one_fails(monkeypatch):
    first, second = FakeChild(), FakeChild()
    reaped = []
    M.register_process(first, 'spec-1')
    M.register_process(second, 'spec-2')
    killed = []

    def terminate(process):
        killed.append(process)
        if process is first:
            raise OSError('access denied')

    monkeypatch.setattr(M, 'terminate_tree', terminate)
    monkeypatch.setattr(M.compute_engine, 'reap', reaped.append)
    M.terminate_live_processes()
    assert killed == [first, second] and reaped == ['spec-2']
    M.unregister_process(first)
    assert [process for process, spec in M._LIVE] == [second]


def test_an_interrupt_in_the_main_thread_kills_live_children_stops_the_lanes_and_fails_the_receipt(tmp_path, monkeypatch):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [M.Step('db_one', 'fixture'), M.Step('db_two', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: [])
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [M.Step('derived', 'fixture')])
    child, killed, executed = FakeChild(), [], []
    registered = threading.Event()
    monkeypatch.setattr(M, 'terminate_tree', killed.append)
    monkeypatch.setattr(M.compute_engine, 'reap', lambda spec: None)

    class InterruptingPool(ThreadPoolExecutor):
        """Ctrl-C arrives in the main thread, at future.result(), once the collector is up."""
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)

            def result(timeout=None):
                assert registered.wait(10)
                raise KeyboardInterrupt
            future.result = result
            return future

    monkeypatch.setattr(M, 'ThreadPoolExecutor', InterruptingPool)

    def runner(step, root, log, environment):
        log.write_text(step.name)
        executed.append(step.name)
        M.register_process(child, None)
        registered.set()
        try:
            # A long collector: it only ends once the interrupt handler has stopped the run.
            assert M.STOP.wait(10), 'the interrupt handler never set the stop flag'
        finally:
            M.unregister_process(child)
        return 0

    with pytest.raises(KeyboardInterrupt):
        M.run(tmp_path, today=date(2026, 9, 13), runner=runner, preflight_check=lambda root: None)
    assert killed == [child]                       # the live tree was terminated
    assert executed == ['db_one']                  # the lane launched nothing after the interrupt
    receipt = json.loads((tmp_path / M.RECEIPT).read_text())
    assert receipt['status'] == 'FAIL' and receipt['error_count'] >= 1
    assert receipt['error'].startswith('KeyboardInterrupt') and 'completed_at' in receipt
    saved = list((tmp_path / 'audits/daily_media_refresh').glob('*/receipt.json'))
    assert len(saved) == 1 and json.loads(saved[0].read_text())['status'] == 'FAIL'
    assert not (tmp_path / M.SUCCESS_RECEIPT).exists()


def test_an_ordinary_exception_still_returns_a_fail_code_instead_of_raising(tmp_path, monkeypatch):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)

    def failing_preflight(root):
        raise RuntimeError('required existing media input is missing: social_media.db')

    assert M.run(tmp_path, today=date(2026, 9, 13), runner=lambda *args: 0, preflight_check=failing_preflight) == 1
    receipt = json.loads((tmp_path / M.RECEIPT).read_text())
    assert receipt['status'] == 'FAIL' and 'social_media.db' in receipt['error']
    assert not (tmp_path / M.SUCCESS_RECEIPT).exists()


def test_a_stop_flag_left_over_from_an_interrupted_run_does_not_block_the_next(tmp_path, monkeypatch):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    monkeypatch.setattr(M, 'source_steps', lambda today, last_success: [M.Step('crec', 'fixture')])
    monkeypatch.setattr(M, 'press_steps', lambda root, today: [])
    monkeypatch.setattr(M, 'derived_steps', lambda root, today: [M.Step('derived', 'fixture')])
    M.STOP.set()
    executed = []

    def runner(step, root, log, environment):
        log.write_text(step.name)
        executed.append(step.name)
        return 0

    assert M.run(tmp_path, today=date(2026, 9, 13), runner=runner, preflight_check=lambda root: None) == 0
    assert executed == ['crec', 'derived']

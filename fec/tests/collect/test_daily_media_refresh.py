from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from collect import daily_media_refresh as M


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
    assert press[0].timeout == 129600
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
    assert all('command' in row and 'log_sha256' in row for row in receipt['sources'])
    saved = json.loads((tmp_path / M.SUCCESS_RECEIPT).read_text())
    assert saved['local_date'] == ('2026-09-01' if source_failed else '2026-09-13')
    assert receipt['last_successful_collection_date'] == '2026-09-01'
    assert set(receipt['effective_windows']) == {'db_one', 'db_two', 'press', 'press_accept'}
    assert receipt['partial'] is False and receipt['partial_sources'] == []
    assert receipt['press_partial'] is None


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
    document = {'schema_version': 2, 'mode': 'live_network_retry', 'status': 'PARTIAL',
                'pending_body_urls': 2200, 'failed_bodies': 2578}
    document.update(overrides)
    M.write_json(Path(run_root) / M.PRESS_RETRY_RECEIPT, document)
    return document


def test_press_budgeted_partial_is_recorded_and_never_counted_as_a_failure(tmp_path):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root)

    def runner(step, root, log, environment):
        log.write_text('status=PARTIAL pending_body_urls=2200')
        return M.PRESS_PARTIAL_RETURNCODE

    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {}, runner)
    assert row['returncode'] == 3 and row['status'] == 'PARTIAL'
    assert row['error_count'] == 0 and 'error' not in row
    assert row['partial'] == {'pending_body_urls': 2200, 'failed_bodies': 2578,
                              'status': 'PARTIAL', 'run_root': str(run_root)}


def test_press_partial_without_a_retry_receipt_still_records_the_absence(tmp_path):
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    row = M.execute_step(press_step(run_root), tmp_path, tmp_path, {},
                         lambda step, root, log, environment: M.PRESS_PARTIAL_RETURNCODE)
    assert row['status'] == 'PARTIAL' and row['error_count'] == 0
    assert row['partial'] == {'note': 'retry receipt absent', 'run_root': str(run_root)}


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


def test_press_partial_passes_the_day_while_naming_the_gap(tmp_path, monkeypatch, capsys):
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
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
    assert receipt['status'] == 'PASS' and receipt['error_count'] == 0
    assert receipt['partial'] is True and receipt['partial_sources'] == ['press']
    assert receipt['press_partial']['pending_body_urls'] == 2200
    assert receipt['press_partial']['failed_bodies'] == 2578
    assert receipt['press_partial']['status'] == 'PARTIAL'
    assert [row['status'] for row in receipt['sources'] if row['source'] == 'press'] == ['PARTIAL']
    # The PASS success anchor carries the same partial evidence, so no later run
    # can read it as a complete press generation.
    saved = json.loads((tmp_path / M.SUCCESS_RECEIPT).read_text())
    assert saved['local_date'] == '2026-09-13' and saved['partial_sources'] == ['press']
    assert re.search(r'press: PARTIAL \(\d+(\.\d+)?s\) pending_body_urls=2200', capsys.readouterr().out)


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
    @contextmanager
    def lock(*args, **kwargs):
        yield {'pid': 17, 'token': 'lease', 'adopted': False}
    monkeypatch.setattr(M, 'pipeline_run_lock', lock)
    run_root = tmp_path / 'outputs/press_refresh_20260913'
    write_retry_receipt(run_root)
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
    code, executed, receipt = parked_world(tmp_path, monkeypatch, M.PRESS_PARTIAL_RETURNCODE)
    # Nothing that hands the intake gate this run's press directory was launched.
    assert 'press' in executed
    assert not {'press_intake', 'press_feed', 'press_baseline_fold', 'all_intake'} & set(executed)
    # Everything that does not touch the press generation still runs.
    assert {'crec', 'hearing_extract', 'social_master'} <= set(executed)
    rows = {row['source']: row for row in receipt['sources']}
    assert rows['press']['status'] == 'PARTIAL'
    for name in ('press_intake', 'press_feed', 'press_baseline_fold', 'all_intake'):
        assert rows[name]['status'] == 'SKIPPED_PARTIAL'
        assert rows[name]['error_count'] == 0
        assert rows[name]['note'] == ('press generation incomplete (pending_body_urls=2200); '
                                      'intake deferred until a complete run')
    # A parked step is not an error, so the day is still a PASS that says so.
    assert code == 0
    assert receipt['status'] == 'PASS' and receipt['error_count'] == 0
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
    # A failure is not a park: the lane simply broke, and the day fails.
    assert 'press_intake' not in rows
    assert receipt['parked_steps'] == [] and receipt['status'] == 'FAIL'
    assert code == 1
    assert 'all_intake' not in executed


def test_summarize_partials_reports_nothing_when_no_source_is_partial():
    state = {'sources': [{'source': 'press', 'status': 'PASS'}, {'source': 'crec', 'status': 'FAIL'}]}
    M.summarize_partials(state)
    assert state == {'sources': state['sources'], 'partial': False,
                     'partial_sources': [], 'press_partial': None, 'parked_steps': []}

"""Native daily triggers are isolated from all live collectors and schedulers."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import daily_refresh as daily

DAY = '2026-09-13'


def test_windowless_entry_uses_console_python_for_captured_worker_logs(tmp_path, monkeypatch):
    gui = tmp_path / 'pythonw.exe'
    console = tmp_path / 'python.exe'
    gui.touch()
    console.touch()
    monkeypatch.setattr(daily.sys, 'executable', str(gui))
    assert daily.worker_python() == str(console)


def test_windowless_entry_refuses_missing_console_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(daily.sys, 'executable', str(tmp_path / 'pythonw.exe'))
    with pytest.raises(RuntimeError, match='canonical Python worker'):
        daily.worker_python()


@pytest.fixture(autouse=True)
def local_calendar(monkeypatch):
    monkeypatch.setattr(daily, 'local_day', lambda: DAY)


def state(root):
    return daily.read(root / daily.STATE)


def day(root, date=DAY):
    return state(root)['days'][date]


def complete(root, attempt_id, pid=8123, **changes):
    directory = root / 'audits/congress_pipeline' / attempt_id
    directory.mkdir(parents=True, exist_ok=True)
    media = {'schema': 'daily-media-refresh-receipt-v1', 'status': 'PASS'}
    daily.write(root / daily.MEDIA_RECEIPT, media)
    daily.write(directory / 'daily_media_refresh_receipt.json', media)
    receipt = {'schema': 'congress-pipeline-status-v1', 'status': 'PASS', 'include_media': True,
               'daily_attempt_id': attempt_id, 'owner_pid': pid, 'run_dir': str(directory),
               'started_at': daily.utc_now(), 'completed_at': daily.utc_now(),
               'artifacts': {daily.MEDIA_RECEIPT: {'sha256': daily.digest(root / daily.MEDIA_RECEIPT)}}}
    receipt.update(changes)
    daily.write(directory / 'receipt.json', receipt)
    daily.write(root / daily.WRAPPER_RECEIPT, receipt)
    daily.write(root / daily.WRAPPER_STATUS, receipt)
    return receipt


class Finished:
    pid = 8123

    def __init__(self, returncode=0):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def fake_launch(root, monkeypatch, *, returncode=0, publish=True, changes=None):
    calls = []

    def launch(command, **kwargs):
        calls.append((command, kwargs))
        assert (root / '.daily_refresh.lock').exists()
        assert not (root / '.pipeline_run.lock').exists()
        env = kwargs['env']
        assert 'MELANGE_PIPELINE_LOCK_TOKEN' not in env
        assert 'MELANGE_PIPELINE_LOCK_OWNER_PID' not in env
        assert command[-1] == '--include-media'
        assert kwargs['stdin'] == subprocess.DEVNULL
        assert env['MONEYBALL_ROOT'] == str(root)
        assert kwargs['stdout'].name.endswith('pipeline.log')
        if publish:
            complete(root, env[daily.ATTEMPT_ENV], **(changes or {}))
        return Finished(returncode)

    monkeypatch.setattr(daily.subprocess, 'Popen', launch)
    return calls


def test_retry_cannot_observe_or_initiate_a_new_calendar_day(tmp_path, monkeypatch):
    monkeypatch.setattr(daily.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    assert daily.run(tmp_path, trigger='retry') == 0
    assert not (tmp_path / daily.STATE).exists()
    assert not (tmp_path / daily.OBSERVATIONS).exists()


@pytest.mark.parametrize('trigger', ['first-use', 'manual'])
def test_first_observation_runs_full_pipeline_and_success_is_once_per_day(tmp_path, monkeypatch, trigger):
    monkeypatch.setenv('MELANGE_PIPELINE_LOCK_TOKEN', 'not-a-daily-lease')
    monkeypatch.setenv('MELANGE_PIPELINE_LOCK_OWNER_PID', '123')
    calls = fake_launch(tmp_path, monkeypatch)
    assert daily.run(tmp_path, trigger=trigger) == 0
    success = day(tmp_path)['success']
    assert success['include_media'] is True and success['owner_pid'] == Finished.pid
    assert day(tmp_path)['status'] == 'PASS'
    assert not (tmp_path / '.daily_refresh.lock').exists()
    for repeated in ('first-use', 'retry', 'manual'):
        assert daily.run(tmp_path, trigger=repeated) == 0
    assert len(calls) == 1
    assert day(tmp_path)['success'] == success


def test_new_day_requires_its_own_first_use_then_runs_once(tmp_path, monkeypatch):
    calls = fake_launch(tmp_path, monkeypatch)
    assert daily.run(tmp_path, trigger='first-use') == 0
    monkeypatch.setattr(daily, 'local_day', lambda: '2026-09-14')
    assert daily.run(tmp_path, trigger='retry') == 0
    assert len(calls) == 1 and '2026-09-14' not in state(tmp_path)['days']
    assert daily.run(tmp_path, trigger='first-use') == 0
    assert len(calls) == 2
    assert state(tmp_path)['days'][DAY]['success']
    assert state(tmp_path)['days']['2026-09-14']['success']


def test_crossing_midnight_completes_only_the_observed_start_day(tmp_path, monkeypatch):
    def launch(command, **kwargs):
        complete(tmp_path, kwargs['env'][daily.ATTEMPT_ENV])
        monkeypatch.setattr(daily, 'local_day', lambda: '2026-09-14')
        return Finished()

    monkeypatch.setattr(daily.subprocess, 'Popen', launch)
    assert daily.run(tmp_path, trigger='first-use') == 0
    assert day(tmp_path)['success']
    assert daily.run(tmp_path, trigger='retry') == 0
    assert '2026-09-14' not in state(tmp_path)['days']


@pytest.mark.parametrize('code', [1, 75])
def test_failed_or_busy_child_does_not_complete_day_and_retry_can_succeed(tmp_path, monkeypatch, code):
    fake_launch(tmp_path, monkeypatch, returncode=code, publish=False)
    assert daily.run(tmp_path, trigger='first-use') == code
    assert day(tmp_path)['success'] is None
    assert day(tmp_path)['status'] == ('BUSY' if code == 75 else 'FAILED')
    calls = fake_launch(tmp_path, monkeypatch)
    assert daily.run(tmp_path, trigger='retry') == 0
    assert len(calls) == 1 and len(day(tmp_path)['attempts']) == 2


def test_every_busy_exit_names_its_own_cause(tmp_path, monkeypatch, capsys):
    """75 means BUSY for three different reasons; each must say which one."""
    reasons = {}

    # 1. Gate contention: another daily refresh already owns the daily lock.
    monkeypatch.setattr(daily.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    with daily.daily_lock(tmp_path):
        assert daily.run(tmp_path, trigger='first-use') == 75
    reasons['gate'] = json.loads(capsys.readouterr().out.strip().splitlines()[-1])['reason']

    # 2. A live child from an interrupted parent still owns the job.
    interrupted_attempt(tmp_path)
    monkeypatch.setattr(daily, 'process_alive', lambda pid: pid == 8123)
    assert daily.run(tmp_path, trigger='retry') == 75
    reasons['live_child'] = json.loads(capsys.readouterr().out.strip().splitlines()[-1])['reason']

    # 3. Our own child exited 75 because the pipeline writer lease was held.
    (tmp_path / daily.STATE).unlink()
    (tmp_path / daily.OBSERVATIONS / (DAY + '.json')).unlink()
    fake_launch(tmp_path, monkeypatch, returncode=75, publish=False)
    monkeypatch.setattr(daily, 'process_alive', lambda pid: False)
    assert daily.run(tmp_path, trigger='first-use') == 75
    reasons['child_75'] = json.loads(capsys.readouterr().out.strip().splitlines()[-1])['reason']

    assert reasons['child_75'] == 'pipeline lease busy (child exited 75)'
    assert len(set(reasons.values())) == 3, reasons
    assert day(tmp_path)['status'] == 'BUSY' and day(tmp_path)['success'] is None


def test_partial_press_coverage_still_closes_the_day(tmp_path, monkeypatch):
    """A PASS media receipt that names partial coverage and parked steps is
    still a completed day, so the hourly retry stops re-running the chain."""
    def launch(command, **kwargs):
        attempt_id = kwargs['env'][daily.ATTEMPT_ENV]
        directory = tmp_path / 'audits/congress_pipeline' / attempt_id
        directory.mkdir(parents=True, exist_ok=True)
        media = {'schema': 'daily-media-refresh-receipt-v1', 'status': 'PASS',
                 'error_count': 0, 'partial': True, 'partial_sources': ['press'],
                 'press_partial': {'pending_body_urls': 2200, 'failed_bodies': 2578,
                                   'status': 'PARTIAL'},
                 'parked_steps': ['press_intake', 'press_feed', 'press_baseline_fold',
                                  'all_intake']}
        daily.write(tmp_path / daily.MEDIA_RECEIPT, media)
        daily.write(directory / 'daily_media_refresh_receipt.json', media)
        receipt = {'schema': 'congress-pipeline-status-v1', 'status': 'PASS',
                   'include_media': True, 'mode': 'full', 'daily_attempt_id': attempt_id,
                   'owner_pid': Finished.pid, 'run_dir': str(directory),
                   'started_at': daily.utc_now(), 'completed_at': daily.utc_now(),
                   'artifacts': {daily.MEDIA_RECEIPT: {
                       'sha256': daily.digest(tmp_path / daily.MEDIA_RECEIPT)}}}
        daily.write(directory / 'receipt.json', receipt)
        daily.write(tmp_path / daily.WRAPPER_RECEIPT, receipt)
        daily.write(tmp_path / daily.WRAPPER_STATUS, receipt)
        return Finished()

    monkeypatch.setattr(daily.subprocess, 'Popen', launch)
    assert daily.run(tmp_path, trigger='first-use') == 0
    assert day(tmp_path)['status'] == 'PASS'
    assert day(tmp_path)['success']['include_media'] is True
    # The day is closed, so a later retry does not start the chain again.
    monkeypatch.setattr(daily.subprocess, 'Popen',
                        lambda *a, **k: pytest.fail('a completed day must not re-run'))
    assert daily.run(tmp_path, trigger='retry') == 0


def test_deferred_discovery_still_closes_the_day(tmp_path, monkeypatch):
    """A PASS receipt that defers flaky discovery is still a completed day, so
    the hourly retry stops instead of restarting the four-hour chain."""
    calls = fake_launch(tmp_path, monkeypatch,
                        changes={'deferred_steps': ['discovery']})
    assert daily.run(tmp_path, trigger='first-use') == 0
    assert len(calls) == 1
    assert day(tmp_path)['status'] == 'PASS'
    success = day(tmp_path)['success']
    assert success['include_media'] is True
    receipt = daily.read(Path(success['receipt']))
    assert receipt['deferred_steps'] == ['discovery'] and receipt['status'] == 'PASS'
    monkeypatch.setattr(daily.subprocess, 'Popen',
                        lambda *a, **k: pytest.fail('a completed day must not re-run'))
    assert daily.run(tmp_path, trigger='retry') == 0


@pytest.mark.parametrize('changes', [
    {'include_media': False}, {'status': 'FAILED'}, {'owner_pid': 99999},
    {'daily_attempt_id': 'another-attempt'}, {'schema': 'unrelated-receipt'},
    {'started_at': '2026-01-01T00:00:00+00:00'},
])
def test_only_owned_fresh_full_pass_receipt_completes_day(tmp_path, monkeypatch, changes):
    fake_launch(tmp_path, monkeypatch, changes=changes)
    assert daily.run(tmp_path, trigger='first-use') == 1
    assert day(tmp_path)['success'] is None
    assert day(tmp_path)['status'] == 'FAILED'


def test_preexisting_bills_only_pass_never_counts_as_daily_success(tmp_path, monkeypatch):
    complete(tmp_path, 'old-bills-only', include_media=False)
    calls = fake_launch(tmp_path, monkeypatch, publish=False)
    assert daily.run(tmp_path, trigger='first-use') == 1
    assert len(calls) == 1 and day(tmp_path)['success'] is None


@pytest.mark.parametrize('kind', ['changed_live_media', 'failed_snapshot', 'missing_immutable'])
def test_media_and_immutable_receipts_are_required(tmp_path, monkeypatch, kind):
    def launch(command, **kwargs):
        attempt_id = kwargs['env'][daily.ATTEMPT_ENV]
        receipt = complete(tmp_path, attempt_id)
        if kind == 'changed_live_media':
            daily.write(tmp_path / daily.MEDIA_RECEIPT, {'status': 'FAIL'})
        elif kind == 'failed_snapshot':
            daily.write(Path(receipt['run_dir']) / 'daily_media_refresh_receipt.json', {'status': 'FAIL'})
        else:
            (Path(receipt['run_dir']) / 'receipt.json').unlink()
        return Finished()

    monkeypatch.setattr(daily.subprocess, 'Popen', launch)
    assert daily.run(tmp_path, trigger='first-use') == 1
    assert day(tmp_path)['success'] is None


def test_gate_contention_preserves_first_use_for_later_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(daily.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    with daily.daily_lock(tmp_path):
        assert daily.run(tmp_path, trigger='first-use') == 75
        assert not (tmp_path / daily.STATE).exists()
    assert (tmp_path / daily.OBSERVATIONS / (DAY + '.json')).is_file()
    calls = fake_launch(tmp_path, monkeypatch)
    assert daily.run(tmp_path, trigger='retry') == 0 and len(calls) == 1


def interrupted_attempt(root, *, child_pid=8123):
    directory = root / 'audits/daily_refresh' / DAY / 'interrupted'
    attempt = {'id': 'interrupted', 'status': 'RUNNING', 'owner_pid': 99999,
               'child_pid': child_pid, 'started_epoch': 1, 'audit_dir': str(directory)}
    daily.write(root / daily.STATE, {'schema': daily.SCHEMA, 'days': {
        DAY: {'date': DAY, 'observed_at': daily.utc_now(), 'observed_trigger': 'first-use',
              'status': 'RUNNING', 'attempts': [attempt], 'success': None}}})
    return attempt


@pytest.mark.parametrize('recorded_child_pid', [None, 8123])
def test_successful_child_after_parent_interruption_is_reconciled_without_second_run(tmp_path, monkeypatch, recorded_child_pid):
    interrupted_attempt(tmp_path, child_pid=recorded_child_pid)
    complete(tmp_path, 'interrupted')
    daily.write(tmp_path / daily.MEDIA_RECEIPT, {'later': 'unrelated refresh'})
    monkeypatch.setattr(daily, 'process_alive', lambda pid: False)
    monkeypatch.setattr(daily.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch twice'))
    assert daily.run(tmp_path, trigger='retry') == 0
    assert day(tmp_path)['success']['attempt_id'] == 'interrupted'
    assert day(tmp_path)['attempts'][0]['recovered_completion'] is True


def test_interrupted_parent_does_not_start_over_its_live_child(tmp_path, monkeypatch):
    interrupted_attempt(tmp_path)
    monkeypatch.setattr(daily, 'process_alive', lambda pid: pid == 8123)
    monkeypatch.setattr(daily.subprocess, 'Popen', lambda *a, **k: pytest.fail('child still owns job'))
    assert daily.run(tmp_path, trigger='retry') == 75
    assert day(tmp_path)['success'] is None


def test_interrupted_failed_child_remains_retryable(tmp_path, monkeypatch):
    interrupted_attempt(tmp_path)
    monkeypatch.setattr(daily, 'process_alive', lambda pid: False)
    calls = fake_launch(tmp_path, monkeypatch)
    assert daily.run(tmp_path, trigger='retry') == 0
    assert len(calls) == 1
    assert day(tmp_path)['attempts'][0]['status'] == 'INTERRUPTED'
    assert day(tmp_path)['success']['attempt_id'] != 'interrupted'


def running_status(root, attempt_id, **changes):
    status = {'schema': 'congress-pipeline-status-v1', 'status': 'RUNNING',
              'include_media': True, 'daily_attempt_id': attempt_id,
              'owner_pid': Finished.pid,
              'run_dir': str(root / 'audits/congress_pipeline' / attempt_id)}
    status.update(changes)
    daily.write(root / daily.WRAPPER_STATUS, status)
    return status


@pytest.mark.parametrize('unavailable', ['permission', 'missing', 'sharing', 'locked'])
@pytest.mark.parametrize('publish', [False, True])
def test_transient_status_read_does_not_kill_child_or_replace_completion_checks(
        tmp_path, monkeypatch, unavailable, publish):
    class Pending(Finished):
        waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired('fixture', timeout)
            if publish:
                complete(tmp_path, self.attempt_id)
            self.returncode = 0
            return 0

    child = Pending(None)

    def launch(command, **kwargs):
        child.attempt_id = kwargs['env'][daily.ATTEMPT_ENV]
        running_status(tmp_path, child.attempt_id)
        return child

    real_read = daily.read
    status_reads = []

    def read_with_atomic_replace_race(path):
        if Path(path) == tmp_path / daily.WRAPPER_STATUS:
            status_reads.append(path)
            if unavailable == 'permission':
                raise PermissionError(13, 'status replacement denied this read')
            if unavailable == 'missing':
                raise FileNotFoundError(2, 'status not yet visible')
            error = OSError('status replacement is in progress')
            error.winerror = 32 if unavailable == 'sharing' else 33
            raise error
        return real_read(path)

    monkeypatch.setattr(daily.subprocess, 'Popen', launch)
    monkeypatch.setattr(daily, 'read', read_with_atomic_replace_race)
    monkeypatch.setattr(daily, 'terminate_tree', lambda process: pytest.fail('healthy child killed'))
    assert daily.run(tmp_path, trigger='first-use') == (0 if publish else 1)
    assert child.waits == 2 and len(status_reads) == 1
    record = day(tmp_path)
    assert bool(record['success']) is publish
    assert record['status'] == ('PASS' if publish else 'FAILED')
    assert record['attempts'][0]['child_pid'] == child.pid
    assert record['attempts'][0]['heartbeat_at']
    assert not (tmp_path / '.daily_refresh.lock').exists()


@pytest.mark.parametrize('changes', [
    {'owner_pid': 99999}, {'owner_pid': None}, {'owner_pid': True},
    {'schema': 'wrong'}, {'include_media': False}, {'run_dir': None},
    {'run_dir': ''}, {'run_dir': str(Path('outside-the-owned-audit-directory').resolve())},
])
def test_invalid_owned_status_still_stops_child_without_completing_day(tmp_path, monkeypatch, changes):
    class Pending(Finished):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired('fixture', timeout)

    child = Pending(None)

    def launch(command, **kwargs):
        running_status(tmp_path, kwargs['env'][daily.ATTEMPT_ENV], **changes)
        return child

    killed = []
    monkeypatch.setattr(daily.subprocess, 'Popen', launch)
    monkeypatch.setattr(daily, 'terminate_tree', lambda process: killed.append(process.pid))
    assert daily.run(tmp_path, trigger='first-use') == 1
    assert killed == [child.pid]
    assert day(tmp_path)['success'] is None
    assert day(tmp_path)['status'] == 'FAILED'


@pytest.mark.parametrize('document', ['{broken-json', '[]'])
def test_unreadable_status_content_is_not_a_transient_sharing_failure(tmp_path, document):
    path = tmp_path / daily.WRAPPER_STATUS
    path.parent.mkdir(parents=True)
    path.write_text(document, encoding='utf-8')
    with pytest.raises(ValueError):
        daily.inspect_child_status(tmp_path, {'id': 'attempt', 'child_pid': Finished.pid})


def test_unrelated_status_and_transient_read_leave_recorded_identity_unchanged(tmp_path, monkeypatch):
    attempt = {'id': 'current', 'child_pid': Finished.pid, 'wrapper_run_dir': 'already-recorded'}
    expected = dict(attempt)
    running_status(tmp_path, 'older-attempt', owner_pid=99999)
    daily.inspect_child_status(tmp_path, attempt)
    assert attempt == expected

    def unavailable(path):
        raise PermissionError(13, 'sharing violation')

    monkeypatch.setattr(daily, 'read', unavailable)
    daily.inspect_child_status(tmp_path, attempt)
    assert attempt == expected


def test_other_status_io_errors_still_fail_closed(tmp_path, monkeypatch):
    def broken_storage(path):
        raise OSError(5, 'storage I/O error')

    monkeypatch.setattr(daily, 'read', broken_storage)
    with pytest.raises(OSError, match='storage I/O error'):
        daily.inspect_child_status(tmp_path, {'id': 'attempt', 'child_pid': Finished.pid})


@pytest.mark.parametrize('interrupted', [False, True])
def test_timeout_and_keyboard_interrupt_cleanup_child_without_success(tmp_path, monkeypatch, interrupted):
    class Pending(Finished):
        def wait(self, timeout=None):
            if interrupted:
                raise KeyboardInterrupt()
            raise subprocess.TimeoutExpired('fixture', timeout)

    child = Pending(None)
    killed = []
    monkeypatch.setattr(daily.subprocess, 'Popen', lambda *a, **k: child)
    monkeypatch.setattr(daily, 'terminate_tree', lambda process: killed.append(process.pid))
    assert daily.run(tmp_path, trigger='first-use', timeout_seconds=-1) == 1
    assert killed == [child.pid]
    assert day(tmp_path)['success'] is None
    assert not (tmp_path / '.daily_refresh.lock').exists()


def test_corrupt_day_state_fails_closed(tmp_path, monkeypatch):
    daily.write(tmp_path / daily.STATE, {'schema': 'wrong', 'days': {}})
    monkeypatch.setattr(daily.subprocess, 'Popen', lambda *a, **k: pytest.fail('invalid gate'))
    assert daily.run(tmp_path, trigger='first-use') == 1
    assert daily.read(tmp_path / daily.STATE)['schema'] == 'wrong'


def test_child_launch_failure_is_a_retryable_audited_attempt(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('runtime unavailable')

    monkeypatch.setattr(daily.subprocess, 'Popen', unavailable)
    assert daily.run(tmp_path, trigger='first-use') == 1
    attempt = day(tmp_path)['attempts'][0]
    assert attempt['status'] == 'FAILED' and 'runtime unavailable' in attempt['error']
    assert daily.read(Path(attempt['audit_dir']) / 'attempt.json') == attempt
    assert day(tmp_path)['success'] is None
    assert not (tmp_path / '.daily_refresh.lock').exists()


def backdated_launch(root, monkeypatch, seconds, only=None):
    """Publish a complete PASS whose clock readings sit `seconds` before the attempt start."""
    def launch(command, **kwargs):
        stamp = day(root)['attempts'][-1]['started_epoch'] - seconds
        changes = {}
        if only in (None, 'window'):
            changes['started_at'] = daily.datetime.fromtimestamp(stamp, daily.timezone.utc).isoformat()
        receipt = complete(root, kwargs['env'][daily.ATTEMPT_ENV], **changes)
        files = {'immutable': Path(receipt['run_dir']) / 'receipt.json',
                 'snapshot': Path(receipt['run_dir']) / 'daily_media_refresh_receipt.json',
                 'live': root / daily.MEDIA_RECEIPT}
        for name, path in files.items():
            if only in (None, name):
                os.utime(path, (stamp, stamp))
        return Finished()

    monkeypatch.setattr(daily.subprocess, 'Popen', launch)


def test_clock_readings_may_trail_the_attempt_clock(tmp_path, monkeypatch):
    """Windows stamps files from the coarse system tick, st_mtime and time.time()
    round one instant to different floats, and an ISO timestamp is floored to the
    microsecond, so a reading taken after the attempt began can sit slightly
    before started_epoch. Measured 2026-09-19: up to 19.3 ms. Reproduced here
    deterministically for all four comparisons."""
    backdated_launch(tmp_path, monkeypatch, 0.02)
    assert daily.run(tmp_path, trigger='first-use') == 0
    assert day(tmp_path)['status'] == 'PASS'


@pytest.mark.parametrize('stale,message', [
    ('window', 'does not belong to this attempt time window'),
    ('immutable', 'immutable completion receipt is missing or stale'),
    ('snapshot', 'fresh hash-bound successful media receipt'),
    ('live', 'Live media receipt no longer matches'),
])
def test_reading_older_than_the_slack_is_still_rejected(tmp_path, monkeypatch, capsys, stale, message):
    """The slack covers a clock tick, never what an earlier attempt left behind."""
    backdated_launch(tmp_path, monkeypatch, daily.CLOCK_SLACK_SECONDS + 1, only=stale)
    assert daily.run(tmp_path, trigger='first-use') == 1
    assert message in capsys.readouterr().out
    assert day(tmp_path)['success'] is None


def test_w2_third_full_attempt_in_a_day_is_capped(tmp_path, monkeypatch, capsys):
    events = []
    monkeypatch.setattr(daily, '_gb', lambda *a, **k: events.append(a))
    calls = fake_launch(tmp_path, monkeypatch, returncode=1, publish=False)
    assert daily.run(tmp_path, trigger='first-use') == 1
    assert daily.run(tmp_path, trigger='retry') == 1
    assert len(calls) == 2
    capsys.readouterr()
    assert daily.run(tmp_path, trigger='retry') == 0
    assert len(calls) == 2, 'the third tick launched the chain'
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])['status'] == 'CAPPED'
    assert day(tmp_path)['status'] == 'CAPPED' and 'cap is 2' in day(tmp_path)['capped_reason']
    assert events and events[-1][0] == 'daily_refresh.capped'

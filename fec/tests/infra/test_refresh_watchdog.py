# -*- coding: utf-8 -*-
"""fec/refresh_watchdog.py: persistence gate, alert/re-alert/resolve, tolerance of absent inputs.

Everything runs in tmp_path; the toast and Task Scheduler are monkeypatched, so nothing is shown,
registered or queried.  The one live-clock dependency (file mtimes) is anchored to NOW.
"""
import datetime
import json
import os
import subprocess
import sys

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)
import refresh_watchdog as rw                                          # noqa: E402

NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
STEP = datetime.timedelta(minutes=30)
HEALTHY_TASK = {'status': 'Ready', 'state': 'Enabled', 'last_result': 0, 'last_run': 'run-0'}


def at(n):
    return NOW + n * STEP


def touch(path, when, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    os.utime(path, (when.timestamp(), when.timestamp()))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A healthy temp root plus recorders for toasts and a settable disk / task."""
    class World:
        root = tmp_path

        def go(self, n, **kw):
            return rw.run(self.root, now=at(n), **kw)

        @property
        def log(self):
            path = self.root / 'data' / 'engine' / 'watchdog_alerts.log'
            return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

        @property
        def state(self):
            return json.loads((self.root / 'data' / 'engine' / 'watchdog_state.json').read_text(encoding='utf-8'))

    w = World()
    w.toasts = []
    w.disk = [500.0]
    w.task = [dict(HEALTHY_TASK)]
    touch(tmp_path / 'member_desk_bundle.json', NOW)
    touch(tmp_path / 'Moneyball_email.zip', NOW)
    (tmp_path / 'data' / 'engine').mkdir(parents=True)
    monkeypatch.setattr(rw, 'send_toast', lambda title, body: (w.toasts.append((title, body)) or True, ''))
    monkeypatch.setattr(rw, 'free_gb', lambda root: w.disk[0])
    monkeypatch.setattr(rw, 'query_tick_task', lambda: dict(w.task[0]))
    return w


# ---- gate / alert / re-alert / resolve ------------------------------------------------------
def test_no_breach_no_alert(world):
    for n in range(6):
        result = world.go(n)
        assert result['breaches'] == {}
    assert world.toasts == [] and world.log == []
    assert world.state['breaches'] == {}


def test_breach_needs_three_consecutive_runs(world):
    world.disk[0] = 12.0
    assert world.go(0)['alerts'] == [] and world.go(1)['alerts'] == []
    assert world.toasts == [] and world.log == []
    third = world.go(2)
    assert [a['key'] for a in third['alerts']] == ['disk']
    assert len(world.toasts) == 1 and world.toasts[0][0] == 'Melange pipeline' and '12.0 GB' in world.toasts[0][1]
    assert [(l['event'], l['key'], l['toast']) for l in world.log] == [('alert', 'disk', 'ok')]
    world.go(3)                                              # still breached: no second alert
    assert len(world.toasts) == 1 and len(world.log) == 1


def test_realert_only_after_twelve_hours(world):
    world.disk[0] = 12.0
    for n in range(3):
        world.go(n)                                          # alert at run index 2
    world.go(2 + 23)                                         # 11.5 h later
    assert len(world.toasts) == 1
    world.go(2 + 24)                                         # 12 h later
    assert len(world.toasts) == 2 and world.toasts[1][1].startswith('Still failing')
    assert [l['event'] for l in world.log] == ['alert', 'realert'] and world.log[1]['alert_count'] == 2
    world.go(2 + 25)
    assert len(world.toasts) == 2


def test_resolved_notice_once_after_alert(world):
    world.disk[0] = 12.0
    for n in range(3):
        world.go(n)
    world.disk[0] = 500.0
    cleared = world.go(3)
    assert [r['key'] for r in cleared['resolved']] == ['disk']
    assert world.toasts[-1][1] == 'Resolved: disk'
    assert [l['event'] for l in world.log] == ['alert', 'resolved']
    world.go(4)
    assert len(world.toasts) == 2 and len(world.log) == 2


def test_breach_that_clears_before_third_run_is_silent_and_recounts(world):
    world.disk[0] = 12.0
    world.go(0), world.go(1)
    world.disk[0] = 500.0
    world.go(2)
    world.disk[0] = 12.0
    world.go(3), world.go(4)
    assert world.toasts == [] and world.log == []
    assert world.state['breaches']['disk']['consecutive'] == 2


def test_disk_floor_boundary(world):
    world.disk[0] = 29.9
    assert 'disk' in world.go(0)['breaches']
    world.disk[0] = 30.0
    assert 'disk' not in world.go(1)['breaches']


# ---- tolerance ------------------------------------------------------------------------------
def test_missing_inputs_tolerated(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, 'free_gb', lambda root: 500.0)
    monkeypatch.setattr(rw, 'query_tick_task', lambda: {'unavailable': 'no schtasks'})
    monkeypatch.setattr(rw, 'send_toast', lambda t, b: pytest.fail('no toast expected'))
    result = rw.run(tmp_path / 'does' / 'not' / 'exist', now=NOW)
    assert result['breaches'] == {} and result['notes'] == ['tick_task skipped: no schtasks']


def test_corrupt_inputs_and_state_do_not_crash(world):
    eng = world.root / 'data' / 'engine'
    (eng / 'watchdog_state.json').write_text('{not json', encoding='utf-8')
    (eng / 'source_freshness.json').write_text('{broken', encoding='utf-8')
    (world.root / '.pipeline_run.lock').write_text('garbage', encoding='utf-8')
    result = world.go(0)
    assert 'check_error:sources' in result['breaches']       # the watchdog must not go blind quietly
    assert world.state['breaches']['check_error:sources']['consecutive'] == 1


def test_toast_failure_does_not_raise_and_is_logged(world, monkeypatch):
    def boom(title, body):
        raise RuntimeError('no desktop session')
    monkeypatch.setattr(rw, 'send_toast', boom)
    world.disk[0] = 12.0
    for n in range(3):
        world.go(n)                                          # must not raise
    assert world.log[0]['event'] == 'alert' and world.log[0]['toast'].startswith('failed: RuntimeError')
    world.go(3)
    assert len(world.log) == 1                               # counted as sent: no 30-minute spam


def test_send_toast_never_raises(monkeypatch):
    def no_powershell(*a, **k):
        raise FileNotFoundError('powershell.exe')
    monkeypatch.setattr(rw.subprocess, 'run', no_powershell)
    ok, note = rw.send_toast('t', 'b')
    assert ok is False and note
    monkeypatch.setattr(rw.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, '', 'bad'))
    assert rw.send_toast('t', 'b')[0] is False


def test_send_toast_passes_text_in_env_not_script(monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen.update(cmd=cmd, env=kw['env'])
        return subprocess.CompletedProcess(cmd, 0, '', '')
    monkeypatch.setattr(rw.subprocess, 'run', fake)
    if os.name != 'nt':
        pytest.skip('toast is Windows-only')
    assert rw.send_toast('Melange pipeline', "it's \"quoted\"") == (True, '')
    assert seen['env']['MELANGE_TOAST_BODY'] == "it's \"quoted\"" and 'ToastText02' in rw._TOAST_PS
    assert rw.TOAST_APP_ID in rw._TOAST_PS and '-EncodedCommand' in seen['cmd']


def test_dry_run_writes_and_shows_nothing(world):
    world.disk[0] = 12.0
    world.go(0), world.go(1)
    state_before = (world.root / 'data' / 'engine' / 'watchdog_state.json').read_bytes()
    result = world.go(2, dry_run=True)
    assert [a['key'] for a in result['alerts']] == ['disk']  # it would alert ...
    assert world.toasts == [] and world.log == []            # ... but did not
    assert (world.root / 'data' / 'engine' / 'watchdog_state.json').read_bytes() == state_before


def test_dry_run_on_empty_root_creates_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, 'free_gb', lambda root: 500.0)
    monkeypatch.setattr(rw, 'query_tick_task', lambda: {'unavailable': 'x'})
    rw.run(tmp_path, dry_run=True, now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_main_returns_zero_even_with_breaches(world, capsys):
    world.disk[0] = 12.0
    assert rw.main(['--once', '--dry-run', '--root', str(world.root)]) == 0
    assert 'BREACH disk' in capsys.readouterr().out


# ---- individual checks ----------------------------------------------------------------------
def write_sources(world, payload):
    (world.root / 'data' / 'engine' / 'source_freshness.json').write_text(json.dumps(payload), encoding='utf-8')


def ago(hours):
    return (NOW - datetime.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')


def test_source_thresholds_core_2x_enrichment_3x(world):
    write_sources(world, {'sources': {
        'core_late': {'last_success_utc': ago(49), 'cadence_h': 24, 'tier': 'core'},
        'core_ok': {'last_success_utc': ago(47), 'cadence_h': 24, 'tier': 'core'},
        'enrich_late': {'last_success_utc': ago(73), 'cadence_h': 24, 'tier': 'enrichment'},
        'enrich_ok': {'last_success_utc': ago(71), 'cadence_h': 24, 'tier': 'enrichment'},
        'from_age_h': {'age_h': 100, 'cadence_h': 24, 'tier': 'core'},
        'no_cadence': {'last_success_utc': ago(999), 'tier': 'core'}}})
    assert sorted(world.go(0)['breaches']) == ['source:core_late', 'source:enrich_late', 'source:from_age_h']
    write_sources(world, [{'name': 'listed', 'last_success_utc': ago(60), 'cadence_h': 24, 'tier': 'core'}])
    assert list(world.go(1)['breaches']) == ['source:listed']


def test_live_publication_older_than_30h(world):
    touch(world.root / 'member_desk_bundle.json', NOW - datetime.timedelta(hours=31))
    touch(world.root / 'Moneyball_email.zip', NOW - datetime.timedelta(hours=29))
    assert list(world.go(0)['breaches']) == ['live:member_desk_bundle.json']


def lease(world, hours_old, **payload):
    path = world.root / '.pipeline_run.lock'
    payload.setdefault('pid', 4242)
    payload['started_utc'] = (NOW - datetime.timedelta(hours=hours_old)).timestamp()
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def test_lease_stale_with_dead_owner_is_a_breach(world, monkeypatch):
    lease(world, 3)
    monkeypatch.setattr(rw, '_owner_live', lambda payload: False)
    assert list(world.go(0)['breaches']) == ['lease']
    monkeypatch.setattr(rw, '_owner_live', lambda payload: True)
    assert world.go(1)['breaches'] == {}                     # a long but live run
    monkeypatch.setattr(rw, '_owner_live', lambda payload: False)
    lease(world, 1)
    assert world.go(2)['breaches'] == {}                     # young lease


def test_lease_heartbeat_file_overrides_started_utc(world, monkeypatch):
    path = lease(world, 5)
    beat = world.root / '.pipeline_run.heartbeat.lock'
    touch(beat, NOW - datetime.timedelta(minutes=5))
    monkeypatch.setattr(rw, '_owner_live', lambda payload: False)
    assert world.go(0)['breaches'] == {}
    touch(beat, NOW - datetime.timedelta(hours=4))
    assert list(world.go(1)['breaches']) == ['lease']
    path.unlink()
    assert world.go(2)['breaches'] == {}


def test_owner_live_rules():
    assert rw._owner_live({}) is False                       # malformed: nobody to wait for
    assert rw._owner_live({'pid': os.getpid()}) is True
    assert rw._owner_live({'pid': 2 ** 22 + 12345}) is False
    assert rw._owner_live({'pid': os.getpid(), 'proc_start': 1.0}) is False   # recycled pid
    # a lease from another host cannot be judged by pid: held on Windows; POSIX skips the host test
    assert rw._owner_live({'pid': 2 ** 22 + 12345, 'host': 'nt:SOME-OTHER-BOX'}) is (os.name == 'nt')


def task_row(**kw):
    return dict(HEALTHY_TASK, **kw)


def test_tick_task_missing_disabled_and_failure_streak(world):
    world.task[0] = {'missing': True}
    assert 'tick_task' in world.go(0)['breaches']
    world.task[0] = task_row(status='Disabled', state='Disabled')
    assert 'disabled' in world.go(1)['breaches']['tick_task']
    # three DISTINCT nonzero fires; a repeated Last Run Time is the same fire
    world.task[0] = task_row(last_result=1, last_run='f1')
    assert 'tick_task' not in world.go(2)['breaches']
    assert 'tick_task' not in world.go(3)['breaches']
    world.task[0] = task_row(last_result=1, last_run='f2')
    assert 'tick_task' not in world.go(4)['breaches']
    world.task[0] = task_row(last_result=267009, last_run='f3')   # running: not a fire
    assert 'tick_task' not in world.go(5)['breaches']
    world.task[0] = task_row(last_result=1, last_run='f3')
    assert 'tick_task' in world.go(6)['breaches']
    world.task[0] = task_row(last_result=0, last_run='f4')       # a success resets the streak
    assert 'tick_task' not in world.go(7)['breaches']


def test_parse_schtasks_csv_with_unescaped_quotes():
    text = ('"HostName","TaskName","Status","Last Run Time","Last Result","Task To Run","Scheduled Task State"\n'
            '"PC","\\MelangeRefreshTick","Ready","10/15/2026 3:18:04 PM","267011",'
            '"python.exe "C:\\a b\\fec\\refresh_tick.py"","Enabled"\n')
    assert rw.parse_schtasks_csv(text) == {'status': 'Ready', 'state': 'Enabled', 'last_result': 267011,
                                           'last_run': '10/15/2026 3:18:04 PM'}
    assert rw.parse_schtasks_csv('ERROR: The system cannot find the file specified.') is None
    assert rw.parse_schtasks_csv('') is None


def test_query_tick_task_missing_and_unavailable(monkeypatch):
    monkeypatch.setattr(rw.subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, '', 'ERROR: cannot find'))
    assert rw.query_tick_task() == {'missing': True}
    monkeypatch.setattr(rw.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(OSError('nope')))
    assert 'unavailable' in rw.query_tick_task()


def test_dead_letter_baseline_growth_and_hold(world):
    folder = world.root / 'data' / 'engine' / 'dead_letter'
    folder.mkdir()
    (folder / 'a.jsonl').write_text('{"n":1}\n{"n":2}\n', encoding='utf-8')
    assert 'dead_letter' not in world.go(0)['breaches']      # first run only baselines
    assert 'dead_letter' not in world.go(1)['breaches']
    with open(folder / 'a.jsonl', 'a', encoding='utf-8') as handle:
        handle.write('{"n":3}\n')
    assert 'dead_letter' in world.go(2)['breaches']
    assert 'dead_letter' in world.go(3)['breaches']          # held, though nothing new arrived
    assert [a['key'] for a in world.go(4)['alerts']] == ['dead_letter']   # 3rd consecutive run
    assert 'dead_letter' not in world.go(2 + 12)['breaches'] # 6 h after the growth: cleared
    assert world.log[-1]['event'] == 'resolved'

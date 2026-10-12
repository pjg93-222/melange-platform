"""A concurrent reader must never be able to fail a writer's atomic rename.

On 2026-09-13 22:42Z a scheduled congress-pipeline run died on
`os.replace(...congress_pipeline_status.json.15892.tmp, ...json)` with
`PermissionError: [WinError 5] Access is denied` because the live watcher was
polling that file every five seconds. Nothing here touches a real receipt: every
test writes into tmp_path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lane_file_io as L

WRITERS = ('daily_refresh', 'daily_media_refresh', 'refresh_congress_pipeline')


def resolve_writer(label):
    """The atomic JSON writer each daily-refresh module publishes through."""
    if label == 'daily_refresh':
        import daily_refresh
        return daily_refresh.write
    if label == 'daily_media_refresh':
        from collect import daily_media_refresh
        return daily_media_refresh.write_json
    from legislation import refresh_congress_pipeline
    return refresh_congress_pipeline.write


def denial(winerror=5):
    error = PermissionError(13, 'Access is denied')
    error.winerror = winerror
    return error


def sharing_violation(winerror):
    """A sharing/lock violation surfaces as a plain OSError, not PermissionError."""
    error = OSError(0, 'The process cannot access the file')
    error.winerror = winerror
    return error


def flaky_replace(failures, error_factory=denial):
    """Fail the first `failures` renames, then perform the real one."""
    state = {'calls': 0}
    real = os.replace

    def replace(source, target):
        state['calls'] += 1
        if state['calls'] <= failures:
            raise error_factory()
        return real(source, target)

    replace.state = state
    return replace


@pytest.fixture(autouse=True)
def instant_backoff(monkeypatch):
    slept = []
    monkeypatch.setattr(L.time, 'sleep', lambda seconds: slept.append(seconds))
    return slept


def strays(target):
    """Anything left in the destination directory that is not the output."""
    target = Path(target)
    if not target.parent.is_dir():
        return []
    return sorted(p.name for p in target.parent.iterdir() if p.name != target.name)


@pytest.mark.parametrize('failures', [1, 5, 39])
def test_replace_retries_through_a_transient_denial_and_still_publishes(
        tmp_path, monkeypatch, failures, instant_backoff):
    target = tmp_path / 'congress_pipeline_status.json'
    target.write_text('previous generation', encoding='utf-8')
    scratch = tmp_path / 'congress_pipeline_status.json.15892.tmp'
    scratch.write_text('the new generation', encoding='utf-8')
    replace = flaky_replace(failures)
    monkeypatch.setattr(L.os, 'replace', replace)

    assert L.replace_atomic(scratch, target) == failures + 1
    assert target.read_text(encoding='utf-8') == 'the new generation'
    assert not scratch.exists() and strays(target) == []
    # One pause per failed attempt, each inside the configured jitter window.
    assert len(instant_backoff) == failures
    assert all(L._REPLACE_BACKOFF[0] <= s <= L._REPLACE_BACKOFF[1] for s in instant_backoff)


@pytest.mark.parametrize('winerror', [5, 32, 33])
def test_every_transient_windows_code_is_retried(tmp_path, monkeypatch, winerror):
    target = tmp_path / 'state.json'
    scratch = tmp_path / 'state.json.tmp'
    scratch.write_text('body', encoding='utf-8')
    if winerror == 5:
        factory = denial
    else:
        factory = lambda: sharing_violation(winerror)  # noqa: E731 - one-line fixture
    monkeypatch.setattr(L.os, 'replace', flaky_replace(2, factory))

    assert L.replace_atomic(scratch, target) == 3
    assert target.read_text(encoding='utf-8') == 'body'


def test_a_persistent_denial_raises_and_leaves_no_scratch_file(tmp_path, monkeypatch, instant_backoff):
    target = tmp_path / 'state.json'
    target.write_text('previous generation', encoding='utf-8')
    scratch = tmp_path / 'state.json.tmp'
    scratch.write_text('never published', encoding='utf-8')
    replace = flaky_replace(10 ** 6)
    monkeypatch.setattr(L.os, 'replace', replace)

    with pytest.raises(PermissionError):
        L.replace_atomic(scratch, target, attempts=6)

    assert replace.state['calls'] == 6 and len(instant_backoff) == 5
    # The reader keeps the previous generation, and no sibling is left behind
    # for OneDrive to sync or a later run to mistake for output.
    assert target.read_text(encoding='utf-8') == 'previous generation'
    assert not scratch.exists() and strays(target) == []


def test_a_real_error_is_not_retried(tmp_path, monkeypatch, instant_backoff):
    """A vanished scratch file or a bad path must surface immediately."""
    calls = []

    def replace(source, target):
        calls.append(source)
        raise FileNotFoundError(2, 'No such file or directory')

    monkeypatch.setattr(L.os, 'replace', replace)
    with pytest.raises(FileNotFoundError):
        L.replace_atomic(tmp_path / 'gone.tmp', tmp_path / 'state.json')
    assert len(calls) == 1 and instant_backoff == []


def test_atomic_write_bytes_publishes_through_the_same_retry(tmp_path, monkeypatch):
    target = tmp_path / 'nested' / 'artifact.json'
    monkeypatch.setattr(L.os, 'replace', flaky_replace(3))
    L.atomic_write_bytes(target, b'{"ok": true}')
    assert json.loads(target.read_text(encoding='utf-8')) == {'ok': True}
    assert strays(target) == []


# --- the three daily-refresh writers all publish through the shared helper ---


@pytest.mark.parametrize('label', WRITERS)
def test_each_daily_writer_survives_a_reader_holding_its_output(tmp_path, monkeypatch, label):
    writer = resolve_writer(label)
    replace = flaky_replace(4)
    monkeypatch.setattr(L.os, 'replace', replace)
    target = tmp_path / 'data/engine' / (label + '.json')
    document = {'schema': 'fixture', 'status': 'PASS', 'writer': label}

    writer(target, document)

    assert json.loads(target.read_text(encoding='utf-8')) == document
    assert replace.state['calls'] == 5, 'this writer does not publish through replace_atomic'
    assert strays(target) == []


@pytest.mark.parametrize('label', WRITERS)
def test_each_daily_writer_reports_a_permanent_failure_without_debris(tmp_path, monkeypatch, label):
    writer = resolve_writer(label)
    monkeypatch.setattr(L.os, 'replace', flaky_replace(10 ** 6))
    target = tmp_path / 'data/engine' / (label + '.json')
    target.parent.mkdir(parents=True, exist_ok=True)

    with pytest.raises(PermissionError):
        writer(target, {'schema': 'fixture'})

    assert not target.exists() and strays(target) == []

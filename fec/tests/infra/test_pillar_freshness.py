"""Mode-aware stale-basis guards; all fixtures are isolated from live artifacts."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from audit.pillar_freshness import (
    LEGACY_CHECK, LIVE_CHECK, LIVE_INPUTS, check_pillar_freshness,
)

NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
SNAPSHOT = 'data/engine/pillar_inputs_snapshot.json'
POLICY = 'data/engine/sentinel_admission_policy.json'


def write(root, path, data):
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data), encoding='utf-8')


@pytest.fixture
def live_root(tmp_path):
    for path in LIVE_INPUTS:
        write(tmp_path, path, {})
    write(tmp_path, POLICY, {'live_legislative_pillar': True})
    write(tmp_path, SNAPSHOT, {'stamped': '2026-09-03T21:04:35-04:00'})
    hashes = {path: hashlib.sha256((tmp_path / path).read_bytes()).hexdigest()
              for path in LIVE_INPUTS}
    write(tmp_path, 'ranks_python.json', {'legislative_pillar': {
        'schema': 'live-legislative-pillar-v1', 'inputs_sha256': hashes}})
    return tmp_path


def test_live_mode_ignores_legacy_snapshot_and_is_read_only(live_root):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in live_root.rglob('*') if p.is_file()}
    assert check_pillar_freshness(live_root, authority_parity=True, now=NOW)[:2] == (LIVE_CHECK, True)
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
    # A corrupt or missing legacy cache is also irrelevant to live computation.
    (live_root / SNAPSHOT).write_text('not json', encoding='utf-8')
    assert check_pillar_freshness(live_root, authority_parity=True)[1]
    (live_root / SNAPSHOT).unlink()
    assert check_pillar_freshness(live_root, authority_parity=True)[1]


@pytest.mark.parametrize('path,change', [
    (path, change) for path in LIVE_INPUTS for change in ('changed', 'missing', 'unrecorded')
    if (path, change) != (POLICY, 'missing')
])
def test_live_inputs_must_match_recorded_hashes(live_root, path, change):
    if change == 'changed':
        # Whitespace alone changes provenance without necessarily changing ranks.
        with (live_root / path).open('a', encoding='utf-8') as handle:
            handle.write('\n')
    elif change == 'missing':
        (live_root / path).unlink()
    else:
        authority = json.loads((live_root / 'ranks_python.json').read_text())
        del authority['legislative_pillar']['inputs_sha256'][path]
        write(live_root, 'ranks_python.json', authority)
    name, passed, detail = check_pillar_freshness(live_root, authority_parity=True)
    assert name == LIVE_CHECK
    assert not passed
    assert path.split('/')[-1] in detail


def test_live_mode_rejects_stale_recomputed_authority(live_root):
    assert check_pillar_freshness(live_root, authority_parity=False)[:2] == (LIVE_CHECK, False)


def test_removing_live_policy_restores_legacy_guard(live_root):
    (live_root / POLICY).unlink()
    assert check_pillar_freshness(live_root, authority_parity=True, now=NOW)[:2] == (LEGACY_CHECK, False)


@pytest.mark.parametrize('path', [POLICY, 'ranks_python.json'])
def test_malformed_active_inputs_fail_closed(live_root, path):
    (live_root / path).write_text('not json', encoding='utf-8')
    assert not check_pillar_freshness(live_root, authority_parity=True)[1]


@pytest.mark.parametrize('authority', [None, {}, {'legislative_pillar': {'schema': 'wrong'}}])
def test_live_mode_requires_live_authority_provenance(live_root, authority):
    if authority is None:
        (live_root / 'ranks_python.json').unlink()
    else:
        write(live_root, 'ranks_python.json', authority)
    assert check_pillar_freshness(live_root, authority_parity=True)[:2] == (LIVE_CHECK, False)


@pytest.mark.parametrize('policy', [None, {}, {'live_legislative_pillar': False},
                                    {'live_legislative_pillar': 'true'}, {'live_legislative_pillar': 1}])
@pytest.mark.parametrize('age,passed', [(14, True), (15, False), (18, False)])
def test_legacy_mode_preserves_vintage_guard(tmp_path, policy, age, passed):
    if policy is not None:
        write(tmp_path, POLICY, policy)
    write(tmp_path, SNAPSHOT, {'stamped': (NOW - timedelta(days=age)).isoformat()})
    assert check_pillar_freshness(tmp_path, authority_parity=True, now=NOW)[:2] == (LEGACY_CHECK, passed)


@pytest.mark.parametrize('snapshot', [None, {}])
def test_legacy_unstamped_behavior_is_preserved(tmp_path, snapshot):
    if snapshot is not None:
        write(tmp_path, SNAPSHOT, snapshot)
    assert check_pillar_freshness(tmp_path, authority_parity=True, now=NOW)[:2] == (LEGACY_CHECK, True)


def test_invalid_legacy_stamp_fails(tmp_path):
    write(tmp_path, SNAPSHOT, {'stamped': 'not a timestamp'})
    assert check_pillar_freshness(tmp_path, authority_parity=True, now=NOW)[:2] == (LEGACY_CHECK, False)


def test_audit_wires_frozen_parity_into_live_freshness(live_root, monkeypatch):
    import os
    source = (FEC / 'audit/audit_full.py').read_text(encoding='utf-8')
    block = source[source.index("os.environ['PILLAR_SNAPSHOT_FREEZE']='1'"):
                   source.index('_dbm=os.path.getmtime')]
    monkeypatch.delenv('PILLAR_SNAPSHOT_FREEZE', raising=False)
    for output, passed in [('PARITY: CLEAN', True), ('AUTHORITY STALE', False), ('', False)]:
        checks = []

        def run(script):
            assert script == 'engine/compute_pillars.py'
            assert os.environ['PILLAR_SNAPSHOT_FREEZE'] == '1'
            return output

        exec(block, {'os': os, 'B': str(live_root), 'run': run,
                     'ck': lambda *args: checks.append(args)})
        assert checks[0][1] is passed
        assert checks[1][:2] == (LIVE_CHECK, passed)
        assert 'PILLAR_SNAPSHOT_FREEZE' not in os.environ

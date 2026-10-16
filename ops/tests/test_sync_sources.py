"""Source propagation guards, with no real remotes or changes to shared refs."""
import json
from types import SimpleNamespace

import pytest

from ops import sync_sources as sync


@pytest.fixture
def lane(tmp_path, monkeypatch):
    cfg = {'platform_remote_url': sync.PLATFORM_ORIGIN, 'melange_ref': 'main',
           'mentat_ref': 'codex/mentat-crypto', 'melange_live': str(tmp_path / 'source'),
           'mentat_live': str(tmp_path / 'mentat'), 'platform_bare': str(tmp_path / 'bare')}
    state = tmp_path / 'state'
    heads = {'melange': 'a'*40, 'mentat': 'b'*40}
    model = SimpleNamespace(local='1'*40, remote='1'*40, changed=True, scans=0,
                            builds=0, pushes=0, proof=0, source_heads=heads.copy())
    monkeypatch.setattr(sync, 'check_routing', lambda cfg: tmp_path / 'bare')
    monkeypatch.setattr(sync, 'check_push_routing', lambda *a: None)
    monkeypatch.setattr(sync.export, 'rev', lambda *a: model.local)
    monkeypatch.setattr(sync.export, 'plan_export', lambda cfg: {'heads': heads})
    monkeypatch.setattr(sync.export, 'diff_against', lambda *a: {
        'added': ['ops/desktop/client/main.cjs'] if model.changed else [], 'changed': [], 'removed': []})
    def build(*a, **kw):
        assert kw['parent'] == model.local
        model.builds += 1
        return '2'*40, '3'*40
    monkeypatch.setattr(sync.export, 'build_commit', build)
    def prove(*a):
        model.proof += 1
    monkeypatch.setattr(sync.export, 'prove', prove)
    def gate(*a):
        model.scans += 1
        return 'security.json'
    monkeypatch.setattr(sync, 'security_gate', gate)
    monkeypatch.setattr(sync, 'source_heads', lambda cfg: model.source_heads)
    monkeypatch.setattr(sync, 'remote_head', lambda cfg: model.remote)
    def cas(bare, ref, new, old):
        assert ref == 'refs/heads/main' and old == model.local
        model.local = new
        model.changed = False
    monkeypatch.setattr(sync.export, 'set_ref', cas)
    def push(*args, **kw):
        assert args == ('push', 'origin', model.local + ':refs/heads/main')
        model.pushes += 1
        model.remote = model.local
    monkeypatch.setattr(sync.core, 'git', push)
    return cfg, state, model


def test_plan_and_unchanged_apply_never_build_scan_or_write_refs(lane):
    cfg, state, model = lane
    assert sync.synchronize(cfg, state_root=state)['status'] == 'plan'
    assert not state.exists()
    model.changed = False
    for _ in range(2):
        assert sync.synchronize(cfg, apply=True, push=True, state_root=state)['status'] == 'unchanged'
    assert (model.builds, model.scans, model.pushes) == (0, 0, 0)


def test_apply_proves_scans_cas_pushes_once_and_then_skips(lane):
    cfg, state, model = lane
    result = sync.synchronize(cfg, apply=True, push=True, state_root=state)
    assert result['status'] == 'synchronized' and result['pushed']
    assert result['sourceHeads'] == model.source_heads
    assert (model.proof, model.scans, model.pushes) == (1, 1, 1)
    assert sync.synchronize(cfg, apply=True, push=True, state_root=state)['status'] == 'unchanged'
    assert (model.builds, model.scans, model.pushes) == (1, 1, 1)


@pytest.mark.parametrize('failure', ['security', 'source-moved', 'remote-moved', 'cas'])
def test_guard_failures_never_push_or_replace_latest(lane, monkeypatch, failure):
    cfg, state, model = lane
    if failure == 'security':
        def gate(*a):
            raise sync.core.ReleaseError('SOURCE_SECURITY', 'fixture failed')
        monkeypatch.setattr(sync, 'security_gate', gate)
    elif failure == 'source-moved':
        model.source_heads = {**model.source_heads, 'melange': '9'*40}
    elif failure == 'remote-moved':
        model.remote = '9'*40
    else:
        def cas(*a):
            raise sync.core.ReleaseError('CAS', 'fixture concurrent writer')
        monkeypatch.setattr(sync.export, 'set_ref', cas)
    with pytest.raises(sync.core.ReleaseError):
        sync.synchronize(cfg, apply=True, push=True, state_root=state)
    assert model.local == '1'*40 and model.pushes == 0
    assert not (state / 'latest.json').exists()


def test_failed_push_can_retry_without_build_or_scan(lane, monkeypatch):
    cfg, state, model = lane
    real_push = sync.core.git
    def failed(*a, **kw):
        raise sync.core.ReleaseError('PUSH_FAILED', 'fixture network outage')
    monkeypatch.setattr(sync.core, 'git', failed)
    with pytest.raises(sync.core.ReleaseError, match='network outage'):
        sync.synchronize(cfg, apply=True, push=True, state_root=state)
    assert model.local == '2'*40 and model.remote == '1'*40
    assert not json.loads((state / 'latest.json').read_text())['pushed']
    monkeypatch.setattr(sync.core, 'git', real_push)
    assert sync.synchronize(cfg, apply=True, push=True, state_root=state)['status'] == 'unchanged'
    assert (model.builds, model.scans, model.pushes) == (1, 1, 1)


def test_uncertain_push_success_is_verified_without_repeat(lane, monkeypatch):
    cfg, state, model = lane
    real_push = sync.core.git
    def uncertain(*a, **kw):
        real_push(*a, **kw)
        raise sync.core.ReleaseError('PUSH_FAILED', 'fixture response lost after success')
    monkeypatch.setattr(sync.core, 'git', uncertain)
    with pytest.raises(sync.core.ReleaseError):
        sync.synchronize(cfg, apply=True, push=True, state_root=state)
    assert sync.synchronize(cfg, apply=True, push=True, state_root=state)['status'] == 'unchanged'
    assert (model.builds, model.scans, model.pushes) == (1, 1, 1)


def test_interruption_between_cas_and_latest_receipt_recovers(lane, monkeypatch):
    cfg, state, model = lane
    original = sync.save
    def fail_latest(path, value):
        if path.name == 'latest.json':
            raise OSError('fixture interruption')
        original(path, value)
    monkeypatch.setattr(sync, 'save', fail_latest)
    with pytest.raises(OSError):
        sync.synchronize(cfg, apply=True, state_root=state)
    assert model.local == '2'*40 and not (state / 'latest.json').exists()
    monkeypatch.setattr(sync, 'save', original)
    assert sync.synchronize(cfg, apply=True, push=True, state_root=state)['status'] == 'unchanged'
    assert (model.builds, model.scans, model.pushes) == (1, 1, 1)


def test_pending_push_never_overwrites_external_remote_writer(lane):
    cfg, state, model = lane
    sync.synchronize(cfg, apply=True, state_root=state)
    model.remote = '9'*40
    with pytest.raises(sync.core.ReleaseError, match='remote platform main moved'):
        sync.synchronize(cfg, apply=True, push=True, state_root=state)
    assert model.pushes == 0


def test_routing_requires_pinned_refs_origins_and_existing_bare(tmp_path, monkeypatch):
    bare = tmp_path / 'bare'
    bare.mkdir(); (bare / 'HEAD').write_text('ref: refs/heads/main')
    cfg = {'platform_remote_url': sync.PLATFORM_ORIGIN, 'platform_bare': str(bare),
           'melange_live': 'main', 'mentat_live': 'mentat', 'melange_ref': 'main',
           'mentat_ref': 'codex/mentat-crypto'}
    monkeypatch.setattr(sync.core, 'git_text', lambda *a, **kw: sync.PLATFORM_ORIGIN if 'git_dir' in kw else sync.SOURCE_ORIGIN)
    assert sync.check_routing(cfg) == bare
    for key, value in [('melange_ref', 'HEAD'), ('mentat_ref', 'main'), ('platform_remote_url', 'https://example.com/repo')]:
        with pytest.raises(sync.core.ReleaseError):
            sync.check_routing({**cfg, key: value})
    monkeypatch.setattr(sync.core, 'git_text', lambda *a, **kw: 'https://example.com/wrong-source')
    with pytest.raises(sync.core.ReleaseError):
        sync.check_routing(cfg)


def test_lock_excludes_second_writer(tmp_path):
    with sync.serialized(tmp_path):
        with pytest.raises(sync.core.ReleaseError, match='another source synchronization'):
            with sync.serialized(tmp_path):
                pytest.fail('lock did not exclude another writer')


@pytest.mark.parametrize('push_urls', ['https://example.com/wrong.git', sync.PLATFORM_ORIGIN + '\nhttps://example.com/extra.git'])
def test_alternate_or_multiple_push_urls_are_refused(tmp_path, monkeypatch, push_urls):
    monkeypatch.setattr(sync.core, 'git_text', lambda *a, **kw: push_urls)
    with pytest.raises(sync.core.ReleaseError, match='pinned destination'):
        sync.check_push_routing({'platform_remote_url': sync.PLATFORM_ORIGIN}, tmp_path)


def test_push_routing_is_rechecked_after_verification(lane, monkeypatch):
    cfg, state, model = lane
    def changed(*a):
        raise sync.core.ReleaseError('SOURCE_ROUTING', 'fixture changed push URL')
    monkeypatch.setattr(sync, 'check_push_routing', changed)
    with pytest.raises(sync.core.ReleaseError, match='changed push URL'):
        sync.synchronize(cfg, apply=True, push=True, state_root=state)
    assert model.pushes == 0 and model.local == '2'*40


@pytest.mark.parametrize('push', [False, True])
def test_hidden_task_logs_and_only_invokes_source_sync(tmp_path, monkeypatch, push):
    from ops.auto import source_sync_task
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    invoked = []
    def invoke(argv):
        invoked.append(argv)
        print('fixture sync result')
        return 1
    monkeypatch.setattr(sync, 'main', invoke)
    assert source_sync_task.main(['--push'] if push else []) == 1
    assert invoked == [['--apply'] + (['--push'] if push else [])]
    assert 'fixture sync result' in (tmp_path / 'Melange/source-sync/scheduled.log').read_text()

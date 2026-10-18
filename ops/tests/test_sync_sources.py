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


def test_plan_still_works_and_apply_or_push_is_refused_before_any_work(lane):
    cfg, state, model = lane
    assert sync.synchronize(cfg, state_root=state)['status'] == 'plan'
    assert not state.exists()
    for kwargs in ({'apply': True}, {'push': True}, {'apply': True, 'push': True}):
        with pytest.raises(sync.core.ReleaseError, match='retired.*2026-10-15') as refused:
            sync.synchronize(cfg, state_root=state, **kwargs)
        assert refused.value.code == 'SOURCE_SYNC_RETIRED'
    assert not state.exists()
    assert (model.builds, model.scans, model.pushes, model.proof) == (0, 0, 0, 0) and model.local == '1'*40


@pytest.mark.parametrize('flags', [['--apply'], ['--push'], ['--apply', '--push']])
def test_cli_refuses_apply_and_push_naming_the_per_app_repositories(monkeypatch, capsys, flags):
    monkeypatch.setattr(sync.core, 'load_config', lambda: {})
    assert sync.main(flags) == 1
    err = capsys.readouterr().err
    assert 'retired' in err and '2026-10-15' in err and 'pjg93-222/Melange' in err and 'pjg93-222/Mentat' in err and 'pjg93-222/Geo' in err


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

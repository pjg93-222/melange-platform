"""Synchronize committed application source to platform main; no tags, data or releases."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.release_lib import core, export

SOURCE_ORIGIN = 'https://github.com/pjg93-222/Melange.git'
PLATFORM_ORIGIN = 'https://github.com/pjg93-222/melange-platform.git'


@contextlib.contextmanager
def serialized(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'source-sync.lock').open('a+b') as lock:
        lock.seek(0, os.SEEK_END)
        if not lock.tell():
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise core.ReleaseError('SOURCE_SYNC_BUSY', 'another source synchronization is running') from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check_routing(cfg):
    if cfg.get('platform_remote_url') != PLATFORM_ORIGIN:
        raise core.ReleaseError('SOURCE_ROUTING', 'review source synchronization before changing the destination repository')
    if cfg.get('melange_ref') != 'main' or cfg.get('mentat_ref') != 'codex/mentat-crypto':
        raise core.ReleaseError('SOURCE_ROUTING', 'review source synchronization before changing canonical source refs')
    for key in ('melange_live', 'mentat_live'):
        if core.git_text('remote', 'get-url', 'origin', cwd=cfg[key]) != SOURCE_ORIGIN:
            raise core.ReleaseError('SOURCE_ROUTING', 'source checkout origin does not match the configured application repository')
    bare = Path(cfg['platform_bare'])
    if not (bare / 'HEAD').is_file():
        raise core.ReleaseError('SOURCE_ROUTING', 'existing platform bare repository is required')
    if core.git_text('remote', 'get-url', 'origin', git_dir=bare) != cfg['platform_remote_url']:
        raise core.ReleaseError('SOURCE_ROUTING', 'platform origin differs from the release configuration')
    check_push_routing(cfg, bare)
    return bare


def check_push_routing(cfg, bare):
    push_urls = core.git_text('remote', 'get-url', '--push', '--all', 'origin', git_dir=bare).splitlines()
    if push_urls != [PLATFORM_ORIGIN] or cfg['platform_remote_url'] != PLATFORM_ORIGIN:
        raise core.ReleaseError('SOURCE_ROUTING', 'platform push must resolve to exactly the pinned destination')


def remote_head(cfg):
    lines = core.git_text('ls-remote', '--heads', cfg['platform_remote_url'], 'refs/heads/main').splitlines()
    if len(lines) != 1 or lines[0].split()[-1] != 'refs/heads/main':
        raise core.ReleaseError('REMOTE_PARENT', 'remote platform main must already exist')
    return lines[0].split()[0]


def source_heads(cfg):
    return {name: core.git_text('rev-parse', cfg[ref] + '^{commit}', cwd=cfg[checkout])
            for name, ref, checkout in (('melange', 'melange_ref', 'melange_live'), ('mentat', 'mentat_ref', 'mentat_live'))}


def security_gate(cfg, bare, plan, commit, root):
    report = root / ('security-' + commit + '.json')
    with tempfile.TemporaryDirectory(prefix='source-sync-', dir=root) as temporary:
        target = export.materialize(bare, plan, Path(temporary) / 'source')
        command = [sys.executable, str(Path(cfg['melange_live']) / 'ops/security_gate.py'), 'export',
                   '--target', str(target), '--report', str(report)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
        (root / ('security-' + commit + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise core.ReleaseError('SOURCE_SECURITY', 'complete exported-source security gate failed; see ' + str(report))
    return str(report)


def finish_push(cfg, bare, pending, state_path):
    if pending.get('platformRemote') != cfg['platform_remote_url']:
        raise core.ReleaseError('SOURCE_ROUTING', 'pending synchronization targets a different repository')
    if export.rev(bare, 'refs/heads/main') != pending['candidate']:
        raise core.ReleaseError('LOCAL_PARENT', 'platform main moved; pending synchronization needs review')
    observed = remote_head(cfg)
    if observed != pending['candidate']:
        if observed != pending['remoteParent']:
            raise core.ReleaseError('REMOTE_PARENT', 'remote platform main moved; refusing to overwrite it')
        check_push_routing(cfg, bare)
        # Ordinary fast-forward push only. A concurrent remote writer is never forced.
        core.git('push', 'origin', pending['candidate'] + ':refs/heads/main', git_dir=bare)
        if remote_head(cfg) != pending['candidate']:
            raise core.ReleaseError('REMOTE_PARENT', 'remote changed during synchronization; inspect before retrying')
    pending['pushed'] = True
    save(state_path, pending)


def synchronize(cfg, *, apply=False, push=False, state_root=None):
    if push and not apply:
        raise core.ReleaseError('SOURCE_SYNC_USAGE', '--push requires --apply')
    root = Path(state_root or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Melange' / 'source-sync')
    bare = check_routing(cfg)
    # Plan is read-only and neither needs a signing key nor writes a lock/report.
    if not apply:
        plan = export.plan_export(cfg)
        return {'status': 'plan', 'sourceHeads': plan['heads'], 'diff': export.diff_against(bare, plan),
                'sourceRefs': {'melange': cfg['melange_ref'], 'mentat': cfg['mentat_ref']}, 'pushed': False}
    with serialized(root):
        state_path = root / 'latest.json'
        pending = json.loads(state_path.read_text(encoding='utf-8')) if state_path.is_file() else None
        parent = export.rev(bare, 'refs/heads/main')
        if not parent:
            raise core.ReleaseError('LOCAL_PARENT', 'platform main is absent')
        # A process may stop after the CAS but before updating latest.json. The
        # exact candidate receipt was fsynced first, so recover only that commit.
        recovery_path = root / ('candidate-' + parent + '.json')
        if (not pending or pending.get('candidate') != parent) and recovery_path.is_file():
            recovered = json.loads(recovery_path.read_text(encoding='utf-8'))
            if recovered.get('candidate') != parent:
                raise core.ReleaseError('LOCAL_PARENT', 'candidate recovery receipt is inconsistent')
            pending = recovered
            save(state_path, pending)
        if pending and not pending.get('pushed') and pending.get('candidate') != parent:
            raise core.ReleaseError('LOCAL_PARENT', 'pending synchronization no longer matches local main')
        if push and pending and not pending.get('pushed'):
            finish_push(cfg, bare, pending, state_path)
        plan = export.plan_export(cfg)
        diff = export.diff_against(bare, plan)
        if not any(diff[name] for name in ('added', 'changed', 'removed')):
            if push and remote_head(cfg) != parent:
                raise core.ReleaseError('REMOTE_PARENT', 'unchanged source has an unexpected remote parent; inspect routing')
            return {'status': 'unchanged', 'commit': parent, 'sourceHeads': plan['heads'],
                    'pushed': bool(push), 'diffCounts': {name: 0 for name in ('added', 'changed', 'removed')}}
        expected_remote = pending['remoteParent'] if pending and not pending.get('pushed') else parent
        if push and remote_head(cfg) != expected_remote:
            raise core.ReleaseError('REMOTE_PARENT', 'remote platform main differs from the expected parent')
        message = 'Synchronize committed application sources\n\n' + '\n'.join(name + ': ' + sha for name, sha in sorted(plan['heads'].items())) + '\n'
        commit, tree = export.build_commit(bare, plan, cfg, parent=parent, date_unix=int(time.time()), message=message)
        export.prove(bare, commit, plan)
        report = security_gate(cfg, bare, plan, commit, root)
        if source_heads(cfg) != plan['heads']:
            raise core.ReleaseError('SOURCE_MOVED', 'source refs moved during verification; no platform ref changed')
        if push and remote_head(cfg) != expected_remote:
            raise core.ReleaseError('REMOTE_PARENT', 'remote platform main moved during verification')
        record = {'candidate': commit, 'parent': parent, 'remoteParent': expected_remote,
                  'platformRemote': cfg['platform_remote_url'],
                  'sourceHeads': plan['heads'], 'sourceRefs': {'melange': cfg['melange_ref'], 'mentat': cfg['mentat_ref']},
                  'tree': tree, 'securityReport': report, 'pushed': False,
                  'diffCounts': {name: len(diff[name]) for name in ('added', 'changed', 'removed')}}
        # Persist recovery intent before the CAS. A failed CAS never changes
        # latest.json; an interrupted save can recover by exact candidate ID.
        save(root / ('candidate-' + commit + '.json'), record)
        export.set_ref(bare, 'refs/heads/main', commit, parent)
        save(state_path, record)
        if push:
            finish_push(cfg, bare, record, state_path)
        return {'status': 'synchronized' if push else 'prepared-local', 'commit': commit, **record}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='verify and advance local platform main')
    parser.add_argument('--push', action='store_true', help='also fast-forward remote main; no tags or releases')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(synchronize(core.load_config(), apply=args.apply, push=args.push), indent=2))
        return 0
    except (core.ReleaseError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('Source synchronization stopped: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

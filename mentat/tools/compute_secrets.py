"""Windows side of the container secret handoff (D-173..D-182): the DPAPI FEC key store, decrypted into one per-run file.

A Linux container cannot read DPAPI, so run_job.ps1 calls `stage` before `docker compose run`, mounts the file as a Docker
secret (read-only, /run/secrets/fec_api_keys; compute_job.py reads it) and calls `wipe` in a finally block. The keys are written
nowhere else: not the repository, not OneDrive, not a log. Only key fingerprints (fec_key_store.key_id, the identifier already
safe to print) ever leave this module. A run that dies with the laptop leaves its folder behind; `sweep` removes those.

    python compute_secrets.py stage --run-id <id>     -> {"path", "count", "key_ids"}
    python compute_secrets.py wipe  --run-id <id>
    python compute_secrets.py sweep [--max-age-hours 24]
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

SECRET_NAME = 'fec_api_keys'
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}\Z')


def run_base():
    """%LOCALAPPDATA%\\Melange\\compute\\run: per-machine, never synced, never in a repository."""
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Melange' / 'compute' / 'run'


def run_dir(run_id, base=None):
    if not RUN_ID.match(run_id):
        raise ValueError('The run id may hold only letters, digits, dots, dashes and underscores.')
    return Path(base or run_base()) / run_id


def _owner_only(folder):
    """Grant the current user alone access to the folder and what it will hold (Windows ACL; nothing to do elsewhere)."""
    if os.name != 'nt':
        return
    user = os.environ['USERNAME']
    if os.environ.get('USERDOMAIN'):
        user = os.environ['USERDOMAIN'] + '\\' + user
    done =subprocess.run(['icacls', str(folder), '/inheritance:r', '/grant:r', user + ':(OI)(CI)F'], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError('Could not restrict the secret folder to the current user.')


def stage(run_id, loader=None, base=None, restrict=True):
    """Write the enabled FEC keys, one per line, to <base>/<run_id>/fec_api_keys; return {"path", "count", "key_ids"}.

    `loader` defaults to fec_key_store.load_keys (DPAPI, Windows only); tests pass fake keys."""
    if loader is None:
        from fec_key_store import load_keys as loader
    keys = loader()
    if not keys:
        raise ValueError('No enabled FEC keys remain. Save an enabled key before running a job that collects.')
    folder = run_dir(run_id, base)
    folder.mkdir(parents=True)                      # a second stage for the same run id is a bug, not a retry
    try:
        if restrict:
            _owner_only(folder)
        target = folder / SECRET_NAME
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(('\n'.join(keys) + '\n').encode('utf-8'))
    except BaseException:
        wipe(run_id, base)
        raise
    from fec_key_store import key_id
    return {'path': str(target), 'count': len(keys), 'key_ids': [key_id(key) for key in keys]}


def wipe(run_id, base=None):
    """Overwrite and delete the run's secret files and its folder; true when something was there. Never recursive."""
    folder = run_dir(run_id, base)
    if not folder.is_dir():
        return False
    for entry in folder.iterdir():
        if not entry.is_file():
            raise RuntimeError('Unexpected entry in a secret folder; nothing deleted beyond its files: ' + entry.name)
        with entry.open('r+b') as stream:
            stream.write(b'\0' * entry.stat().st_size)
            stream.flush()
            os.fsync(stream.fileno())
        entry.unlink()
    folder.rmdir()
    return True


def sweep(base=None, max_age_hours=24, now=None):
    """Wipe run folders older than the limit (a run that died with the laptop); returns their ids."""
    base, now, swept = Path(base or run_base()), now or time.time(), []
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            if folder.is_dir() and RUN_ID.match(folder.name) and now - folder.stat().st_mtime > max_age_hours * 3600:
                wipe(folder.name, base)
                swept.append(folder.name)
    return swept


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('stage', 'wipe', 'sweep'))
    parser.add_argument('--run-id')
    parser.add_argument('--max-age-hours', type=float, default=24)
    args = parser.parse_args(argv)
    if args.command == 'sweep':
        print(json.dumps({'swept': sweep(max_age_hours=args.max_age_hours)}))
        return 0
    if not args.run_id:
        parser.error('--run-id is required')
    if args.command == 'stage':
        print(json.dumps(stage(args.run_id)))
    else:
        print(json.dumps({'wiped': wipe(args.run_id)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())

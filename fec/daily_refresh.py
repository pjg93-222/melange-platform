"""Native daily gate for the full bills/media/Excel/dashboard refresh.

First-use and manual triggers observe the Windows local calendar day. Retry
triggers only finish an already observed day. A successful, owned full-pipeline
receipt is the only event that completes a day; launch, contention and failure
remain retryable. This entry point requires only Python's standard library.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from lane_file_io import replace_atomic
from paths import ROOT as ROOT_TEXT
from process_liveness import process_alive
try:
    from surfaces.glassbox_emit import emit as _gb
except Exception:  # pragma: no cover - the stream is optional, the gate is not
    def _gb(*_a, **_k):
        return None

ROOT = Path(ROOT_TEXT)
STATE = Path('data/engine/daily_refresh_state.json')
OBSERVATIONS = Path('data/engine/daily_refresh_observations')
WRAPPER_STATUS = Path('data/engine/congress_pipeline_status.json')
WRAPPER_RECEIPT = Path('data/engine/congress_pipeline_receipt.json')
MEDIA_RECEIPT = 'data/engine/daily_media_refresh_receipt.json'
SCHEMA = 'daily-refresh-state-v1'
ATTEMPT_ENV = 'MELANGE_DAILY_ATTEMPT_ID'
# started_epoch is one reading of the process clock; what it is compared with is read
# differently. Windows stamps a file from the coarse system tick, st_mtime and
# time.time() round one instant to different floats, and an ISO timestamp is floored
# to the microsecond, so a reading taken just AFTER started_epoch can sit marginally
# before it. Allow the second the pipeline wrapper already allows
# (validated_media_snapshot); anything an earlier attempt left is older by far more.
CLOCK_SLACK_SECONDS = 1
# W2 (DE-020): an unpassable day was rerun by the hourly retry 13+ times. A day gets at most
# this many full chain attempts; an attempt whose child only found the lease busy (BUSY) never
# ran the chain and does not count.
MAX_DAILY_ATTEMPTS = 2


class Busy(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def local_day():
    return datetime.now().astimezone().date().isoformat()


def worker_python():
    """The windowless scheduler entry starts a console worker with logged stdio."""
    executable = Path(sys.executable)
    if executable.name.lower() == 'pythonw.exe':
        executable = executable.with_name('python.exe')
        if not executable.is_file():
            raise RuntimeError('The canonical Python worker beside pythonw.exe is missing')
    return str(executable)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        # A concurrent reader of the state document must not be able to fail
        # the gate on the rename; retry through the brief lock window.
        replace_atomic(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def daily_lock(root):
    """A separate non-inheritable gate; never grant the pipeline writer lease."""
    path = Path(root) / '.daily_refresh.lock'
    token = uuid.uuid4().hex
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps({'pid': os.getpid(), 'token': token}).encode('ascii'))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            try:
                prior = read(path)
            except (OSError, ValueError):
                prior = {}
            alive = process_alive(prior.get('pid')) if isinstance(prior, dict) else False
            malformed = not isinstance(prior, dict) or not prior.get('pid')
            stale = not alive and (not malformed or time.time() - path.stat().st_mtime >= 30)
            if attempt == 0 and stale:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            raise Busy('Another daily refresh owns the gate')
    try:
        yield
    finally:
        try:
            if read(path).get('token') == token:
                path.unlink()
        except FileNotFoundError:
            pass


def load_state(root):
    path = root / STATE
    state = read(path) if path.exists() else {'schema': SCHEMA, 'days': {}}
    if state.get('schema') != SCHEMA or not isinstance(state.get('days'), dict):
        raise ValueError('Invalid daily refresh state; refusing to lose the completion history')
    for day, record in state['days'].items():
        if record.get('date') != day or not isinstance(record.get('attempts'), list):
            raise ValueError('Invalid daily refresh day record')
        success = record.get('success')
        if success and (success.get('include_media') is not True or not success.get('attempt_id')):
            raise ValueError('Invalid daily full-refresh completion record')
    return state


def save_state(root, state):
    state['updated_at'] = utc_now()
    write(root / STATE, state)


def inspect_child_status(root, attempt):
    """Recover child identity after a daily parent was interrupted at launch."""
    path = root / WRAPPER_STATUS
    try:
        status = read(path)
    except (FileNotFoundError, PermissionError):
        # Windows/OneDrive may briefly deny a read while the child atomically
        # replaces this advisory snapshot. Keep monitoring the known child;
        # only verified_completion can authorize a successful daily refresh.
        return
    except OSError as exc:
        if getattr(exc, 'winerror', None) not in {32, 33}:  # sharing/lock violation
            raise
        return
    if not isinstance(status, dict):
        raise ValueError('Invalid pipeline status document')
    if status.get('daily_attempt_id') != attempt['id']:
        return
    pid = status.get('owner_pid')
    if (status.get('schema') != 'congress-pipeline-status-v1'
            or status.get('include_media') is not True
            or type(pid) is not int or pid <= 0
            or not isinstance(status.get('run_dir'), str) or not status['run_dir']):
        raise ValueError('Invalid owned pipeline status')
    Path(status['run_dir']).resolve().relative_to((root / 'audits/congress_pipeline').resolve())
    if attempt.get('child_pid') and pid != attempt['child_pid']:
        raise ValueError('Pipeline owner changed within the daily attempt')
    attempt['child_pid'] = pid
    attempt['wrapper_run_dir'] = status['run_dir']


def verified_completion(root, attempt, *, current_media):
    """Accept only this child's complete full pipeline and immutable receipts."""
    if attempt.get('wrapper_run_dir'):
        receipt_path = Path(attempt['wrapper_run_dir']) / 'receipt.json'
    else:
        receipt_path = root / WRAPPER_RECEIPT
    receipt = read(receipt_path)
    pid = receipt.get('owner_pid')
    # A smoke run carries include_media because it runs the media and canonical
    # stages, but it collected a source subset and skipped press entirely. Only
    # its mode distinguishes it, so reject on the mode explicitly.
    if receipt.get('mode') == 'smoke':
        raise ValueError('A smoke pipeline run cannot complete the day')
    if (receipt.get('schema') != 'congress-pipeline-status-v1'
            or receipt.get('status') != 'PASS' or receipt.get('include_media') is not True
            or receipt.get('daily_attempt_id') != attempt['id']
            or not isinstance(pid, int) or pid <= 0
            or (attempt.get('child_pid') is not None and pid != attempt['child_pid'])):
        raise ValueError('No owned successful full bills/media pipeline receipt')
    started = datetime.fromisoformat(receipt['started_at']).timestamp()
    completed = datetime.fromisoformat(receipt['completed_at']).timestamp()
    oldest = attempt['started_epoch'] - CLOCK_SLACK_SECONDS
    if not oldest <= started <= completed <= time.time() + 300:
        raise ValueError('Pipeline completion does not belong to this attempt time window')
    directory = Path(receipt['run_dir']).resolve()
    directory.relative_to((root / 'audits/congress_pipeline').resolve())
    immutable = directory / 'receipt.json'
    if read(immutable) != receipt or immutable.stat().st_mtime < oldest:
        raise ValueError('Pipeline immutable completion receipt is missing or stale')
    media = directory / 'daily_media_refresh_receipt.json'
    bound = (receipt.get('artifacts') or {}).get(MEDIA_RECEIPT) or {}
    media_document = read(media)
    if (media_document.get('schema') != 'daily-media-refresh-receipt-v1'
            or media_document.get('status') != 'PASS' or not bound.get('sha256')
            or digest(media) != bound['sha256'] or media.stat().st_mtime < oldest):
        raise ValueError('Full refresh lacks a fresh hash-bound successful media receipt')
    if current_media and (digest(root / MEDIA_RECEIPT) != bound['sha256']
                          or (root / MEDIA_RECEIPT).stat().st_mtime < oldest):
        raise ValueError('Live media receipt no longer matches the completed pipeline')
    return {'attempt_id': attempt['id'], 'include_media': True, 'owner_pid': pid,
            'completed_at': receipt['completed_at'], 'receipt': str(immutable),
            'receipt_sha256': digest(immutable), 'media_receipt_sha256': bound['sha256']}


def finish_attempt(root, day, attempt, status, **extra):
    attempt.update(status=status, completed_at=utc_now(), **extra)
    day['status'] = status
    write(Path(attempt['audit_dir']) / 'attempt.json', attempt)


def reconcile(root, state):
    """A child can finish successfully even if its daily parent was killed."""
    active = False
    for day in state['days'].values():
        for attempt in day['attempts']:
            if attempt.get('status') != 'RUNNING':
                continue
            inspect_child_status(root, attempt)
            if process_alive(attempt.get('child_pid')):
                active = True
                continue
            try:
                completion = verified_completion(root, attempt, current_media=False)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                finish_attempt(root, day, attempt, 'INTERRUPTED', error=str(exc))
            else:
                day['success'] = completion
                finish_attempt(root, day, attempt, 'PASS', recovered_completion=True)
    return active


def terminate_tree(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, check=False, timeout=30)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=30)


def run(root=ROOT, *, trigger='manual', timeout_seconds=72 * 60 * 60):
    root = Path(root).resolve()
    if trigger not in {'first-use', 'retry', 'manual'}:
        raise ValueError('Unknown daily refresh trigger')
    day_key = local_day()
    invocation_id = uuid.uuid4().hex
    audit_dir = root / 'audits/daily_refresh' / day_key / invocation_id
    invocation = {'schema': 'daily-refresh-invocation-v1', 'id': invocation_id,
                  'date': day_key, 'trigger': trigger, 'started_at': utc_now()}
    audit_dir.mkdir(parents=True, exist_ok=False)
    write(audit_dir / 'invocation.json', invocation)
    observation_path = root / OBSERVATIONS / (day_key + '.json')
    if trigger in {'first-use', 'manual'} and not observation_path.exists():
        # Preserve first-use even if an older day's still-running job holds the
        # gate. A timer retry may consume this observation but never creates it.
        write(observation_path, {'schema': 'daily-refresh-observation-v1', 'date': day_key,
                                 'observed_at': utc_now(), 'trigger': trigger})

    def result(status, code, **extra):
        invocation.update(status=status, completed_at=utc_now(), **extra)
        write(audit_dir / 'invocation.json', invocation)
        print(json.dumps({'status': status, 'date': day_key, **extra}), flush=True)
        return code

    try:
        with daily_lock(root):
            state = load_state(root)
            if day_key not in state['days']:
                if not observation_path.exists():
                    return result('WAIT_FIRST_USE', 0)
                observed = read(observation_path)
                if (observed.get('schema') != 'daily-refresh-observation-v1'
                        or observed.get('date') != day_key or observed.get('trigger') not in {'first-use', 'manual'}):
                    raise ValueError('Invalid first-use observation')
                state['days'][day_key] = {'date': day_key, 'observed_at': observed['observed_at'],
                                         'observed_trigger': observed['trigger'], 'status': 'PENDING',
                                         'attempts': [], 'success': None}
            active = reconcile(root, state)
            save_state(root, state)
            day = state['days'][day_key]
            if day.get('success'):
                return result('ALREADY_COMPLETE', 0)
            if active:
                return result('BUSY', 75, reason='Previous daily child is still running')
            ran = [a for a in day['attempts'] if a.get('status') != 'BUSY']
            if len(ran) >= MAX_DAILY_ATTEMPTS:
                reason = (f'{len(ran)} full chain attempts already ran on {day_key}; '
                          f'the cap is {MAX_DAILY_ATTEMPTS} per day')
                day.update(status='CAPPED', capped_reason=reason)
                save_state(root, state)
                _gb('daily_refresh.capped', 'blocked', 'Daily refresh capped: ' + reason,
                    source='daily-refresh', stage=trigger)
                return result('CAPPED', 0, reason=reason)
            attempt = {'id': invocation_id, 'status': 'RUNNING', 'trigger': trigger,
                       'owner_pid': os.getpid(), 'child_pid': None, 'audit_dir': str(audit_dir),
                       'started_at': utc_now(), 'started_epoch': time.time()}
            day['attempts'].append(attempt)
            day['status'] = 'RUNNING'
            save_state(root, state)
            write(audit_dir / 'attempt.json', attempt)
            environment = {**os.environ, 'MONEYBALL_ROOT': str(root), ATTEMPT_ENV: attempt['id'],
                           'PYTHONUNBUFFERED': '1', 'PYTHONUTF8': '1'}
            for name in ('MELANGE_PIPELINE_LOCK_TOKEN', 'MELANGE_PIPELINE_LOCK_OWNER_PID', 'DESK_OUT', 'DASH_OUT'):
                environment.pop(name, None)
            options = ({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                       if os.name == 'nt' else {'start_new_session': True})
            process = None
            try:
                with (audit_dir / 'pipeline.log').open('w', encoding='utf-8') as log:
                    process = subprocess.Popen([worker_python(), str(Path(__file__).resolve().parent /
                                                'legislation/refresh_congress_pipeline.py'), '--include-media'],
                                               cwd=root, env=environment, stdin=subprocess.DEVNULL,
                                               stdout=log, stderr=subprocess.STDOUT, **options)
                    attempt['child_pid'] = process.pid
                    save_state(root, state)
                    while process.poll() is None:
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            attempt['heartbeat_at'] = utc_now()
                            inspect_child_status(root, attempt)
                            save_state(root, state)
                            if time.time() - attempt['started_epoch'] > timeout_seconds:
                                raise TimeoutError('Daily full refresh exceeded its time limit')
                attempt['returncode'] = process.returncode
                if process.returncode:
                    busy = process.returncode == 75
                    finish_attempt(root, day, attempt, 'BUSY' if busy else 'FAILED',
                                   error=f'Full pipeline exited {process.returncode}')
                    save_state(root, state)
                    # Every 75 is BUSY, but the three causes are different
                    # operational facts. Name this one so a retry decision does
                    # not have to guess which gate was held.
                    return result(attempt['status'], process.returncode,
                                  **({'reason': 'pipeline lease busy (child exited 75)'} if busy else {}))
                day['success'] = verified_completion(root, attempt, current_media=True)
                finish_attempt(root, day, attempt, 'PASS')
                save_state(root, state)
                return result('PASS', 0, receipt=day['success']['receipt'])
            except BaseException as exc:
                if process is not None and process.poll() is None:
                    terminate_tree(process)
                finish_attempt(root, day, attempt, 'INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'FAILED',
                               error=f'{type(exc).__name__}: {exc}')
                save_state(root, state)
                return result(attempt['status'], 1, error=attempt['error'])
    except Busy as exc:
        return result('BUSY', 75, reason=str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return result('FAILED', 1, error=f'{type(exc).__name__}: {exc}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trigger', choices=('first-use', 'retry', 'manual'), default='manual')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    return run(args.root, trigger=args.trigger)


if __name__ == '__main__':
    raise SystemExit(main())

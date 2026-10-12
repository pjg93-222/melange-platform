"""One cross-process writer lease for collectors, pipelines, and publication surfaces.

``weekly_refresh`` owns the lease for its whole collector-to-publication transaction.  Its direct
``run_pipeline`` child may adopt (but never remove) that lease through a token-bound environment
handoff.  Standalone pipelines and scheduled refreshes therefore cannot overlap.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
import uuid

from paths import ROOT


LOCK_TOKEN_ENV = "MELANGE_PIPELINE_LOCK_TOKEN"
LOCK_OWNER_PID_ENV = "MELANGE_PIPELINE_LOCK_OWNER_PID"
MALFORMED_LOCK_GRACE_SECONDS = 30.0
# Two readings of the same kernel record differ only in rounding, so the tolerance is tight.
LOCK_PROC_START_TOLERANCE_SECONDS = 2.0


def _pid_alive(pid) -> bool:
    from process_liveness import process_alive
    return process_alive(pid)


def _holder_is_live(payload: dict) -> bool:
    """Is the process named in ``payload`` still the one that took this lease?

    Liveness by pid alone is not enough: Windows recycles pids, and a recycled pid made this
    lease immortal -- the file named a pid some unrelated process now owned, every peer read it
    as live, and the refresh stayed blocked until a human deleted the file by hand.

    The identity is the holder's OS process CREATION TIME, recorded at acquisition and compared
    with the live value now.  Both come from the SAME kernel record, so no wall-clock step can
    make a live holder look stale: a clock that moves between a process's start and its
    acquisition would break any comparison against a wall-clock ``started_utc``.

    Every uncertain path -- a lease written before ``proc_start`` existed, an unreadable start
    time, access denied -- returns True, because removing a live peer's lease is how two
    writers end up on the same artifacts.
    """
    pid = payload.get("pid")
    if not _pid_alive(pid):
        return False
    recorded = payload.get("proc_start")
    if recorded is None:
        return True          # a lease from before this key existed: the pid rule, unchanged
    from process_liveness import process_start_time
    live = process_start_time(pid)
    if live is None:
        return True
    try:
        return abs(float(live) - float(recorded)) <= LOCK_PROC_START_TOLERANCE_SECONDS
    except (TypeError, ValueError):
        return True


def _read_lock(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _stale_lock_may_be_removed(path: Path, payload: dict) -> bool:
    """Distinguish a dead owner from a live owner still writing its just-created lease.

    ``O_EXCL`` creates the file before the JSON write is visible.  A peer can therefore observe a
    momentarily empty file.  Treat a valid dead PID as stale immediately, but require a grace period
    before recovering malformed owner metadata so that peer can never unlink a fresh live lease.

    "Dead" also covers a RECYCLED pid: see :func:`_holder_is_live`.
    """
    if payload.get("pid") is not None:
        return not _holder_is_live(payload)
    try:
        return time.time() - path.stat().st_mtime >= MALFORMED_LOCK_GRACE_SECONDS
    except OSError:
        return False


def child_lock_environment(lease: dict) -> dict[str, str]:
    """Return the exact environment handoff accepted by a direct child process."""
    return {
        LOCK_TOKEN_ENV: str(lease["token"]),
        LOCK_OWNER_PID_ENV: str(lease["pid"]),
    }


def valid_inherited_lease(path=None, *, direct_parent=False) -> bool:
    """Validate the token-bound writer lease inherited by a pipeline descendant.

    Only ``run_pipeline`` may *adopt* the lease and therefore still requires the owner to be its
    direct parent. Read-only integrity guards run deeper in that same subprocess tree; they may
    recognize the inherited token without gaining ownership or permission to remove the lease.
    """
    path = Path(path or (Path(ROOT) / ".pipeline_run.lock"))
    inherited_token = os.environ.get(LOCK_TOKEN_ENV) or ""
    inherited_owner = os.environ.get(LOCK_OWNER_PID_ENV) or ""
    current = _read_lock(path)
    try:
        owner_pid = int(inherited_owner)
    except (TypeError, ValueError):
        return False
    return bool(
        inherited_token
        and current.get("token") == inherited_token
        and int(current.get("pid") or -1) == owner_pid
        and (not direct_parent or os.getppid() == owner_pid)
        and _pid_alive(owner_pid)
    )


@contextmanager
def pipeline_run_lock(path=None, owner_kind="pipeline"):
    """Acquire or securely adopt the single stale-owner-recoverable writer lease."""
    path = Path(path or (Path(ROOT) / ".pipeline_run.lock"))
    inherited_token = os.environ.get(LOCK_TOKEN_ENV) or ""
    inherited_owner = os.environ.get(LOCK_OWNER_PID_ENV) or ""
    if inherited_token or inherited_owner:
        current = _read_lock(path)
        if not valid_inherited_lease(path, direct_parent=True):
            raise RuntimeError("invalid inherited Moneyball pipeline writer lease")
        yield {**current, "adopted": True, "path": str(path)}
        return

    token = uuid.uuid4().hex
    for attempt in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                # proc_start is the holder's IDENTITY, read from the same kernel record every
                # later peer reads; None when it cannot be read, which reads as "assume live".
                from process_liveness import process_start_time
                payload = {
                    "pid": os.getpid(), "token": token, "started_utc": time.time(),
                    "owner_kind": owner_kind,
                    "proc_start": process_start_time(os.getpid()),
                }
                os.write(fd, json.dumps(payload, separators=(",", ":")).encode("ascii"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            prior = _read_lock(path)
            if attempt == 0 and _stale_lock_may_be_removed(path, prior):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            raise RuntimeError(
                "Moneyball pipeline is already running (pid %s, owner %s)"
                % (prior.get("pid", "unknown"), prior.get("owner_kind", "unknown"))
            )
    lease = {**payload, "adopted": False, "path": str(path)}
    try:
        yield lease
    finally:
        current = _read_lock(path)
        if current.get("token") == token and int(current.get("pid") or -1) == os.getpid():
            try:
                path.unlink()
            except FileNotFoundError:
                pass

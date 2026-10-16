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
import re
import socket
import threading
import time
import uuid

from paths import ROOT


LOCK_TOKEN_ENV = "MELANGE_PIPELINE_LOCK_TOKEN"
LOCK_OWNER_PID_ENV = "MELANGE_PIPELINE_LOCK_OWNER_PID"
MALFORMED_LOCK_GRACE_SECONDS = 30.0
# Two readings of the same kernel record differ only in rounding, so the tolerance is tight.
LOCK_PROC_START_TOLERANCE_SECONDS = 2.0
# A lease from another host or pid namespace cannot be judged by pid.  Its holder proves it is
# alive by rewriting its heartbeat file (``.pipeline_run.lock`` -> ``.pipeline_run.heartbeat.lock``)
# every HEARTBEAT_SECONDS; the lease is recoverable only once that file is older than the timeout
# (env override; invalid or <= 0 falls back).
HEARTBEAT_SECONDS = 30.0
LOCK_STALE_SECONDS_ENV = "MELANGE_LOCK_STALE_SECONDS"
LOCK_STALE_SECONDS_DEFAULT = 600.0
_CONTAINER_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def _pid_alive(pid) -> bool:
    from process_liveness import process_alive
    return process_alive(pid)


def _windows() -> bool:
    return os.name == "nt"


def _proc_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def host_identity() -> str:
    """Who can see this process's pid?  Equal identities share one pid space; anything else does not.

    Windows is ``nt:<hostname>``.  POSIX adds the boot id, the pid namespace and the container id,
    because a container shares its host's hostname only by accident and a restarted container is a
    new pid namespace whose pids mean nothing to a lease written by its predecessor.
    """
    host = socket.gethostname()
    if _windows():
        return "nt:" + host
    try:
        namespace = os.readlink("/proc/self/ns/pid")
    except OSError:
        namespace = ""
    container = ""
    for name in ("/proc/self/mountinfo", "/proc/self/cgroup"):
        found = _CONTAINER_ID.search(_proc_text(name))
        if found:
            container = found.group(0)
            break
    return "posix:%s:%s:%s:%s" % (host, _proc_text("/proc/sys/kernel/random/boot_id").strip(),
                                  namespace, container)


def _heartbeat_path(path) -> Path:
    """The lease's sibling heartbeat file: ``<name>.lock`` -> ``<name>.heartbeat.lock`` (any other name gets the suffix)."""
    path = Path(path)
    return path.with_name(path.name.removesuffix(".lock") + ".heartbeat.lock")


def _stale_seconds() -> float:
    try:
        value = float(os.environ.get(LOCK_STALE_SECONDS_ENV, ""))
    except ValueError:
        return LOCK_STALE_SECONDS_DEFAULT
    return value if 0 < value < float("inf") else LOCK_STALE_SECONDS_DEFAULT


def _same_pid_space(theirs) -> bool:
    """May ``theirs`` (a lease's ``host``) be judged by pid here?

    Windows judges every Windows lease -- including a legacy one with no ``host`` -- by pid, even
    after a rename.  POSIX judges only a lease written from this exact host/boot/namespace/container;
    a legacy lease was written by the Windows chain, whose pids a container cannot see.
    """
    if _windows():
        return not theirs or str(theirs).startswith("nt:")
    return bool(theirs) and theirs == host_identity()


def _foreign_holder_is_live(payload: dict, path) -> bool:
    """A lease from another pid space is held unless its OWN heartbeat proves the holder gone.

    Missing, unreadable, malformed or token-mismatched heartbeat => held (a Windows holder never
    writes one, and a container must not take over its lease).  A heartbeat dated in the future is
    clock skew (WSL2 drifts), which reads as fresh.

    A stale-looking heartbeat is not yet proof: a container frozen by laptop sleep resumes and beats
    again within one interval.  So before declaring the holder dead the mtime is re-read after
    ``2 * HEARTBEAT_SECONDS + 5``; the holder is live if it moved (or the file can no longer be
    stat'ed), dead only if it is unchanged.
    """
    token = payload.get("token")
    if path is None or not token:
        return True
    beat_path = _heartbeat_path(path)
    try:
        beat = json.loads(beat_path.read_text(encoding="utf-8"))
        seen = beat_path.stat().st_mtime
    except (OSError, ValueError, TypeError):
        return True
    if not isinstance(beat, dict) or beat.get("token") != token:
        return True
    if time.time() - seen <= _stale_seconds():
        return True
    time.sleep(2 * HEARTBEAT_SECONDS + 5)
    try:
        return beat_path.stat().st_mtime != seen
    except OSError:
        return True


def _write_heartbeat(path, token) -> None:
    """Atomically refresh the heartbeat.  A failure is swallowed, and a heartbeat that keeps failing makes the lease look stale to peers (it does NOT make them more conservative)."""
    beat_path = _heartbeat_path(path)
    temp = beat_path.with_name(beat_path.name + ".tmp")
    try:
        temp.write_text(json.dumps({"token": token, "host": host_identity(), "pid": os.getpid(),
                                    "ts": time.time()}, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, beat_path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass


def _start_heartbeat(path, token):
    """Write once synchronously, then keep rewriting on a daemon thread.  None on Windows."""
    if _windows():
        return None
    _write_heartbeat(path, token)
    stop = threading.Event()

    def beat():
        while not stop.wait(HEARTBEAT_SECONDS):
            _write_heartbeat(path, token)

    thread = threading.Thread(target=beat, name="pipeline-lock-heartbeat", daemon=True)
    thread.start()
    return stop, thread


def _stop_heartbeat(path, token, running) -> None:
    """Stop the thread, then delete the heartbeat file if (and only if) its token is ours."""
    if _windows():
        return
    if running is not None:
        running[0].set()
        running[1].join(timeout=5)
    beat_path = _heartbeat_path(path)
    try:
        if json.loads(beat_path.read_text(encoding="utf-8")).get("token") == token:
            beat_path.unlink()
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def _holder_is_live(payload: dict, path=None) -> bool:
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

    A lease from another host or pid namespace (``host``, see :func:`host_identity`) is not judged
    by pid at all: see :func:`_foreign_holder_is_live`.  ``path`` locates its heartbeat file;
    without it such a lease is held.
    """
    if not _same_pid_space(payload.get("host")):
        return _foreign_holder_is_live(payload, path)
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
        return not _holder_is_live(payload, path)
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
                    "host": host_identity(),
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
            message = ("Moneyball pipeline is already running (pid %s, owner %s)"
                       % (prior.get("pid", "unknown"), prior.get("owner_kind", "unknown")))
            if prior and not _same_pid_space(prior.get("host")):
                message += (", host %s; a lease from another host or container is recovered only"
                            " when %s is older than %.0f s (%s), else delete %s once its holder is gone"
                            % (prior.get("host") or "unknown", _heartbeat_path(path).name,
                               _stale_seconds(), LOCK_STALE_SECONDS_ENV, path))
            raise RuntimeError(message)
    lease = {**payload, "adopted": False, "path": str(path)}
    running = None
    try:
        running = _start_heartbeat(path, token)
        yield lease
    finally:
        _stop_heartbeat(path, token, running)
        current = _read_lock(path)
        if current.get("token") == token and int(current.get("pid") or -1) == os.getpid():
            try:
                path.unlink()
            except FileNotFoundError:
                pass

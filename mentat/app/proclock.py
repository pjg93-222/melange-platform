"""Cross-process lock files and process liveness, shared by the console and its helper processes.

Review finding M3 (2026-09-24): analysis_data.py (the Insights build markers), response_cache.py (its
build locks), drawer_sidecars.py (the drawer sidecars' build locks) and prewarm.py (the warmer's
liveness checks) each carried their own copy of this code, with staleness rules that had drifted apart.
All four use this module now. A lock decides who builds a key. It never decides what an answer is, and
no data or integrity rule depends on it.

Process liveness. process_started(pid) returns the creation time of a running process. The unit is
Windows FILETIME ticks: 100 ns since 1601-01-01 UTC. On Linux the value is derived from /proc. A pid that
the system has handed to a new process (a recycled pid) is told apart from the process that wrote a lock:
the pid is the same, the creation time is not. alive(pid, started) is True while that exact process runs.
On Windows the creation time is read with GetProcessTimes. A process this user cannot open (a session-0
service, csrss and other protected processes: OpenProcess answers ERROR_ACCESS_DENIED) is looked up in the
system's process list instead (NtQuerySystemInformation), which names every process's creation time
without opening any. A lock left behind by a dead builder whose pid now belongs to such a process is
therefore judged by the same rule as any other (review round 1 of batch 1, 2026-09-24).

Lock files. claim(path, key) creates `path` with O_CREAT|O_EXCL and writes one JSON object:
    {"pid": <this process>, "pid_started": <its creation time>, "started_utc": <time of the claim>,
     "key": <what is being built>}
plus any extra fields the caller asks for. The file's existence is the lock; its body names the holder.
inspect(path) reports whether a lock is live. release(path, body) removes a lock only while it still
holds exactly `body`. On Windows that check and the delete happen under one exclusive handle, so no
other process can replace the lock in between.

Readers never stand in the way of a release (review round 2 of batch 1, 2026-09-24). On Windows a file
opened by open() lacks FILE_SHARE_DELETE, and while any process has it open the release's delete is
refused. A reader caught between its open and its close, for instance a below-normal warmer preempted
under load, made the release give up after about 0.2 s. The lock it left behind still named a running
process, so every other process judged it live for as long as that process ran. This module therefore
reads a lock through a handle shared for reading, writing and deleting (_open_shared). A release that is
still refused, by a console on older code or by any other program that holds the file open, does not
drop the lock: it is kept as a leftover and removed by a background thread once the other handle
closes (retry_leftovers), still only while it holds exactly the body it was released with.

The staleness rule. One rule covers every lock this module reads:
  - A lock is stale only when its process is gone or its pid was recycled. Its age never matters while
    that can be read: a live builder keeps its lock for as long as its build takes (review finding H2).
  - A lock naming this process is live exactly while this process holds it (claimed and not yet
    released). Every caller admits one builder per key within a process, so a lock of this process
    that it does not hold was left behind by it.
  - A body that cannot be read or parsed is being written at that instant, or was abandoned by a
    writer that died mid-write. It is live for UNREADABLE_STALE_SECONDS after its last write and stale
    after that.
  - A process whose creation time cannot be read by any means (on Windows only when the system's
    process list cannot be read; on POSIX without a readable /proc) cannot be told apart from a
    recycled pid. Its lock is live for UNKNOWN_START_STALE_SECONDS after the claim and stale after
    that, the rule every lock followed before H2. These two are the only age limits.

Compatibility with a console still running the old code. The old modules' lock bodies are read by the
same rule:
  - analysis_data and response_cache wrote {pid, started_utc, key}.
  - drawer_sidecars wrote {pid, started, created, token}.
A body with pid_started, or drawer's `started`, names the holder's creation time exactly. A body with
only started_utc or `created` names the moment of the claim; the holder existed then, so a process
created more than RECYCLE_SLACK_SECONDS later holds a recycled pid.

Every body written here keeps the keys the old readers parse. It always has pid and started_utc, and
drawer_sidecars adds started, created and token. An old console therefore still respects a live lock
written by new code. The old readers do keep their own 30-minute age rule, so a console on old code
can still break a live lock after 30 minutes until it restarts. That is the only difference.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import threading
import time
from typing import NamedTuple, Optional
import uuid

UNREADABLE_STALE_SECONDS = 60
UNKNOWN_START_STALE_SECONDS = 30 * 60   # the pre-H2 age rule, kept only where no creation time is readable
RECYCLE_SLACK_SECONDS = 2
EPOCH_TICKS = 116444736000000000       # FILETIME ticks at 1970-01-01T00:00:00Z
TICKS_PER_SECOND = 10 ** 7
# A creation time computed on Linux (boot time + clock ticks) can move by up to this much between reads.
POSIX_START_TOLERANCE_TICKS = TICKS_PER_SECOND
PID_LIMIT = 2 ** 32
BODY_LIMIT = 1 << 16
RELEASE_ATTEMPTS = 5
# The background retry of a release another process's open handle refused: first wait, then doubling up to
# the last value, until every leftover is removed or has changed (retry_leftovers).
LEFTOVER_RETRY_SECONDS = (0.1, 5.0)

_log = logging.getLogger('mentat.proclock')
_now = time.time                       # the clock of the unreadable-body rule (tests move it)
_held = {}                             # normalised path -> the bytes this process wrote there
_leftovers = {}                        # normalised path -> the bytes a refused release was to remove
_retrier = None                        # the thread retrying the leftovers while there are any
_held_lock = threading.Lock()
_own = (None, None)                    # (pid, creation time) of this process
_boot = []


class Found(NamedTuple):
    """What inspect() found at a lock path."""
    live: bool
    raw: Optional[bytes]               # the bytes read; None when the file could not be read
    reason: str
    info: Optional[dict]               # the parsed body; None when it is unreadable or partial

    @property
    def pid(self):
        return self.info['pid'] if self.info else None


# ----------------------------------------------------------------------------- processes
_win = None


def _windows():
    """(ctypes, wintypes, kernel32) with every signature this module calls declared once."""
    global _win
    if _win is None:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetCurrentProcess.argtypes = ()
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.SetFileInformationByHandle.restype = wintypes.BOOL
        kernel.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        _win = (ctypes, wintypes, kernel)
    return _win


_listing = None
_listing_size = [1 << 20]              # bytes; grows to what the system's process list needs
SYSTEM_PROCESS_INFORMATION = 5
STATUS_INFO_LENGTH_MISMATCH = -0x3FFFFFFC   # 0xC0000004 as a signed NTSTATUS


def _listing_api():
    """(ctypes, ntdll, Entry): NtQuerySystemInformation and the head of one SYSTEM_PROCESS_INFORMATION
    record (ntexapi.h), up to UniqueProcessId; ctypes lays it out for this interpreter's word size."""
    global _listing
    if _listing is None:
        ctypes, _wintypes, _kernel = _windows()
        ntdll = ctypes.WinDLL('ntdll')
        ntdll.NtQuerySystemInformation.restype = ctypes.c_long
        ntdll.NtQuerySystemInformation.argtypes = (ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                                   ctypes.POINTER(ctypes.c_ulong))

        class UnicodeString(ctypes.Structure):
            _fields_ = [('Length', ctypes.c_ushort), ('MaximumLength', ctypes.c_ushort), ('Buffer', ctypes.c_void_p)]

        class Entry(ctypes.Structure):
            _fields_ = [('NextEntryOffset', ctypes.c_ulong), ('NumberOfThreads', ctypes.c_ulong),
                        ('WorkingSetPrivateSize', ctypes.c_longlong), ('HardFaultCount', ctypes.c_ulong),
                        ('NumberOfThreadsHighWatermark', ctypes.c_ulong), ('CycleTime', ctypes.c_ulonglong),
                        ('CreateTime', ctypes.c_longlong), ('UserTime', ctypes.c_longlong),
                        ('KernelTime', ctypes.c_longlong), ('ImageName', UnicodeString),
                        ('BasePriority', ctypes.c_long), ('UniqueProcessId', ctypes.c_void_p)]
        _listing = (ctypes, ntdll, Entry)
    return _listing


def started_listed(pid):
    """The creation time of `pid` from the system's process list (NtQuerySystemInformation,
    SystemProcessInformation), which needs no access to the process itself: FILETIME ticks, None when no
    running process has this pid, 0 when the list cannot be read. Windows only. process_started uses it
    for the processes this user cannot open. An exited process is not listed, even while a handle keeps
    its pid reserved; a listed one runs, whatever its thread count ('Secure System' lists none)."""
    ctypes, ntdll, Entry = _listing_api()
    size = _listing_size[0]
    for _ in range(8):
        buffer = ctypes.create_string_buffer(size)
        needed = ctypes.c_ulong(0)
        status = ntdll.NtQuerySystemInformation(SYSTEM_PROCESS_INFORMATION, buffer, size, ctypes.byref(needed))
        if status != STATUS_INFO_LENGTH_MISMATCH:
            break
        size = max(size * 2, needed.value + (1 << 16))   # the list grew since the size was reported
    else:
        return 0
    if status < 0:
        return 0
    _listing_size[0] = size
    offset, head = 0, ctypes.sizeof(Entry)
    while offset + head <= size:
        entry = Entry.from_buffer(buffer, offset)
        if (entry.UniqueProcessId or 0) == pid:
            return entry.CreateTime if entry.CreateTime > 0 else 0
        if not entry.NextEntryOffset:
            return None
        offset += entry.NextEntryOffset
    return 0


def _started_windows(pid):
    ctypes, wintypes, kernel = _windows()
    handle = kernel.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        if ctypes.get_last_error() == 5:   # ERROR_ACCESS_DENIED: it exists, and this user may not open it
            return started_listed(pid)
        return None
    try:
        code = wintypes.DWORD()
        if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:   # STILL_ACTIVE
            return None   # exited; a handle someone still holds keeps the pid reserved
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return started_listed(pid)
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def _boot_seconds():
    if not _boot:
        value = None
        with contextlib.suppress(OSError, ValueError):
            for line in Path('/proc/stat').read_text().splitlines():
                if line.startswith('btime '):
                    value = int(line.split()[1])
                    break
        _boot.append(value)
    return _boot[0]


def _started_posix(pid):
    try:
        os.kill(pid, 0)   # signal 0 only tests for the process; it is never sent on Windows
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    except OSError:
        return None
    try:
        raw = Path(f'/proc/{pid}/stat').read_bytes()
        fields = raw[raw.rindex(b')') + 2:].split()
    except (OSError, ValueError):
        return 0   # it runs; its creation time cannot be read here (no /proc)
    if fields and fields[0] in (b'Z', b'X'):
        return None   # a zombie has exited
    boot = _boot_seconds()
    try:
        if boot is None:
            return 0
        seconds = boot + int(fields[19]) / os.sysconf('SC_CLK_TCK')   # field 22, starttime
    except (IndexError, ValueError, OSError):
        return 0
    return EPOCH_TICKS + int(round(seconds * TICKS_PER_SECOND))


def process_started(pid):
    """The creation time of a running process as FILETIME ticks (an int), 0 when it runs but its creation
    time cannot be read, None when no process with this pid is running. On Windows a process this user
    cannot open is found in the system's process list (started_listed), so 0 there means that list could
    not be read either. It only queries: on Windows os.kill(pid, 0) would terminate the process it names,
    so it is never called there."""
    try:
        pid = int(pid)
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0 < pid < PID_LIMIT:
        return None
    return _started_windows(pid) if os.name == 'nt' else _started_posix(pid)


def own_started():
    """This process's creation time (process_started(os.getpid())), read once per process."""
    global _own
    pid, value = _own
    if pid != os.getpid() or value is None:
        value = process_started(os.getpid()) or 0
        _own = (os.getpid(), value)
    return value


def _ticks(value):
    """A recorded creation time, or None when the value is absent or not one."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def same_start(current, recorded):
    """Whether two creation times name the same process: equal on Windows, where the value is exact;
    within POSIX_START_TOLERANCE_TICKS elsewhere."""
    if os.name == 'nt':
        return current == recorded
    return abs(current - recorded) <= POSIX_START_TOLERANCE_TICKS


def alive(pid, started=None):
    """True while `pid` runs. With `started` (its creation time as process_started gives it), a pid now
    held by another process (recycled) counts as gone."""
    current = process_started(pid)
    if current is None:
        return False
    recorded = _ticks(started)
    return not (recorded and current and not same_start(current, recorded))


# ----------------------------------------------------------------------------- lock bodies
def lock_body(key, extra=None):
    """The bytes claim() writes: {pid, pid_started, started_utc, key} and then `extra`'s other fields."""
    body = {'pid': os.getpid(), 'pid_started': own_started() or None,
            'started_utc': datetime.now(timezone.utc).isoformat(), 'key': key}
    for name, value in (extra or {}).items():
        body.setdefault(name, value)
    return json.dumps(body).encode('utf-8')


def _seconds(value):
    """A POSIX time from an ISO text (started_utc) or a number (drawer's `created`); None otherwise."""
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def parse(raw):
    """The body as a dict with a valid pid, or None when it is unreadable or partial."""
    try:
        info = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(info, dict):
        return None
    pid = info.get('pid')
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid < PID_LIMIT:
        return None
    return info


def _open_shared(path):
    """A binary read stream on `path` that never stands in the way of a release or a write. On Windows the
    file is opened with FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE: open() leaves out
    FILE_SHARE_DELETE, so a reader holding it made another process's release fail (module docstring)."""
    if os.name != 'nt':
        return open(path, 'rb')
    ctypes, _wintypes, kernel = _windows()
    import msvcrt
    # GENERIC_READ, shared for reading, writing and deleting; OPEN_EXISTING; FILE_ATTRIBUTE_NORMAL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):   # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND
            raise FileNotFoundError(2, 'No lock file', str(path))
        raise OSError(None, ctypes.FormatError(error).strip(), str(path), error)
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, 'rb')


def _read(path):
    with _open_shared(path) as stream:
        return stream.read(BODY_LIMIT + 1)


def _slot(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _holds(path, raw):
    with _held_lock:
        return _held.get(_slot(path)) == raw


def held():
    """{normalised path: body} of every lock this process holds now."""
    with _held_lock:
        return dict(_held)


# ----------------------------------------------------------------------------- inspect
def _unreadable(path, raw, reason):
    try:
        written = os.stat(path).st_mtime
    except FileNotFoundError:
        return None
    except OSError:
        return Found(True, raw, reason + '; its age cannot be read', None)
    if _now() - written < UNREADABLE_STALE_SECONDS:
        return Found(True, raw, reason + ', written moments ago', None)
    return Found(False, raw, reason + f', older than {UNREADABLE_STALE_SECONDS} s', None)


def _claimed(info, path):
    """When the lock was claimed (POSIX seconds): its started_utc, drawer's `created`, else the file's
    last write; None when none of them can be read."""
    for name in ('started_utc', 'created'):
        value = _seconds(info.get(name))
        if value is not None:
            return value
    if path is not None:
        with contextlib.suppress(OSError):
            return os.stat(path).st_mtime
    return None


def judge(info, raw, path=None):
    """(live, reason) for a parsed body under the staleness rule (module docstring)."""
    pid = info['pid']
    recorded = _ticks(info.get('pid_started')) or _ticks(info.get('started'))
    if pid == os.getpid():
        if path is not None and _holds(path, raw):
            return True, 'held by this process'
        own = own_started()
        if recorded and own and not same_start(own, recorded):
            return False, 'pid recycled: an earlier process with this pid wrote it'
        return False, 'left behind by this process, which no longer holds it'
    current = process_started(pid)
    if current is None:
        return False, 'process gone'
    if not current:
        # No creation time is readable here, so a recycled pid cannot be told apart: the lock ages out.
        claimed = _claimed(info, path)
        if claimed is not None and _now() - claimed >= UNKNOWN_START_STALE_SECONDS:
            return False, (f'process runs; its creation time cannot be read and the lock is older than '
                           f'{UNKNOWN_START_STALE_SECONDS} s')
        return True, 'process runs; its creation time cannot be read'
    if recorded:
        if same_start(current, recorded):
            return True, 'process runs'
        return False, 'pid recycled: another creation time'
    written = _seconds(info.get('started_utc'))
    if written is None:
        written = _seconds(info.get('created'))
    if written is not None and (current - EPOCH_TICKS) / TICKS_PER_SECOND > written + RECYCLE_SLACK_SECONDS:
        return False, 'pid recycled: its process was created after the lock'
    return True, 'process runs'


def inspect(path):
    """None when there is no lock at `path`; otherwise Found(live, raw, reason, info)."""
    path = Path(path)
    try:
        raw = _read(path)
    except FileNotFoundError:
        return None
    except OSError:
        # A release holds it this instant (Windows denies the read), or it cannot be read at all.
        return _unreadable(path, None, 'unreadable')
    info = parse(raw) if len(raw) <= BODY_LIMIT else None
    if info is None:
        return _unreadable(path, raw, 'partial or unreadable body')
    live, reason = judge(info, raw, path)
    return Found(live, raw, reason, info)


def is_stale(path):
    """True when a lock exists at `path` and is stale."""
    found = inspect(path)
    return found is not None and not found.live


# ----------------------------------------------------------------------------- claim and release
def claim(path, key=None, *, extra=None, attempts=3):
    """Create the lock at `path` (O_CREAT|O_EXCL) and return the bytes written; None while another live
    holder has it. A stale lock is removed (release-if-unchanged) and the claim retried. `key` defaults to
    the file's stem; `extra` adds fields to the body. OSError when no lock can be created here."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    slot = _slot(path)
    with _held_lock:
        previous = _held.get(slot)
    if previous is not None:
        try:
            current = _read(path)
        except OSError:
            current = None
        if current == previous:
            return None   # this process holds it already
        _forget(slot, previous)   # removed or replaced by someone else: no longer held
    for attempt in range(attempts):
        body = lock_body(path.stem if key is None else key, extra)
        with _held_lock:
            _held[slot] = body   # before the file exists, so this process never judges it left behind
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o644)
        except FileExistsError:
            _forget(slot, body)
            found = inspect(path)
            if found is not None:
                if found.live:
                    return None
                release(path, found.raw)
            continue
        except PermissionError:
            _forget(slot, body)
            # Windows refuses a create over a name that another process is deleting this instant.
            if attempt + 1 < attempts:
                time.sleep(.02 * (attempt + 1))
                continue
            if os.path.lexists(path):
                return None
            raise
        except BaseException:
            _forget(slot, body)
            raise
        try:
            view = memoryview(body)
            while view:
                view = view[os.write(descriptor, view):]
        except BaseException:
            os.close(descriptor)
            _forget(slot, body)
            with contextlib.suppress(OSError):
                os.unlink(path)   # this process just created it
            raise
        os.close(descriptor)
        return body
    return None


def _forget(slot, body):
    with _held_lock:
        if _held.get(slot) == body:
            del _held[slot]


def _wanted(raw, expected, written):
    """Whether a lock read under release() may be removed: it still holds `expected`, or with expected
    None it is still an unreadable body older than UNREADABLE_STALE_SECONDS."""
    if expected is not None:
        return raw == expected
    return (len(raw) > BODY_LIMIT or parse(raw) is None) and _now() - written() >= UNREADABLE_STALE_SECONDS


def _remove_if_windows(path, expected):
    ctypes, _wintypes, kernel = _windows()
    import msvcrt
    # GENERIC_READ | DELETE, shared for reading only: while this handle is open no other process can
    # write, rename or delete the file, so what is read is what is removed.
    handle = kernel.CreateFileW(str(path), 0x80000000 | 0x00010000, 0x1, None, 3, 0x80, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):   # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND
            raise FileNotFoundError(2, 'No lock file', str(path))
        raise OSError(None, ctypes.FormatError(error).strip(), str(path), error)
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        kernel.CloseHandle(handle)
        raise
    try:
        chunks = []
        while sum(map(len, chunks)) <= BODY_LIMIT:
            chunk = os.read(descriptor, BODY_LIMIT + 1)
            if not chunk:
                break
            chunks.append(chunk)
        if not _wanted(b''.join(chunks), expected, lambda: os.fstat(descriptor).st_mtime):
            return False
        # POSIX semantics (Windows 10 1709+) unlink the name at once; the older class at close.
        flags = ctypes.c_ulong(0x1 | 0x2)   # FILE_DISPOSITION_FLAG_DELETE | FILE_DISPOSITION_FLAG_POSIX_SEMANTICS
        if not kernel.SetFileInformationByHandle(handle, 21, ctypes.byref(flags), ctypes.sizeof(flags)):
            legacy = ctypes.c_ubyte(1)          # FILE_DISPOSITION_INFO.DeleteFile = TRUE
            if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(legacy), ctypes.sizeof(legacy)):
                error = ctypes.get_last_error()
                raise OSError(None, ctypes.FormatError(error).strip(), str(path), error)
        return True
    finally:
        os.close(descriptor)


def _remove_if_posix(path, expected):
    # rename() is atomic: the lock is judged under a private name, and put back if it is not the one
    # to remove (unless a new lock has taken the name meanwhile).
    grave = path.with_name(f'{path.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.release')
    os.rename(path, grave)
    try:
        if _wanted(_read(grave), expected, lambda: os.stat(grave).st_mtime):
            return True
        with contextlib.suppress(OSError):
            os.link(grave, path)
        return False
    finally:
        with contextlib.suppress(OSError):
            os.unlink(grave)


def _remover():
    return _remove_if_windows if os.name == 'nt' else _remove_if_posix


def release(path, expected):
    """Remove the lock at `path` only while it still holds `expected` (bytes this process wrote or read).
    With expected None, remove it only while its body is still unreadable and older than
    UNREADABLE_STALE_SECONDS (an abandoned partial lock). True when this call removed it. A lock this
    process held is no longer held after this call, whatever the outcome. When another process's open
    handle refuses the delete every time, the lock is kept as a leftover and a background thread removes
    it once that handle closes (retry_leftovers), so it never outlives its release for long."""
    path = Path(path)
    if expected is not None:
        _forget(_slot(path), expected)
    remove = _remover()
    for attempt in range(RELEASE_ATTEMPTS):
        try:
            return remove(path, expected)
        except FileNotFoundError:
            return False
        except OSError as error:   # opened by another process this instant
            if attempt + 1 == RELEASE_ATTEMPTS:
                if expected is None:
                    _log.warning('Could not remove the lock %s: %s', path.name, error)
                else:
                    _keep(_slot(path), expected)
                    _log.warning('Could not remove the lock %s yet (%s); retrying in the background',
                                 path.name, error)
                return False
            time.sleep(.02 * (attempt + 1))
    return False


def _keep(slot, body):
    """Keep a lock whose release was refused, and make sure the retry thread runs."""
    global _retrier
    with _held_lock:
        _leftovers[slot] = body
        if _retrier is None:
            _retrier = threading.Thread(target=_retry_loop, name='mentat-proclock-leftovers', daemon=True)
            _retrier.start()


def _retry_loop():
    global _retrier
    wait, longest = LEFTOVER_RETRY_SECONDS
    while True:
        time.sleep(wait)
        wait = min(wait * 2, longest)
        try:
            retry_leftovers()
        except Exception:   # noqa: BLE001 - the thread must outlive a failed try
            _log.warning('Retrying a lock removal failed.', exc_info=True)
        with _held_lock:
            if not _leftovers:
                _retrier = None   # under the lock: a later _keep starts a new thread
                return


def leftovers():
    """{normalised path: body} of every lock this process released but could not remove yet."""
    with _held_lock:
        return dict(_leftovers)


def retry_leftovers():
    """Try once more to remove each leftover (release-if-unchanged). A leftover that is gone, or that now
    holds another body, is forgotten and left as it is; one that is still refused is kept for the next
    try. The paths removed."""
    remove, removed = _remover(), []
    for slot, body in leftovers().items():
        try:
            done = remove(Path(slot), body)
        except FileNotFoundError:
            done = False
        except OSError:
            continue
        with _held_lock:
            if _leftovers.get(slot) == body:
                del _leftovers[slot]
        if done:
            removed.append(slot)
    return removed


def release_all():
    """Release every lock this process holds (release-if-unchanged). The paths removed."""
    released = []
    for slot, body in held().items():
        if release(slot, body):
            released.append(slot)
    return released

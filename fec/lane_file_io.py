#!/usr/bin/env python3
"""Shared, concurrency-safe and BOM-tolerant file I/O for the fec lane.

Two cross-cutting defects live here so they cannot drift again.

Durable atomic writes
---------------------
Nine producers used a *fixed* scratch name -- ``<name>.tmp`` or ``.<name>.tmp``,
on a receiver called either ``path`` or ``destination`` -- with no process
identity and no ``fsync``.  Six staged a body write; three staged a *copy*
(``shutil.copyfile`` into the scratch file, hash-verify, ``os.replace``), which
is why the copy sites were found a pass later than the rest.  This repo
demonstrably runs several producers at once (per-PID manifests exist
throughout), so two processes writing the same output clobbered each other's
scratch file and one of them published a torn result.  The tree also lives
inside OneDrive, which happily syncs a stable ``*.tmp`` name and mints a
conflict copy from it.

:func:`atomic_write_bytes` merges the two good local patterns that already
existed in the lane -- ``build_legislation_candidate_evidence.atomic_write``
(``tempfile.mkstemp`` + ``fsync``) and
``run_legislation_review_batches.atomic_write`` (PID-suffixed scratch name and
an ``os.replace`` retry loop for the OneDrive/AV lock window) -- into one
implementation: a scratch name unique per process *and* per call, flushed and
fsync'd before publication, published with ``os.replace``, retried through
``PermissionError``, and cleaned up on every failure path.

BOM-tolerant NDJSON reading
---------------------------
NDJSON readers were split between ``utf-8`` and ``utf-8-sig``.  A UTF-8 BOM is
trivially easy to introduce on this workstation -- PowerShell 5.1's
``Out-File -Encoding utf8`` emits one -- and a ``utf-8`` reader raises on the
first line of a BOM-prefixed file.  Reading with ``utf-8-sig`` is strictly
more permissive than ``utf-8``: it strips a leading BOM if present and is
otherwise byte-identical in behaviour, so adopting it everywhere costs nothing
and removes a whole class of "invalid JSON at line 1" failures.

Byte-oriented readers (the ones that hash raw lines into receipts) call
:func:`strip_utf8_bom` on the first physical line.  The BOM is an encoding
marker, not payload, so removing it before hashing keeps a receipt stable
across a BOM-prefixed and a BOM-free copy of the same records.

This module is side-effect free on import and depends only on the standard
library.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import random
import sys
import tempfile
import time
from typing import IO, Iterator


UTF8_BOM = b"\xef\xbb\xbf"
NDJSON_ENCODING = "utf-8-sig"

# The OneDrive / antivirus / concurrent-reader lock window on this workstation
# is short but real. The original bounds (8 attempts, exponential to 0.75s) came
# from run_legislation_review_batches.atomic_write and covered only PermissionError.
# They were not enough: on 2026-09-13 22:42Z the congress pipeline lost this race
# on data/engine/congress_pipeline_status.json and the whole scheduled run died
# with "WinError 5: Access is denied" on the rename, because the live watcher
# polls that file every five seconds. Widened to the sharing/lock codes as well,
# with a flat jittered backoff so ~40 attempts still bound the wait at a few
# seconds and independent writers do not resynchronise on the same retry beat.
_REPLACE_ATTEMPTS = 40
_REPLACE_BACKOFF = (0.05, 0.25)
# 5 ERROR_ACCESS_DENIED, 32 ERROR_SHARING_VIOLATION, 33 ERROR_LOCK_VIOLATION.
_TRANSIENT_REPLACE_WINERRORS = (5, 32, 33)


def strip_utf8_bom(raw: bytes) -> bytes:
    """Drop a leading UTF-8 BOM from ``raw`` if one is present."""

    return raw[len(UTF8_BOM):] if raw.startswith(UTF8_BOM) else raw


def strip_utf8_bom_text(text: str) -> str:
    """Drop a leading ``U+FEFF`` from already-decoded text."""

    return text[1:] if text.startswith("\ufeff") else text


def open_ndjson(path: Path, *, newline: str | None = None) -> IO[str]:
    """Open an NDJSON file for reading, tolerating a leading UTF-8 BOM."""

    return path.open("r", encoding=NDJSON_ENCODING, newline=newline)


def read_ndjson_text(path: Path) -> str:
    """Read a whole NDJSON file as text, tolerating a leading UTF-8 BOM."""

    return path.read_text(encoding=NDJSON_ENCODING)


def iter_ndjson_raw_lines(path: Path) -> Iterator[tuple[int, bytes]]:
    """Yield ``(line_number, raw_bytes)`` with the BOM removed from line 1.

    Line terminators are preserved; callers that hash receipts do their own
    ``rstrip(b"\\r\\n")`` exactly as before.
    """

    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            yield line_number, (strip_utf8_bom(raw) if line_number == 1 else raw)


def transient_replace_error(error: OSError) -> bool:
    """True when a rename failed because something briefly held the target.

    A reader, the sync client or a scanner holding the destination open is a
    few-millisecond condition, not a fault. Anything else -- a vanished scratch
    file, a bad path, a full disk -- is a real error and must surface at once.
    """

    if isinstance(error, PermissionError):
        return True
    return getattr(error, "winerror", None) in _TRANSIENT_REPLACE_WINERRORS


def replace_atomic(scratch, path, *, attempts: int = _REPLACE_ATTEMPTS, sleep=None) -> int:
    """``os.replace(scratch, path)``, retried through the lock window.

    Returns the attempt number that succeeded. On the final failure the scratch
    file is removed before the error propagates, so a lost race never leaves a
    sibling behind for OneDrive to sync or a later run to mistake for output.
    """

    scratch = os.fspath(scratch)
    path = os.fspath(path)
    pause = sleep if sleep is not None else time.sleep
    for attempt in range(1, attempts + 1):
        try:
            os.replace(scratch, path)
            return attempt
        except OSError as error:
            if attempt >= attempts or not transient_replace_error(error):
                try:
                    os.unlink(scratch)
                except OSError:
                    pass
                raise
            pause(random.uniform(*_REPLACE_BACKOFF))
    raise AssertionError("unreachable: attempts must be >= 1")


def atomic_write_bytes(path: Path, body: bytes) -> None:
    """Publish ``body`` at ``path`` atomically, durably, and concurrency-safely.

    The scratch file is created by :func:`tempfile.mkstemp` in the destination
    directory, so its name is unique per process and per call and can never be
    clobbered by a sibling producer writing the same output path.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, scratch_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name[:24]}.tmp-{os.getpid()}-",
        suffix=".part",
    )
    scratch = Path(scratch_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        replace_atomic(scratch, path)
    except BaseException:
        try:
            os.unlink(scratch)
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, body: str, *, encoding: str = "utf-8") -> None:
    """Text convenience wrapper around :func:`atomic_write_bytes`."""

    atomic_write_bytes(path, body.encode(encoding))


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_sha256_sidecar(path) -> str:
    """Record the saved model's digest in ``<path>.sha256`` for :func:`safe_to_unpickle`."""

    digest = sha256_file(path)
    atomic_write_text(Path(str(path) + ".sha256"), digest + "\n")
    return digest


def safe_to_unpickle(path) -> bool:
    """Check ``<path>.sha256`` before the caller unpickles ``path``; unpickling runs code.

    A matching sidecar returns True. A mismatched, empty or unreadable one
    returns False, loudly: the caller must not load the file. An absent sidecar
    warns and returns True, so a model saved before sidecars existed loads
    exactly as it did (refusing it would force a retrain that can move ranks).
    """

    sidecar = str(path) + ".sha256"
    try:
        with open(sidecar, "r", encoding="ascii") as handle:
            recorded = (handle.read().split() or [""])[0].lower()
    except FileNotFoundError:
        print(f"WARNING: no integrity sidecar {sidecar}; loading {path} unverified", file=sys.stderr, flush=True)
        return True
    except (OSError, UnicodeDecodeError) as error:
        recorded = f"unreadable sidecar: {error}"
    actual = sha256_file(path)
    if recorded == actual:
        return True
    print(f"SECURITY: {path} does not match its integrity sidecar {sidecar} "
          f"(recorded {recorded[:16]!r}, actual {actual[:16]!r}); refusing to unpickle it", file=sys.stderr, flush=True)
    return False

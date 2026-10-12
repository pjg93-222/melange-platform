#!/usr/bin/env python3
"""Live zero-network watcher for receipt-only Congress.gov timeout rollovers."""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from legislation import run_legislation_timeout_rollover as ROLLOVER


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DIR = (
    ROOT / "audits" / "legfix5" / "timeout_rollover_watcher"
)
LOCK_SCHEMA = "legislation-timeout-rollover-watcher-lock-v1"
PID_SCHEMA = "legislation-timeout-rollover-watcher-pid-v1"
HEARTBEAT_SCHEMA = "legislation-timeout-rollover-watcher-heartbeat-v1"


class WatcherError(ValueError):
    """Raised when watcher ownership or local receipt state is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WatcherError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def process_identity(pid: int) -> str | None:
    """Return a PID-reuse-safe process creation token."""

    if pid <= 0:
        return None
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return None
        try:
            creation = ctypes.c_uint64()
            exit_time = ctypes.c_uint64()
            kernel = ctypes.c_uint64()
            user = ctypes.c_uint64()
            ok = ctypes.windll.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            return str(creation.value) if ok else None
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        fields = stat_path.read_text(encoding="ascii").split()
    except OSError:
        return None
    return fields[21] if len(fields) > 21 else None


def process_matches(pid: Any, identity: Any) -> bool:
    return bool(
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and isinstance(identity, str)
        and identity
        and process_identity(pid) == identity
    )


def archive_lock(path: Path, *, bucket: str) -> Path:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    destination_dir = path.parent / bucket
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{digest}.json"
    if destination.exists():
        suffix = 1
        while (destination_dir / f"{digest}.{suffix}.json").exists():
            suffix += 1
        destination = destination_dir / f"{digest}.{suffix}.json"
    os.replace(path, destination)
    return destination


def lock_owned(lock_path: Path, expected: dict[str, Any]) -> bool:
    try:
        return read_object(lock_path) == expected
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def acquire_lock(
    state_dir: Path,
    *,
    main_pid: int,
    main_identity: str,
) -> tuple[bool, dict[str, Any], Path]:
    """Acquire atomically, archiving only proven stale/tampered locks."""

    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "watcher.lock.json"
    watcher_pid = os.getpid()
    watcher_identity = process_identity(watcher_pid)
    if watcher_identity is None:
        raise WatcherError("Cannot establish watcher process identity")
    lock = {
        "schema": LOCK_SCHEMA,
        "watcher_pid": watcher_pid,
        "watcher_process_identity": watcher_identity,
        "main_pid": main_pid,
        "main_process_identity": main_identity,
        "acquired_at": utc_now(),
        "poll_seconds": 30,
        "network_executed": False,
        "sync_or_promotion_performed": False,
    }
    for _ in range(3):
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            )
        except FileExistsError:
            try:
                existing = read_object(lock_path)
            except (OSError, ValueError, json.JSONDecodeError):
                existing = {}
            if (
                existing.get("schema") == LOCK_SCHEMA
                and process_matches(
                    existing.get("watcher_pid"),
                    existing.get("watcher_process_identity"),
                )
            ):
                return False, existing, lock_path
            archive_lock(lock_path, bucket="stale_locks")
            continue
        try:
            body = (
                json.dumps(
                    lock,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            os.write(descriptor, body)
        finally:
            os.close(descriptor)
        return True, lock, lock_path
    raise WatcherError("Could not acquire watcher lock")


def applied_manifest_sha256() -> str | None:
    pointer = ROLLOVER.CURRENT_POINTER
    if not pointer.is_file():
        return None
    try:
        value = read_object(pointer)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    receipt = value.get("selected_strict_manifest")
    if not ROLLOVER.receipt_is_current(receipt):
        return None
    return str(receipt["sha256"])


def watcher_decision(
    *,
    main_alive: bool,
    final_coverage_complete: bool,
    selected_sha256: str,
    applied_sha256: str | None,
) -> str:
    if not main_alive:
        return "EXIT_MAIN_PROCESS_ENDED"
    if final_coverage_complete:
        return "EXIT_FINAL_COVERAGE_COMPLETE"
    if selected_sha256 != applied_sha256:
        return "RUN_ROLLOVER"
    return "NOOP_CURRENT"


def heartbeat(
    *,
    lock: dict[str, Any],
    sequence: int,
    action: str,
    selection: dict[str, Any] | None,
    applied_sha256: str | None,
    helper_exit_code: int | None = None,
    helper_output: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": HEARTBEAT_SCHEMA,
        "status": (
            "WATCHING"
            if not action.startswith("EXIT_")
            else "EXITING"
        ),
        "heartbeat_at": utc_now(),
        "sequence": sequence,
        "watcher_pid": lock["watcher_pid"],
        "watcher_process_identity": lock["watcher_process_identity"],
        "main_pid": lock["main_pid"],
        "main_process_identity": lock["main_process_identity"],
        "action": action,
        "selected_strict_manifest": (
            selection.get("selected_strict_manifest")
            if selection
            else None
        ),
        "exact_query_count": (
            selection.get("exact_query_count") if selection else None
        ),
        "applied_manifest_sha256": applied_sha256,
        "helper_exit_code": helper_exit_code,
        "helper_output_tail": (
            helper_output[-4000:] if helper_output else None
        ),
        "poll_seconds": lock["poll_seconds"],
        "network_executed": False,
        "collector_process_modified": False,
        "candidate_exclusion_performed": False,
        "scoring_performed": False,
        "sync_or_promotion_performed": False,
        "production_write_performed": False,
    }


def run_watcher(
    *,
    main_pid: int,
    poll_seconds: int,
    state_dir: Path,
) -> int:
    main_identity = process_identity(main_pid)
    if main_identity is None:
        raise WatcherError("Main wrapper process is not alive")
    acquired, lock, lock_path = acquire_lock(
        state_dir,
        main_pid=main_pid,
        main_identity=main_identity,
    )
    if not acquired:
        print(
            json.dumps(
                {
                    "status": "ALREADY_RUNNING",
                    "watcher_pid": lock.get("watcher_pid"),
                    "lock": str(lock_path.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0
    lock["poll_seconds"] = poll_seconds
    atomic_json(lock_path, lock)
    pid_path = state_dir / "watcher_pid.json"
    heartbeat_path = state_dir / "heartbeat.json"
    pid_receipt = {
        "schema": PID_SCHEMA,
        "status": "RUNNING",
        "watcher_pid": lock["watcher_pid"],
        "watcher_process_identity": lock["watcher_process_identity"],
        "main_pid": main_pid,
        "main_process_identity": main_identity,
        "started_at": lock["acquired_at"],
        "lock_sha256": stable_sha256(lock),
        "lock": str(lock_path.resolve()),
        "heartbeat": str(heartbeat_path.resolve()),
        "network_executed": False,
        "sync_or_promotion_performed": False,
    }
    atomic_json(pid_path, pid_receipt)
    sequence = 0
    exit_action = "EXIT_WATCHER_ERROR"
    try:
        while True:
            sequence += 1
            if not lock_owned(lock_path, lock):
                atomic_json(
                    heartbeat_path,
                    heartbeat(
                        lock=lock,
                        sequence=sequence,
                        action="EXIT_LOCK_OWNERSHIP_LOST",
                        selection=None,
                        applied_sha256=applied_manifest_sha256(),
                    ),
                )
                exit_action = "EXIT_LOCK_OWNERSHIP_LOST"
                return 2
            main_alive = process_matches(main_pid, main_identity)
            if not main_alive:
                action = "EXIT_MAIN_PROCESS_ENDED"
                atomic_json(
                    heartbeat_path,
                    heartbeat(
                        lock=lock,
                        sequence=sequence,
                        action=action,
                        selection=None,
                        applied_sha256=applied_manifest_sha256(),
                    ),
                )
                exit_action = action
                return 0
            selection = ROLLOVER.selected_manifest_state()
            selected_sha = selection["selected_strict_manifest"]["sha256"]
            applied_sha = applied_manifest_sha256()
            action = watcher_decision(
                main_alive=True,
                final_coverage_complete=selection[
                    "final_coverage_complete"
                ],
                selected_sha256=selected_sha,
                applied_sha256=applied_sha,
            )
            helper_exit = None
            helper_output = None
            if action == "RUN_ROLLOVER":
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(Path(ROLLOVER.__file__).resolve()),
                        "--check",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                helper_exit = completed.returncode
                helper_output = (
                    (completed.stdout or "") + (completed.stderr or "")
                ).strip()
                if helper_exit == 0:
                    applied_sha = applied_manifest_sha256()
                    if applied_sha != selected_sha:
                        helper_exit = 2
                        helper_output += (
                            "\nhelper returned success without current pointer"
                        )
                action = (
                    "ROLLOVER_APPLIED"
                    if helper_exit == 0
                    else "ROLLOVER_BLOCKED_RETRY_PENDING"
                )
            atomic_json(
                heartbeat_path,
                heartbeat(
                    lock=lock,
                    sequence=sequence,
                    action=action,
                    selection=selection,
                    applied_sha256=applied_sha,
                    helper_exit_code=helper_exit,
                    helper_output=helper_output,
                ),
            )
            if action == "EXIT_FINAL_COVERAGE_COMPLETE":
                exit_action = action
                return 0
            time.sleep(poll_seconds)
    finally:
        stopped_at = utc_now()
        owned = lock_owned(lock_path, lock)
        archived_lock = (
            str(archive_lock(lock_path, bucket="completed_locks"))
            if owned
            else None
        )
        pid_receipt.update(
            {
                "status": "EXITED",
                "stopped_at": stopped_at,
                "exit_action": exit_action,
                "archived_lock": archived_lock,
            }
        )
        atomic_json(pid_path, pid_receipt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-pid", type=int, required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.poll_seconds <= 0:
        print("watcher failed closed: poll interval must be positive")
        return 2
    try:
        return run_watcher(
            main_pid=args.main_pid,
            poll_seconds=args.poll_seconds,
            state_dir=args.state_dir.resolve(),
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"watcher failed closed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

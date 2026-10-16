#!/usr/bin/env python3
"""Standalone, resumable legislation evidence-acceptance orchestrator.

This service intentionally stops at the evidence-ready/culling-preparation
boundary.  It never scores, synchronizes, promotes, publishes, or writes to the
mainframe.  Network collection is impossible unless the caller explicitly
selects ``--canary-only`` or ``--full``; full collection additionally requires
the literal ``--full`` guard.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence
import uuid


ROOT = Path(__file__).resolve().parents[2]
AUDIT_ROOT = ROOT / "audits" / "legfix5"
DEFAULT_STATE_DIR = AUDIT_ROOT / "legislation_acceptance_service"
DEFAULT_CONTROL_SCRIPT = ROOT / "fec" / "legislation" / "build_legislation_culling_control_plane.py"
DEFAULT_COLLECTOR_SCRIPT = (
    ROOT / "fec" / "legislation" / "collect_govinfo_bill_xml_versions.py"
)
DEFAULT_ARCHIVE_AUDIT_SCRIPT = (
    ROOT / "fec" / "audit" / "audit_congress_full_text_failure_catchnet.py"
)
DEFAULT_GOVINFO_OUT = AUDIT_ROOT / "govinfo_bill_xml_versions"
DEFAULT_HTML_RUN = (
    AUDIT_ROOT
    / "congress_advanced_pages_20260725"
    / "text_relevance_httr_full_stage_20260726"
)
DEFAULT_DISCOVERY = DEFAULT_HTML_RUN.parent / "congress_advanced_records.ndjson"
DEFAULT_CONTROL_OUTPUT = AUDIT_ROOT / "culling_integration" / "current.json"
DEFAULT_ARCHIVE_AUDIT_OUT = DEFAULT_HTML_RUN / "failure_catchnet"
DEFAULT_CONTROL_INPUTS = (
    AUDIT_ROOT / "congress_advanced_pages_20260725" / "manifest.json",
    DEFAULT_HTML_RUN / "progress.json",
    AUDIT_ROOT
    / "billstatus_bulk_enrichment"
    / "fetch_runs"
    / "c7ca822fd5951478a33a814d228b71ffdf69745e6d823001fefaba620282c435"
    / "run_manifest.json",
    AUDIT_ROOT
    / "congress_advanced_pages_20260725"
    / "candidate_membership_review"
    / "manifest.json",
    AUDIT_ROOT / "quorum_floor_20260726" / "reconciliation_receipt.json",
    AUDIT_ROOT
    / "identifier_probe_fast_20260726"
    / "failure_resolution_receipt.json",
    AUDIT_ROOT / "PRE_LEGISLATION_RANK_CONTROL_2026-07-25.json",
    AUDIT_ROOT / "legislation_junk_pile",
    AUDIT_ROOT / "culling_integration" / "candidate_evidence_manifest.json",
    AUDIT_ROOT / "culling_integration" / "candidate_partition_manifest.json",
)
SCHEMA = "legislation-acceptance-service-v1"
STAGE_RECEIPT_SCHEMA = "legislation-acceptance-stage-receipt-v1"

FORBIDDEN_POLICY = {
    "candidate_deletion_allowed": False,
    "scoring_allowed": False,
    "sync_allowed": False,
    "promotion_allowed": False,
    "production_write_allowed": False,
    "mainframe_write_allowed": False,
}


class ServiceError(RuntimeError):
    """The standalone acceptance service failed closed."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, body: bytes) -> None:
    """Durably replace a mutable state file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory = None
        if directory is not None:
            try:
                os.fsync(directory)
            except OSError:
                pass
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def immutable_write(path: Path, body: bytes) -> None:
    """Create an immutable file exclusively; identical prior bytes are valid."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if path.read_bytes() != body:
            raise ServiceError(f"immutable artifact collision: {path}")
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ServiceError(f"invalid JSON artifact {path}: {error}") from error
    if not isinstance(value, dict):
        raise ServiceError(f"JSON root is not an object: {path}")
    return value


def safe_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(resolved)


def path_receipt(path: Path) -> dict[str, Any]:
    """Hash one file or an entire directory tree without retaining file bodies."""

    resolved = path.resolve()
    if resolved.is_file():
        return {
            "path": safe_relative(resolved),
            "kind": "file",
            "bytes": resolved.stat().st_size,
            "sha256": sha256_file(resolved),
        }
    if resolved.is_dir():
        digest = hashlib.sha256()
        file_count = 0
        total_bytes = 0
        for item in sorted(
            (candidate for candidate in resolved.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(resolved).as_posix(),
        ):
            relative = item.relative_to(resolved).as_posix()
            item_hash = sha256_file(item)
            item_size = item.stat().st_size
            digest.update(
                canonical_bytes(
                    {
                        "path": relative,
                        "bytes": item_size,
                        "sha256": item_hash,
                    }
                )
            )
            file_count += 1
            total_bytes += item_size
        return {
            "path": safe_relative(resolved),
            "kind": "directory",
            "file_count": file_count,
            "bytes": total_bytes,
            "sha256": digest.hexdigest(),
        }
    return {"path": safe_relative(resolved), "kind": "missing"}


def receipts(paths: Iterable[Path]) -> list[dict[str, Any]]:
    return [path_receipt(path) for path in paths]


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, int(pid)
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class ServiceLock:
    """Exclusive single-writer lock with preserved stale-owner diagnosis."""

    def __init__(self, state_dir: Path, owner: Mapping[str, Any]):
        self.state_dir = state_dir.resolve()
        self.path = self.state_dir / "service.lock"
        self.owner = dict(owner)
        self.acquired = False

    def _publish(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        body = canonical_bytes(self.owner)
        try:
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
            )
        except FileExistsError as error:
            raise ServiceError("acceptance-service writer lock is already held") from error
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        self.acquired = True

    def acquire(self) -> "ServiceLock":
        try:
            self._publish()
            return self
        except ServiceError:
            pass
        try:
            prior = read_json(self.path)
        except ServiceError as error:
            raise ServiceError(
                f"writer lock is malformed and was not reclaimed: {self.path}"
            ) from error
        prior_host = str(prior.get("hostname") or "")
        local_host = socket.gethostname()
        prior_pid = int(prior.get("pid") or 0)
        child_pid = int(prior.get("child_pid") or 0)
        if prior_host != local_host:
            raise ServiceError(
                "writer lock belongs to another host and cannot be diagnosed safely: "
                f"{prior_host!r}"
            )
        if process_alive(prior_pid) or process_alive(child_pid):
            raise ServiceError(
                "acceptance-service writer is active: "
                f"pid={prior_pid}, child_pid={child_pid or None}"
            )
        stale_body = {
            "schema": "legislation-acceptance-stale-lock-v1",
            "diagnosed_at": utc_now(),
            "diagnosis": "same-host owner and child PIDs are not alive",
            "prior_owner": prior,
            "reclaimed_by": self.owner,
        }
        stale_id = sha256_bytes(canonical_bytes(stale_body))
        stale_path = self.state_dir / "stale_locks" / f"{stale_id}.json"
        immutable_write(stale_path, canonical_bytes(stale_body))
        # Atomic move, not unlink: only the rename winner may publish; a racing
        # same-host starter gets FileNotFoundError and treats the lock as contended.
        moved = stale_path.parent / f"{stale_id}.{uuid.uuid4().hex}.lock"
        try:
            os.rename(self.path, moved)
        except FileNotFoundError as error:
            raise ServiceError("acceptance-service writer lock is already held") from error
        except OSError as error:
            raise ServiceError(f"could not reclaim diagnosed stale lock: {error}") from error
        try:
            moved_owner = read_json(moved)
        except ServiceError:
            moved_owner = None
        if moved_owner != prior:
            # We moved a lock someone else published after our read; put it back.
            try:
                os.link(moved, self.path)
                moved.unlink()
            except OSError:
                pass
            raise ServiceError("acceptance-service writer lock is already held")
        self._publish()
        return self

    def set_child(self, child_pid: int | None) -> None:
        if not self.acquired:
            raise ServiceError("cannot update an unacquired writer lock")
        self.owner["child_pid"] = child_pid
        self.owner["updated_at"] = utc_now()
        atomic_write(self.path, canonical_bytes(self.owner))

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            current = read_json(self.path)
            if current.get("owner_token") != self.owner.get("owner_token"):
                raise ServiceError("writer-lock ownership changed unexpectedly")
            self.path.unlink()
        finally:
            self.acquired = False

    def __enter__(self) -> "ServiceLock":
        return self.acquire()

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.release()


def emit_event(state_dir: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    event = {
        "schema": "legislation-acceptance-service-event-v1",
        "event_id": str(uuid.uuid4()),
        "timestamp_utc": utc_now(),
        **dict(payload),
    }
    core_hash = sha256_bytes(canonical_bytes(event))
    event["event_sha256"] = core_hash
    name = (
        f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-"
        f"{event['event_id']}.json"
    )
    immutable_write(state_dir / "events" / name, canonical_bytes(event))
    return event


def write_progress(
    state_dir: Path,
    state: Mapping[str, Any],
    *,
    status: str,
    stage: str,
    current: int,
    total: int,
    detail: str,
    failure_count: int = 0,
) -> None:
    progress = {
        "schema_version": 1,
        "updated_at": utc_now(),
        "lane": "local",
        "source": "legislation-acceptance-service",
        "run_id": state["service_id"],
        "kind": "collector.progress",
        "status": status,
        "stage": stage,
        "current": current,
        "total": total,
        "summary": f"{current}/{total} standalone acceptance stages",
        "detail": detail,
        "workers": 1,
        "active_workers": 1 if status == "running" else 0,
        "collected_records": current,
        "resumed_records": int(state.get("skipped_stage_count") or 0),
        "preserved_prior_records": 0,
        "failure_count": failure_count,
        "records_per_minute": None,
        "rate": None,
        "eta_seconds": None,
        "command": subprocess.list2cmdline(sys.argv),
        "output_directory": str(state_dir.resolve()),
        **FORBIDDEN_POLICY,
    }
    atomic_write(state_dir / "progress.json", canonical_bytes(progress))


def discover_billstatus_input(override: Path | None) -> Path:
    if override is not None:
        path = override.resolve()
        if not path.exists():
            raise ServiceError(f"explicit GovInfo input does not exist: {path}")
        return path
    root = AUDIT_ROOT / "billstatus_bulk_enrichment" / "fetch_runs"
    candidates: list[tuple[float, Path]] = []
    for manifest_path in root.glob("*/run_manifest.json"):
        try:
            manifest = read_json(manifest_path)
        except ServiceError:
            continue
        if manifest.get("status") != "COMPLETE":
            continue
        parsed = manifest_path.parent / "parsed"
        if parsed.is_dir():
            candidates.append((manifest_path.stat().st_mtime, parsed))
    if not candidates:
        raise ServiceError(
            "no COMPLETE BillStatus parsed input was discovered; pass --govinfo-input"
        )
    return max(candidates, key=lambda item: item[0])[1].resolve()


def require_script(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ServiceError(
            f"{label} is unavailable: {resolved}. "
            "Install or restore the standalone script before resuming."
        )
    return resolved


class StageSpec:
    def __init__(
        self,
        name: str,
        command: Sequence[str],
        input_paths: Sequence[Path],
        output_paths: Sequence[Path],
        validator: Callable[[], None],
    ):
        self.name = name
        self.command = [str(value) for value in command]
        self.input_paths = [Path(value).resolve() for value in input_paths]
        self.output_paths = [Path(value).resolve() for value in output_paths]
        self.validator = validator


def validate_control_plane(path: Path) -> None:
    row = read_json(path)
    gates = row.get("gates") or {}
    safety = row.get("safety") or {}
    if gates.get("baseline_snapshot_verified") is not True:
        raise ServiceError("control plane did not verify the preserved baseline")
    forbidden_true = [
        key
        for key in (
            "candidate_deletion_performed",
            "scoring_performed",
            "production_write_performed",
            "main_corpus_write_performed",
            "sync_or_promotion_performed",
            "mainframe_write_performed",
        )
        if safety.get(key) is True
    ]
    if forbidden_true:
        raise ServiceError(f"control-plane safety violation: {forbidden_true}")


def validate_collector_completion(
    path: Path, *, expected_mode: str, require_full_success: bool
) -> None:
    row = read_json(path)
    if row.get("schema") != "govinfo-bill-version-collector-v1":
        raise ServiceError("GovInfo collector completion schema mismatch")
    if row.get("mode") != expected_mode:
        raise ServiceError("GovInfo collector mode mismatch")
    if row.get("retry_waits") != 0 or row.get("attempts_per_job") != 1:
        raise ServiceError("GovInfo collector violated one-shot/no-wait policy")
    if row.get("failure_count") != 0:
        raise ServiceError("GovInfo collector retained failures")
    if expected_mode == "plan" and row.get("status") != "planned":
        raise ServiceError("GovInfo plan stage did not finish as planned")
    if require_full_success and row.get("status") != "accepted":
        raise ServiceError("GovInfo collection stage did not accept every selected job")


def archive_manifest_from_pointer(pointer_path: Path) -> dict[str, Any]:
    pointer = read_json(pointer_path)
    manifest_item = pointer.get("manifest") or {}
    manifest_path = Path(str(manifest_item.get("path") or ""))
    if not manifest_path.is_absolute():
        manifest_path = pointer_path.parent / manifest_path
    if not manifest_path.is_file():
        raise ServiceError("archive-rehash pointer does not reference a manifest")
    if sha256_file(manifest_path) != manifest_item.get("sha256"):
        raise ServiceError("archive-rehash pointer hash is stale")
    return read_json(manifest_path)


def validate_archive_rehash(pointer_path: Path) -> None:
    manifest = archive_manifest_from_pointer(pointer_path)
    if manifest.get("status") == "FAIL_CLOSED":
        raise ServiceError("archive-rehash audit failed closed")
    required = {
        "accepted_archive_body_rehash_performed": True,
        "recovery_is_provisional_until_archive_rehash": False,
        "every_dispatched_key_terminally_accounted": True,
        "invalid_accepted_receipt_count": 0,
        "unaccounted_dispatch_count": 0,
        "malformed_batch_receipt_count": 0,
    }
    failures = {
        key: (manifest.get(key), expected)
        for key, expected in required.items()
        if manifest.get(key) != expected
    }
    if manifest.get("unknown_failure_keys") not in ([], None):
        failures["unknown_failure_keys"] = (
            manifest.get("unknown_failure_keys"),
            [],
        )
    if manifest.get("unknown_batch_keys") not in ([], None):
        failures["unknown_batch_keys"] = (manifest.get("unknown_batch_keys"), [])
    if failures:
        raise ServiceError(
            "archive-rehash audit is not acceptance-current: "
            + json.dumps(failures, sort_keys=True)
        )


def stage_receipt_valid(receipt_path: Path, spec: StageSpec) -> bool:
    if not receipt_path.is_file():
        return False
    try:
        row = read_json(receipt_path)
        supplied = str(row.get("receipt_sha256") or "")
        core = dict(row)
        core.pop("receipt_sha256", None)
        if supplied != sha256_bytes(canonical_bytes(core)):
            return False
        if (
            row.get("schema") != STAGE_RECEIPT_SCHEMA
            or row.get("status") != "complete"
            or row.get("exit_code") != 0
            or row.get("command") != spec.command
            or row.get("inputs") != receipts(spec.input_paths)
            or row.get("outputs") != receipts(spec.output_paths)
        ):
            return False
        spec.validator()
        return True
    except (OSError, ServiceError, ValueError):
        return False


def execute_stage(
    state_dir: Path,
    state: dict[str, Any],
    spec: StageSpec,
    lock: ServiceLock,
) -> str:
    stage_state = state["stages"].setdefault(
        spec.name, {"status": "pending", "attempts": []}
    )
    latest = stage_state.get("receipt")
    if latest and stage_receipt_valid(Path(str(latest)), spec):
        stage_state["status"] = "complete"
        state["skipped_stage_count"] = int(state.get("skipped_stage_count") or 0) + 1
        emit_event(
            state_dir,
            {
                "kind": "acceptance.stage.skipped",
                "status": "completed",
                "stage": spec.name,
                "detail": "Hash-current completed stage reused.",
                **FORBIDDEN_POLICY,
            },
        )
        return "skipped"

    attempt_number = len(stage_state["attempts"]) + 1
    attempt_id = f"{attempt_number:04d}-{uuid.uuid4()}"
    attempt_dir = state_dir / "stage_attempts" / spec.name / attempt_id
    stdout_path = attempt_dir / "stdout.txt"
    stderr_path = attempt_dir / "stderr.txt"
    receipt_path = attempt_dir / "receipt.json"
    input_items = receipts(spec.input_paths)
    attempt = {
        "attempt_id": attempt_id,
        "started_at": utc_now(),
        "command": spec.command,
        "command_line": subprocess.list2cmdline(spec.command),
        "cwd": str(ROOT),
        "inputs": input_items,
        "expected_outputs": [safe_relative(path) for path in spec.output_paths],
        "retry_waits": 0,
    }
    stage_state["attempts"].append(attempt)
    stage_state["status"] = "running"
    state["current_stage"] = spec.name
    atomic_write(state_dir / "state.json", canonical_bytes(state))
    emit_event(
        state_dir,
        {
            "kind": "acceptance.stage.started",
            "status": "running",
            "stage": spec.name,
            "command": spec.command,
            "inputs": input_items,
            **FORBIDDEN_POLICY,
        },
    )
    attempt_dir.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        child = subprocess.Popen(
            spec.command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
        )
        lock.set_child(child.pid)
        exit_code = child.wait()
        lock.set_child(None)
        stdout.flush()
        stderr.flush()
        os.fsync(stdout.fileno())
        os.fsync(stderr.fileno())
    completed_at = utc_now()
    output_items = receipts(spec.output_paths)
    validation_error: str | None = None
    if exit_code == 0:
        try:
            spec.validator()
        except (OSError, ServiceError, ValueError) as error:
            validation_error = str(error)
    status = "complete" if exit_code == 0 and validation_error is None else "failed"
    receipt_core = {
        "schema": STAGE_RECEIPT_SCHEMA,
        "service_id": state["service_id"],
        "stage": spec.name,
        "attempt_id": attempt_id,
        "status": status,
        "started_at": started,
        "completed_at": completed_at,
        "cwd": str(ROOT),
        "command": spec.command,
        "command_line": subprocess.list2cmdline(spec.command),
        "inputs": input_items,
        "outputs": output_items,
        "stdout": path_receipt(stdout_path),
        "stderr": path_receipt(stderr_path),
        "exit_code": exit_code,
        "validation_error": validation_error,
        "retry_waits": 0,
        **FORBIDDEN_POLICY,
    }
    stage_receipt = {
        **receipt_core,
        "receipt_sha256": sha256_bytes(canonical_bytes(receipt_core)),
    }
    immutable_write(receipt_path, canonical_bytes(stage_receipt))
    attempt.update(
        {
            "completed_at": completed_at,
            "status": status,
            "exit_code": exit_code,
            "receipt": str(receipt_path.resolve()),
        }
    )
    stage_state["status"] = status
    stage_state["receipt"] = str(receipt_path.resolve())
    atomic_write(state_dir / "state.json", canonical_bytes(state))
    emit_event(
        state_dir,
        {
            "kind": f"acceptance.stage.{status}",
            "status": "completed" if status == "complete" else "failed",
            "stage": spec.name,
            "receipt": path_receipt(receipt_path),
            "exit_code": exit_code,
            "detail": validation_error,
            **FORBIDDEN_POLICY,
        },
    )
    if status != "complete":
        raise ServiceError(
            f"stage {spec.name} failed closed: exit={exit_code}; "
            f"validation={validation_error!r}"
        )
    return "completed"


def stage_specs(
    *,
    mode: str,
    attempt_tag: str,
    python: Path,
    control_script: Path,
    collector_script: Path,
    archive_audit_script: Path,
    govinfo_input: Path,
    govinfo_out: Path,
    html_run: Path,
    discovery: Path,
    control_output: Path,
    archive_audit_out: Path,
    canary_size: int,
) -> list[StageSpec]:
    service_script = Path(__file__).resolve()
    control = StageSpec(
        "baseline_control_plane_refresh",
        [str(python), str(control_script), "--out", str(control_output)],
        [service_script, control_script, *DEFAULT_CONTROL_INPUTS],
        [control_output],
        lambda: validate_control_plane(control_output),
    )
    plan_run_id = f"acceptance-{attempt_tag}-plan"
    plan_completed = govinfo_out / "runs" / plan_run_id / "completed.json"
    plan = StageSpec(
        "govinfo_plan",
        [
            str(python),
            str(collector_script),
            "--input",
            str(govinfo_input),
            "--out",
            str(govinfo_out),
            "--run-id",
            plan_run_id,
            "--plan-only",
        ],
        [service_script, collector_script, govinfo_input],
        [plan_completed],
        lambda: validate_collector_completion(
            plan_completed, expected_mode="plan", require_full_success=False
        ),
    )
    collector_contract = StageSpec(
        "govinfo_collector_cli_contract",
        [str(python), str(collector_script), "--help"],
        [service_script, collector_script],
        [],
        lambda: None,
    )
    specs = [control, collector_contract, plan]
    if mode in {"canary", "full"}:
        collect_run_id = f"acceptance-{attempt_tag}-{mode}"
        collect_completed = (
            govinfo_out / "runs" / collect_run_id / "completed.json"
        )
        mode_args = ["--canary", str(canary_size)] if mode == "canary" else ["--full"]
        collect = StageSpec(
            f"govinfo_{mode}_collection",
            [
                str(python),
                str(collector_script),
                "--input",
                str(govinfo_input),
                "--out",
                str(govinfo_out),
                "--run-id",
                collect_run_id,
                *mode_args,
            ],
            [service_script, collector_script, govinfo_input, plan_completed],
            [collect_completed],
            lambda: validate_collector_completion(
                collect_completed,
                expected_mode=mode,
                require_full_success=True,
            ),
        )
        specs.append(collect)
    archive_pointer = archive_audit_out / "current.json"
    archive_inputs = [service_script, archive_audit_script, discovery]
    if html_run.is_dir():
        excluded_output = archive_audit_out.resolve()
        for child in sorted(html_run.iterdir(), key=lambda path: path.name.casefold()):
            if child.resolve() == excluded_output:
                continue
            archive_inputs.append(child)
    else:
        archive_inputs.append(html_run)
    archive = StageSpec(
        "full_text_archive_rehash_audit",
        [
            str(python),
            str(archive_audit_script),
            "--run",
            str(html_run),
            "--discovery",
            str(discovery),
            "--out",
            str(archive_audit_out),
        ],
        archive_inputs,
        [archive_pointer],
        lambda: validate_archive_rehash(archive_pointer),
    )
    specs.append(archive)
    return specs


def initial_state(
    mode: str, state_dir: Path, config: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "service_id": f"legislation-acceptance-{uuid.uuid4()}",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "status": "initialized",
        "mode": mode,
        "state_directory": str(state_dir.resolve()),
        "config": dict(config),
        "stages": {},
        "current_stage": None,
        "completed_stage_count": 0,
        "skipped_stage_count": 0,
        "terminal_boundary": "evidence_ready_culling_preparation",
        **FORBIDDEN_POLICY,
    }


def load_or_create_state(
    state_dir: Path, mode: str, config: Mapping[str, Any]
) -> dict[str, Any]:
    path = state_dir / "state.json"
    if not path.is_file():
        state = initial_state(mode, state_dir, config)
        atomic_write(path, canonical_bytes(state))
        return state
    state = read_json(path)
    if state.get("schema") != SCHEMA:
        raise ServiceError("acceptance-service state schema mismatch")
    if state.get("mode") != mode:
        raise ServiceError(
            f"existing service mode is {state.get('mode')!r}; "
            f"refusing requested {mode!r}"
        )
    if state.get("config") != dict(config):
        raise ServiceError(
            "acceptance-service configuration changed; use a new --state-dir"
        )
    if any(state.get(key) is not expected for key, expected in FORBIDDEN_POLICY.items()):
        raise ServiceError("acceptance-service safety policy drift")
    return state


def mode_from_args(args: argparse.Namespace) -> str:
    if args.plan_only:
        return "plan"
    if args.canary_only:
        return "canary"
    if args.full:
        return "full"
    raise ServiceError("select exactly one of --plan-only, --canary-only, or --full")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan-only", action="store_true")
    modes.add_argument("--canary-only", action="store_true")
    modes.add_argument("--full", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--canary-size", type=int, default=12)
    parser.add_argument("--govinfo-input", type=Path)
    parser.add_argument("--govinfo-out", type=Path, default=DEFAULT_GOVINFO_OUT)
    parser.add_argument("--html-run", type=Path, default=DEFAULT_HTML_RUN)
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--control-output", type=Path, default=DEFAULT_CONTROL_OUTPUT)
    parser.add_argument(
        "--archive-audit-out", type=Path, default=DEFAULT_ARCHIVE_AUDIT_OUT
    )
    parser.add_argument("--control-script", type=Path, default=DEFAULT_CONTROL_SCRIPT)
    parser.add_argument(
        "--collector-script", type=Path, default=DEFAULT_COLLECTOR_SCRIPT
    )
    parser.add_argument(
        "--archive-audit-script", type=Path, default=DEFAULT_ARCHIVE_AUDIT_SCRIPT
    )
    return parser.parse_args(argv)


def run_service(args: argparse.Namespace) -> dict[str, Any]:
    mode = mode_from_args(args)
    if args.canary_size < 1:
        raise ServiceError("--canary-size must be positive")
    state_dir = args.state_dir.resolve()
    control_script = require_script(args.control_script, "control-plane builder")
    collector_script = require_script(args.collector_script, "GovInfo collector")
    archive_script = require_script(
        args.archive_audit_script, "archive-rehash audit"
    )
    govinfo_input = discover_billstatus_input(args.govinfo_input)
    config = {
        "python": str(Path(sys.executable).resolve()),
        "control_script": str(control_script),
        "collector_script": str(collector_script),
        "archive_audit_script": str(archive_script),
        "govinfo_input": str(govinfo_input),
        "govinfo_out": str(args.govinfo_out.resolve()),
        "html_run": str(args.html_run.resolve()),
        "discovery": str(args.discovery.resolve()),
        "control_output": str(args.control_output.resolve()),
        "archive_audit_out": str(args.archive_audit_out.resolve()),
        "canary_size": args.canary_size,
    }
    state = load_or_create_state(state_dir, mode, config)
    owner = {
        "schema": "legislation-acceptance-service-lock-v1",
        "owner_token": str(uuid.uuid4()),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "child_pid": None,
        "service_id": state["service_id"],
        "started_at": utc_now(),
        "command": [sys.executable, *sys.argv],
    }
    with ServiceLock(state_dir, owner) as lock:
        # Stable across restarts: completed immutable collector runs can be
        # recognized by their hash-current receipts and skipped. Failed or stale
        # immutable runs are preserved and require a new --service-id.
        attempt_tag = state["service_id"].rsplit("-", 1)[-1][:12]
        specs = stage_specs(
            mode=mode,
            attempt_tag=attempt_tag,
            python=Path(sys.executable).resolve(),
            control_script=control_script,
            collector_script=collector_script,
            archive_audit_script=archive_script,
            govinfo_input=govinfo_input,
            govinfo_out=args.govinfo_out.resolve(),
            html_run=args.html_run.resolve(),
            discovery=args.discovery.resolve(),
            control_output=args.control_output.resolve(),
            archive_audit_out=args.archive_audit_out.resolve(),
            canary_size=args.canary_size,
        )
        total = len(specs)
        state["status"] = "running"
        state["updated_at"] = utc_now()
        atomic_write(state_dir / "state.json", canonical_bytes(state))
        write_progress(
            state_dir,
            state,
            status="running",
            stage="initializing",
            current=0,
            total=total,
            detail="Standalone acceptance service acquired the single-writer lock.",
        )
        emit_event(
            state_dir,
            {
                "kind": "acceptance.service.started",
                "status": "running",
                "stage": "initializing",
                "mode": mode,
                "stage_order": [spec.name for spec in specs],
                **FORBIDDEN_POLICY,
            },
        )
        try:
            completed = 0
            for spec in specs:
                write_progress(
                    state_dir,
                    state,
                    status="running",
                    stage=spec.name,
                    current=completed,
                    total=total,
                    detail=f"Executing one-shot stage {spec.name}.",
                )
                execute_stage(state_dir, state, spec, lock)
                completed += 1
                state["completed_stage_count"] = completed
                state["updated_at"] = utc_now()
                atomic_write(state_dir / "state.json", canonical_bytes(state))
            terminal = (
                "PLANNED_ONLY_OFFLINE"
                if mode == "plan"
                else "CANARY_EVIDENCE_READY_NOT_FULL"
                if mode == "canary"
                else "EVIDENCE_READY_CULLING_PREPARATION"
            )
            state["status"] = terminal
            state["current_stage"] = "evidence-ready-culling-preparation"
            state["updated_at"] = utc_now()
            atomic_write(state_dir / "state.json", canonical_bytes(state))
            write_progress(
                state_dir,
                state,
                status="complete",
                stage="evidence-ready-culling-preparation",
                current=total,
                total=total,
                detail=(
                    f"{terminal}; stopped before culling decisions, scoring, sync, "
                    "promotion, publication, or mainframe writes."
                ),
            )
            emit_event(
                state_dir,
                {
                    "kind": "acceptance.service.completed",
                    "status": "completed",
                    "stage": "evidence-ready-culling-preparation",
                    "terminal_status": terminal,
                    **FORBIDDEN_POLICY,
                },
            )
            return state
        except Exception as error:
            state["status"] = "FAIL_CLOSED"
            state["failure"] = {
                "at": utc_now(),
                "stage": state.get("current_stage"),
                "type": type(error).__name__,
                "message": str(error),
            }
            state["updated_at"] = utc_now()
            atomic_write(state_dir / "state.json", canonical_bytes(state))
            write_progress(
                state_dir,
                state,
                status="failed",
                stage=str(state.get("current_stage") or "unknown"),
                current=int(state.get("completed_stage_count") or 0),
                total=total,
                detail=str(error),
                failure_count=1,
            )
            emit_event(
                state_dir,
                {
                    "kind": "acceptance.service.failed",
                    "status": "failed",
                    "stage": state.get("current_stage"),
                    "error_type": type(error).__name__,
                    "detail": str(error),
                    **FORBIDDEN_POLICY,
                },
            )
            raise


def main(argv: Sequence[str] | None = None) -> int:
    try:
        state = run_service(parse_args(argv))
    except (OSError, ServiceError, ValueError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        return 2
    print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

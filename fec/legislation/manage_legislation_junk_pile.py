#!/usr/bin/env python3
"""Manage a reversible, append-only legislation junk pile.

This utility is audit-only.  It never edits the discovery corpus, scoring
inputs, workbooks, mainframe data, or promotion artifacts.  Exclusions and
restorations are separate append-only ledgers.  Effective state is materialized
only after the full integrity contract passes.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
AUDIT_ROOT = (ROOT / "audits" / "legfix5").resolve()
DEFAULT_ROOT = AUDIT_ROOT / "legislation_junk_pile"
TOOL_NAME = "manage_legislation_junk_pile.py"
TOOL_VERSION = "1.0.0"
MANIFEST_SCHEMA = "acu-legislation-junk-pile-manifest-v1"
EXCLUSION_SCHEMA = "acu-legislation-junk-pile-exclusion-v1"
RESTORATION_SCHEMA = "acu-legislation-junk-pile-restoration-v1"
RECEIPT_SCHEMA = "acu-legislation-junk-pile-audit-receipt-v1"
ZERO_SHA256 = "0" * 64
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DECISIONS_NAME = "exclusion_decisions.ndjson"
RESTORATIONS_NAME = "restoration_decisions.ndjson"


class JunkPileError(ValueError):
    """The junk-pile contract failed closed."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_value_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def validate_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise JunkPileError(f"{field} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise JunkPileError(f"{field} is not a valid timestamp") from exc
    return value


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JunkPileError(f"{field} must be a non-empty string")
    return value.strip()


def require_nonempty_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise JunkPileError(f"{field} must be a non-empty JSON array")
    for index, item in enumerate(value):
        if item is None or item == "" or item == {} or item == []:
            raise JunkPileError(f"{field}[{index}] must contain substantive evidence")
    return value


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_root(path: Path) -> Path:
    resolved = path.resolve()
    allowed = [AUDIT_ROOT, Path(tempfile.gettempdir()).resolve()]
    if not any(_is_relative_to(resolved, base) for base in allowed):
        raise JunkPileError(
            "junk-pile root must resolve under audits/legfix5 or the system "
            f"temporary directory; received {resolved}"
        )
    return resolved


def _write_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise JunkPileError(f"refusing to overwrite existing evidence: {path}") from exc


def _write_new_json(path: Path, value: Any) -> None:
    _write_new(path, canonical_json_bytes(value) + b"\n")


@contextmanager
def exclusive_lock(root: Path) -> Iterable[None]:
    lock_path = root / ".junk-pile.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise JunkPileError(
            f"junk-pile lock already exists; inspect before removing: {lock_path}"
        ) from exc
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def initialize(root: Path, *, created_at: str | None = None) -> Path:
    root = validate_root(root)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        _load_manifest(root)
        return root
    with exclusive_lock(root):
        if manifest_path.exists():
            _load_manifest(root)
            return root
        timestamp = validate_timestamp(created_at or utc_now(), "created_at")
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "status": "EMPTY_READY_NO_CULLING_PERFORMED",
            "created_at": timestamp,
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
            "ledgers": {
                "exclusions": DECISIONS_NAME,
                "restorations": RESTORATIONS_NAME,
            },
            "contract": {
                "append_only": True,
                "restoration_is_separate_ledger": True,
                "discovery_corpus_is_immutable": True,
                "scoring_sync_promotion_authorized": False,
                "mainframe_write_authorized": False,
            },
        }
        _write_new_json(manifest_path, manifest)
        _write_new(root / DECISIONS_NAME, b"")
        _write_new(root / RESTORATIONS_NAME, b"")
    return root


def _load_manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise JunkPileError(f"missing manifest: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JunkPileError(f"invalid manifest: {manifest_path}") from exc
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise JunkPileError("junk-pile manifest schema mismatch")
    contract = manifest.get("contract")
    expected = {
        "append_only": True,
        "restoration_is_separate_ledger": True,
        "discovery_corpus_is_immutable": True,
        "scoring_sync_promotion_authorized": False,
        "mainframe_write_authorized": False,
    }
    if contract != expected:
        raise JunkPileError("junk-pile safety contract drift")
    ledgers = manifest.get("ledgers")
    if ledgers != {
        "exclusions": DECISIONS_NAME,
        "restorations": RESTORATIONS_NAME,
    }:
        raise JunkPileError("junk-pile ledger-name contract drift")
    return manifest


def _record_content_hash(row: dict[str, Any]) -> str:
    core = dict(row)
    core.pop("record_content_sha256", None)
    return canonical_value_sha256(core)


def _seal_record(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["record_content_sha256"] = _record_content_hash(result)
    return result


def _load_ledger(path: Path, expected_schema: str) -> tuple[list[dict[str, Any]], list[bytes]]:
    if not path.is_file():
        raise JunkPileError(f"missing ledger: {path}")
    records: list[dict[str, Any]] = []
    raw_lines: list[bytes] = []
    previous = ZERO_SHA256
    with path.open("rb") as handle:
        for line_number, raw_with_newline in enumerate(handle, 1):
            if not raw_with_newline.endswith(b"\n"):
                raise JunkPileError(f"{path.name}:{line_number}: unterminated record")
            raw = raw_with_newline[:-1]
            if raw.endswith(b"\r"):
                raise JunkPileError(f"{path.name}:{line_number}: noncanonical CRLF")
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise JunkPileError(
                    f"{path.name}:{line_number}: invalid JSON"
                ) from exc
            if not isinstance(row, dict):
                raise JunkPileError(f"{path.name}:{line_number}: row is not an object")
            if canonical_json_bytes(row) != raw:
                raise JunkPileError(
                    f"{path.name}:{line_number}: record is not canonical JSON"
                )
            if row.get("schema") != expected_schema:
                raise JunkPileError(f"{path.name}:{line_number}: schema mismatch")
            observed_content_hash = _record_content_hash(row)
            if row.get("record_content_sha256") != observed_content_hash:
                raise JunkPileError(
                    f"{path.name}:{line_number}: record content hash drift"
                )
            if row.get("previous_record_sha256") != previous:
                raise JunkPileError(
                    f"{path.name}:{line_number}: append-only hash chain is broken"
                )
            previous = sha256_bytes(raw)
            records.append(row)
            raw_lines.append(raw_with_newline)
    return records, raw_lines


def _ledger_checkpoint(path: Path, raw_lines: list[bytes]) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "record_count": len(raw_lines),
        "tail_record_sha256": (
            sha256_bytes(raw_lines[-1][:-1]) if raw_lines else ZERO_SHA256
        ),
    }


def _prefix_sha256(raw_lines: list[bytes], count: int) -> str:
    digest = hashlib.sha256()
    for raw in raw_lines[:count]:
        digest.update(raw)
    return digest.hexdigest()


def _verify_historical_checkpoints(
    root: Path,
    exclusion_lines: list[bytes],
    restoration_lines: list[bytes],
) -> None:
    snapshot_root = root / "snapshots"
    if not snapshot_root.exists():
        return
    current = {
        "exclusions": exclusion_lines,
        "restorations": restoration_lines,
    }
    for receipt_path in sorted(snapshot_root.glob("*/audit_receipt.json")):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JunkPileError(f"invalid historical receipt: {receipt_path}") from exc
        if receipt.get("schema") != RECEIPT_SCHEMA:
            raise JunkPileError(f"historical receipt schema drift: {receipt_path}")
        supplied_hash = receipt.get("receipt_content_sha256")
        core = dict(receipt)
        core.pop("receipt_content_sha256", None)
        if supplied_hash != canonical_value_sha256(core):
            raise JunkPileError(f"historical receipt hash drift: {receipt_path}")
        for name, lines in current.items():
            checkpoint = receipt.get("ledgers", {}).get(name, {})
            count = checkpoint.get("record_count")
            if not isinstance(count, int) or count < 0 or count > len(lines):
                raise JunkPileError(
                    f"append-only truncation detected against {receipt_path}"
                )
            if checkpoint.get("sha256") != _prefix_sha256(lines, count):
                raise JunkPileError(
                    f"historical ledger prefix drift against {receipt_path}"
                )
            expected_tail = (
                sha256_bytes(lines[count - 1][:-1]) if count else ZERO_SHA256
            )
            if checkpoint.get("tail_record_sha256") != expected_tail:
                raise JunkPileError(
                    f"historical ledger tail drift against {receipt_path}"
                )


def _validate_candidate_evidence(value: Any, candidate_id: str) -> None:
    if not isinstance(value, dict):
        raise JunkPileError(f"{candidate_id}: candidate_evidence must be an object")
    mode = value.get("retention_mode")
    if mode == "full_payload":
        if "payload" not in value:
            raise JunkPileError(f"{candidate_id}: full payload is missing")
        expected = canonical_value_sha256(value["payload"])
        if value.get("content_sha256") != expected:
            raise JunkPileError(f"{candidate_id}: embedded candidate payload hash drift")
    elif mode == "immutable_pointer":
        pointer = value.get("source_pointer")
        if not isinstance(pointer, dict):
            raise JunkPileError(f"{candidate_id}: source pointer is missing")
        path_text = require_text(pointer.get("path"), "source_pointer.path")
        source_path = Path(path_text)
        expected_hash = pointer.get("sha256")
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise JunkPileError(f"{candidate_id}: invalid source pointer SHA-256")
        if not isinstance(pointer.get("bytes"), int) or pointer["bytes"] < 0:
            raise JunkPileError(f"{candidate_id}: invalid source pointer byte count")
        locator = pointer.get("locator")
        if not isinstance(locator, dict) or not locator:
            raise JunkPileError(f"{candidate_id}: source pointer locator is missing")
        if not source_path.is_file():
            raise JunkPileError(f"{candidate_id}: immutable source is unavailable")
        if sha256_file(source_path) != expected_hash:
            raise JunkPileError(f"{candidate_id}: immutable source hash drift")
        if source_path.stat().st_size != pointer["bytes"]:
            raise JunkPileError(f"{candidate_id}: immutable source byte-count drift")
        if value.get("content_sha256") != expected_hash:
            raise JunkPileError(f"{candidate_id}: source/content hash mismatch")
    else:
        raise JunkPileError(
            f"{candidate_id}: retention_mode must be full_payload or immutable_pointer"
        )


def _validate_common_decision(row: dict[str, Any], *, restoration: bool) -> None:
    candidate_id = require_text(row.get("candidate_id"), "candidate_id")
    identifier_field = "restoration_id" if restoration else "decision_id"
    time_field = "restored_at" if restoration else "decided_at"
    actor_field = "restored_by" if restoration else "decided_by"
    require_text(row.get(identifier_field), identifier_field)
    validate_timestamp(row.get(time_field), time_field)
    require_text(row.get(actor_field), actor_field)
    require_text(row.get("reason"), "reason")
    require_nonempty_list(row.get("evidence"), "evidence")
    require_nonempty_list(row.get("provenance"), "provenance")
    tool = row.get("tool")
    if tool != {"name": TOOL_NAME, "version": TOOL_VERSION}:
        raise JunkPileError(f"{candidate_id}: tool identity/version drift")
    if restoration:
        require_text(row.get("target_decision_id"), "target_decision_id")
        target_hash = row.get("target_decision_record_sha256")
        if not isinstance(target_hash, str) or not SHA256_RE.fullmatch(target_hash):
            raise JunkPileError(f"{candidate_id}: invalid target decision hash")
    else:
        rule = row.get("rule")
        if not isinstance(rule, dict):
            raise JunkPileError(f"{candidate_id}: rule must be an object")
        require_text(rule.get("id"), "rule.id")
        require_text(rule.get("version"), "rule.version")
        _validate_candidate_evidence(row.get("candidate_evidence"), candidate_id)


def compute_effective_state(root: Path) -> dict[str, Any]:
    root = validate_root(root)
    manifest = _load_manifest(root)
    exclusions, exclusion_lines = _load_ledger(
        root / DECISIONS_NAME, EXCLUSION_SCHEMA
    )
    restorations, restoration_lines = _load_ledger(
        root / RESTORATIONS_NAME, RESTORATION_SCHEMA
    )
    _verify_historical_checkpoints(root, exclusion_lines, restoration_lines)

    exclusion_by_candidate: dict[str, dict[str, Any]] = {}
    exclusion_by_id: dict[str, dict[str, Any]] = {}
    exclusion_line_hash_by_id: dict[str, str] = {}
    for index, row in enumerate(exclusions):
        _validate_common_decision(row, restoration=False)
        candidate_id = row["candidate_id"]
        decision_id = row["decision_id"]
        if candidate_id in exclusion_by_candidate:
            raise JunkPileError(
                f"duplicate/conflicting exclusion decisions for {candidate_id}"
            )
        if decision_id in exclusion_by_id:
            raise JunkPileError(f"duplicate exclusion decision_id: {decision_id}")
        exclusion_by_candidate[candidate_id] = row
        exclusion_by_id[decision_id] = row
        exclusion_line_hash_by_id[decision_id] = sha256_bytes(
            exclusion_lines[index][:-1]
        )

    restoration_by_target: dict[str, dict[str, Any]] = {}
    restoration_ids: set[str] = set()
    for row in restorations:
        _validate_common_decision(row, restoration=True)
        restoration_id = row["restoration_id"]
        target_id = row["target_decision_id"]
        if restoration_id in restoration_ids:
            raise JunkPileError(f"duplicate restoration_id: {restoration_id}")
        restoration_ids.add(restoration_id)
        target = exclusion_by_id.get(target_id)
        if target is None:
            raise JunkPileError(
                f"restoration {restoration_id} targets unknown decision {target_id}"
            )
        if target_id in restoration_by_target:
            raise JunkPileError(
                f"duplicate/conflicting restorations for decision {target_id}"
            )
        if row["candidate_id"] != target["candidate_id"]:
            raise JunkPileError(
                f"restoration {restoration_id} candidate/target conflict"
            )
        if (
            row["target_decision_record_sha256"]
            != exclusion_line_hash_by_id[target_id]
        ):
            raise JunkPileError(
                f"restoration {restoration_id} target decision hash drift"
            )
        restoration_by_target[target_id] = row

    current: list[dict[str, Any]] = []
    restored: list[dict[str, Any]] = []
    for candidate_id in sorted(exclusion_by_candidate):
        exclusion = exclusion_by_candidate[candidate_id]
        restoration = restoration_by_target.get(exclusion["decision_id"])
        item = {
            "candidate_id": candidate_id,
            "status": "restored" if restoration else "junk",
            "candidate_evidence": exclusion["candidate_evidence"],
            "exclusion_decision": exclusion,
            "restoration_decision": restoration,
        }
        (restored if restoration else current).append(item)

    checkpoints = {
        "exclusions": _ledger_checkpoint(root / DECISIONS_NAME, exclusion_lines),
        "restorations": _ledger_checkpoint(
            root / RESTORATIONS_NAME, restoration_lines
        ),
    }
    state_basis = {
        "manifest_sha256": sha256_file(root / "manifest.json"),
        "exclusions_sha256": checkpoints["exclusions"]["sha256"],
        "restorations_sha256": checkpoints["restorations"]["sha256"],
        "current_candidate_ids": [item["candidate_id"] for item in current],
        "restored_candidate_ids": [item["candidate_id"] for item in restored],
    }
    return {
        "root": root,
        "manifest": manifest,
        "exclusions": exclusions,
        "restorations": restorations,
        "current": current,
        "restored": restored,
        "ledgers": checkpoints,
        "state_sha256": canonical_value_sha256(state_basis),
    }


def _append_record(path: Path, row: dict[str, Any]) -> None:
    body = canonical_json_bytes(row) + b"\n"
    with path.open("ab") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _source_receipt(path: Path, locator: dict[str, Any]) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise JunkPileError(f"candidate source does not exist: {path}")
    if not isinstance(locator, dict) or not locator:
        raise JunkPileError("source locator must be a non-empty object")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "locator": locator,
    }


def append_exclusion(
    root: Path,
    *,
    candidate_id: str,
    rule_id: str,
    rule_version: str,
    reason: str,
    evidence: list[Any],
    provenance: list[Any],
    decided_by: str,
    candidate_payload: Any | None = None,
    source_path: Path | None = None,
    source_locator: dict[str, Any] | None = None,
    pointer_only: bool = False,
    decided_at: str | None = None,
    decision_id: str | None = None,
) -> dict[str, Any]:
    root = initialize(root)
    candidate_id = require_text(candidate_id, "candidate_id")
    if pointer_only:
        if candidate_payload is not None:
            raise JunkPileError("pointer-only exclusion cannot also supply payload")
        if source_path is None or source_locator is None:
            raise JunkPileError("pointer-only exclusion requires source path and locator")
        pointer = _source_receipt(source_path, source_locator)
        candidate_evidence = {
            "retention_mode": "immutable_pointer",
            "content_sha256": pointer["sha256"],
            "source_pointer": pointer,
        }
    else:
        if candidate_payload is None:
            raise JunkPileError("full candidate payload is required")
        candidate_evidence = {
            "retention_mode": "full_payload",
            "content_sha256": canonical_value_sha256(candidate_payload),
            "payload": candidate_payload,
        }
        if source_path is not None:
            candidate_evidence["captured_from"] = _source_receipt(
                source_path, source_locator or {"kind": "json_document"}
            )

    with exclusive_lock(root):
        state = compute_effective_state(root)
        if any(
            row["candidate_id"] == candidate_id for row in state["exclusions"]
        ):
            raise JunkPileError(
                f"candidate already has an exclusion decision: {candidate_id}"
            )
        previous = state["ledgers"]["exclusions"]["tail_record_sha256"]
        row = _seal_record(
            {
                "schema": EXCLUSION_SCHEMA,
                "decision_id": require_text(
                    decision_id or f"exclude-{uuid.uuid4()}", "decision_id"
                ),
                "decision_type": "exclude_as_false_positive",
                "candidate_id": candidate_id,
                "candidate_evidence": candidate_evidence,
                "rule": {
                    "id": require_text(rule_id, "rule_id"),
                    "version": require_text(rule_version, "rule_version"),
                },
                "reason": require_text(reason, "reason"),
                "evidence": require_nonempty_list(evidence, "evidence"),
                "provenance": require_nonempty_list(provenance, "provenance"),
                "decided_at": validate_timestamp(
                    decided_at or utc_now(), "decided_at"
                ),
                "decided_by": require_text(decided_by, "decided_by"),
                "candidate_retained_in_discovery_corpus": True,
                "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
                "previous_record_sha256": previous,
            }
        )
        _validate_common_decision(row, restoration=False)
        _append_record(root / DECISIONS_NAME, row)
        compute_effective_state(root)
    return row


def append_restoration(
    root: Path,
    *,
    candidate_id: str,
    target_decision_id: str,
    reason: str,
    evidence: list[Any],
    provenance: list[Any],
    restored_by: str,
    restored_at: str | None = None,
    restoration_id: str | None = None,
) -> dict[str, Any]:
    root = initialize(root)
    with exclusive_lock(root):
        state = compute_effective_state(root)
        target = next(
            (
                row
                for row in state["exclusions"]
                if row["decision_id"] == target_decision_id
            ),
            None,
        )
        if target is None:
            raise JunkPileError(f"unknown exclusion decision: {target_decision_id}")
        candidate_id = require_text(candidate_id, "candidate_id")
        if target["candidate_id"] != candidate_id:
            raise JunkPileError("restoration candidate does not match target decision")
        if any(
            row["target_decision_id"] == target_decision_id
            for row in state["restorations"]
        ):
            raise JunkPileError(
                f"target decision is already restored: {target_decision_id}"
            )
        target_raw_hash = sha256_bytes(canonical_json_bytes(target))
        previous = state["ledgers"]["restorations"]["tail_record_sha256"]
        row = _seal_record(
            {
                "schema": RESTORATION_SCHEMA,
                "restoration_id": require_text(
                    restoration_id or f"restore-{uuid.uuid4()}",
                    "restoration_id",
                ),
                "decision_type": "restore_to_candidate_pool",
                "candidate_id": candidate_id,
                "target_decision_id": target_decision_id,
                "target_decision_record_sha256": target_raw_hash,
                "reason": require_text(reason, "reason"),
                "evidence": require_nonempty_list(evidence, "evidence"),
                "provenance": require_nonempty_list(provenance, "provenance"),
                "restored_at": validate_timestamp(
                    restored_at or utc_now(), "restored_at"
                ),
                "restored_by": require_text(restored_by, "restored_by"),
                "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
                "previous_record_sha256": previous,
            }
        )
        _validate_common_decision(row, restoration=True)
        _append_record(root / RESTORATIONS_NAME, row)
        compute_effective_state(root)
    return row


def _ndjson_body(rows: Iterable[Any]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _csv_body(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "candidate_id",
            "status",
            "exclusion_decision_id",
            "rule_id",
            "rule_version",
            "reason",
            "decided_at",
            "restoration_id",
            "restored_at",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for item in rows:
        exclusion = item["exclusion_decision"]
        restoration = item["restoration_decision"]
        writer.writerow(
            {
                "candidate_id": item["candidate_id"],
                "status": item["status"],
                "exclusion_decision_id": exclusion["decision_id"],
                "rule_id": exclusion["rule"]["id"],
                "rule_version": exclusion["rule"]["version"],
                "reason": exclusion["reason"],
                "decided_at": exclusion["decided_at"],
                "restoration_id": (
                    restoration["restoration_id"] if restoration else ""
                ),
                "restored_at": restoration["restored_at"] if restoration else "",
            }
        )
    return stream.getvalue().encode("utf-8")


def _markdown_body(state: dict[str, Any]) -> bytes:
    lines = [
        "# Legislation junk pile — current view",
        "",
        f"- State SHA-256: `{state['state_sha256']}`",
        f"- Currently in junk pile: **{len(state['current'])}**",
        f"- Restored to candidate pool: **{len(state['restored'])}**",
        "- Discovery corpus modified: **No**",
        "- Scoring, sync, promotion, or mainframe write authorized: **No**",
        "",
        "## Current junk pile",
        "",
    ]
    if not state["current"]:
        lines.append("_Empty._")
    else:
        lines.extend(
            [
                "| Candidate | Decision | Rule | Reason |",
                "|---|---|---|---|",
            ]
        )
        for item in state["current"]:
            row = item["exclusion_decision"]
            reason = row["reason"].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{item['candidate_id']}` | `{row['decision_id']}` | "
                f"`{row['rule']['id']}@{row['rule']['version']}` | {reason} |"
            )
    lines.extend(["", "## Restored candidates", ""])
    if not state["restored"]:
        lines.append("_None._")
    else:
        lines.extend(
            [
                "| Candidate | Exclusion | Restoration | Restoration reason |",
                "|---|---|---|---|",
            ]
        )
        for item in state["restored"]:
            exclusion = item["exclusion_decision"]
            restoration = item["restoration_decision"]
            reason = restoration["reason"].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{item['candidate_id']}` | `{exclusion['decision_id']}` | "
                f"`{restoration['restoration_id']}` | {reason} |"
            )
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def export_current(root: Path, *, audited_at: str | None = None) -> Path:
    root = validate_root(root)
    state = compute_effective_state(root)
    snapshot = root / "snapshots" / state["state_sha256"]
    receipt_path = snapshot / "audit_receipt.json"
    if receipt_path.exists():
        compute_effective_state(root)
        return receipt_path

    timestamp = validate_timestamp(audited_at or utc_now(), "audited_at")
    artifacts = {
        "current_junk_pile.ndjson": _ndjson_body(state["current"]),
        "restored_candidates.ndjson": _ndjson_body(state["restored"]),
        "current_view.csv": _csv_body(state["current"] + state["restored"]),
        "CURRENT_VIEW.md": _markdown_body(state),
    }
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, body in artifacts.items():
        _write_new(snapshot / name, body)

    receipt_core = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "audited_at": timestamp,
        "state_sha256": state["state_sha256"],
        "counts": {
            "exclusion_decisions": len(state["exclusions"]),
            "restoration_decisions": len(state["restorations"]),
            "current_junk_pile": len(state["current"]),
            "restored_to_candidate_pool": len(state["restored"]),
        },
        "ledgers": {
            name: {
                "path": checkpoint["path"],
                "bytes": checkpoint["bytes"],
                "sha256": checkpoint["sha256"],
                "record_count": checkpoint["record_count"],
                "tail_record_sha256": checkpoint["tail_record_sha256"],
            }
            for name, checkpoint in state["ledgers"].items()
        },
        "artifacts": {
            name: {
                "path": str((snapshot / name).resolve()),
                "bytes": len(body),
                "sha256": sha256_bytes(body),
            }
            for name, body in artifacts.items()
        },
        "integrity": {
            "canonical_records": True,
            "self_hashes_valid": True,
            "append_only_hash_chains_valid": True,
            "historical_checkpoints_valid": True,
            "duplicate_or_conflicting_decisions": 0,
            "source_pointer_hash_drift": 0,
        },
        "policy": {
            "candidate_payload_or_immutable_pointer_preserved": True,
            "restoration_ledger_separate": True,
            "discovery_corpus_modified": False,
            "scoring_sync_promotion_authorized": False,
            "mainframe_write_authorized": False,
        },
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
    }
    receipt = dict(receipt_core)
    receipt["receipt_content_sha256"] = canonical_value_sha256(receipt_core)
    _write_new_json(receipt_path, receipt)
    compute_effective_state(root)
    return receipt_path


def parse_json_argument(value: str, field: str) -> Any:
    try:
        if value.startswith("@"):
            return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
        return json.loads(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise JunkPileError(f"{field} is not valid JSON or @file JSON") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("audit")

    exclude = commands.add_parser("exclude")
    exclude.add_argument("--candidate-id", required=True)
    exclude.add_argument("--candidate-json", type=Path, required=True)
    exclude.add_argument("--rule-id", required=True)
    exclude.add_argument("--rule-version", required=True)
    exclude.add_argument("--reason", required=True)
    exclude.add_argument("--evidence-json", required=True)
    exclude.add_argument("--provenance-json", required=True)
    exclude.add_argument("--decided-by", required=True)
    exclude.add_argument("--decision-id")

    restore = commands.add_parser("restore")
    restore.add_argument("--candidate-id", required=True)
    restore.add_argument("--target-decision-id", required=True)
    restore.add_argument("--reason", required=True)
    restore.add_argument("--evidence-json", required=True)
    restore.add_argument("--provenance-json", required=True)
    restore.add_argument("--restored-by", required=True)
    restore.add_argument("--restoration-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = initialize(args.root)
        if args.command == "exclude":
            payload = json.loads(args.candidate_json.read_text(encoding="utf-8"))
            append_exclusion(
                root,
                candidate_id=args.candidate_id,
                candidate_payload=payload,
                source_path=args.candidate_json,
                source_locator={"kind": "json_document"},
                rule_id=args.rule_id,
                rule_version=args.rule_version,
                reason=args.reason,
                evidence=parse_json_argument(args.evidence_json, "evidence_json"),
                provenance=parse_json_argument(
                    args.provenance_json, "provenance_json"
                ),
                decided_by=args.decided_by,
                decision_id=args.decision_id,
            )
        elif args.command == "restore":
            append_restoration(
                root,
                candidate_id=args.candidate_id,
                target_decision_id=args.target_decision_id,
                reason=args.reason,
                evidence=parse_json_argument(args.evidence_json, "evidence_json"),
                provenance=parse_json_argument(
                    args.provenance_json, "provenance_json"
                ),
                restored_by=args.restored_by,
                restoration_id=args.restoration_id,
            )
        receipt = export_current(root)
        print(receipt)
        return 0
    except (JunkPileError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL CLOSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

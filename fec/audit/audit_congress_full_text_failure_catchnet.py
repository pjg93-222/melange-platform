#!/usr/bin/env python3
"""Reconcile all Congress full-text failures into a lossless catchnet.

The audit is a one-shot, read-only observer of collector artifacts.  It binds
historical failed events, quarantined response receipts, new failure-attempt
receipts, and accepted receipts into an immutable snapshot.  It never retries,
collects, deletes, scores, promotes, or writes production data.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable
import re


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = (
    ROOT
    / "audits"
    / "legfix5"
    / "congress_advanced_pages_20260725"
    / "text_relevance_httr_full_stage_20260726"
)
DEFAULT_DISCOVERY = DEFAULT_RUN.parent / "congress_advanced_records.ndjson"
REQUIRED_VALIDATION = (
    "status_200",
    "exact_url",
    "html_content_type",
    "not_challenge",
    "expected_main_marker",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def source_receipt(path: Path, *, base: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        display = str(resolved.relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        display = str(resolved)
    return {
        "path": display,
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def atomic_write(path: Path, body: bytes, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        if path.read_bytes() != body:
            raise ValueError(f"immutable catchnet collision: {path}")
        return
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def json_body(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")


def ndjson_body(rows: Iterable[dict[str, Any]]) -> bytes:
    text = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    )
    return (text + ("\n" if text else "")).encode("utf-8")


def discovery_catalog(path: Path) -> dict[str, str]:
    catalog: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            key = str(row.get("canonical_key") or "")
            canonical_url = str(row.get("canonical_url") or "").rstrip("/")
            if not key or key in catalog or not canonical_url:
                raise ValueError(
                    f"invalid or duplicate discovery identity at line {line_number}: {key!r}"
                )
            catalog[key] = canonical_url + "/text"
    return catalog


def gzip_body_receipt(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    byte_count = 0
    with gzip.open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            byte_count += len(block)
    return {"sha256": digest.hexdigest(), "bytes": byte_count}


def accepted_receipts(
    run: Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    accepted: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, Any]] = []
    files = sorted((run / "receipts").glob("**/receipt.json"))
    for path in files:
        try:
            row = read_object(path)
            validation = row.get("validation") or {}
            artifact_path = Path(str(row.get("html_file") or ""))
            archive = gzip_body_receipt(artifact_path) if artifact_path.is_file() else None
            valid = bool(
                row.get("status") == "accepted"
                and row.get("http_status") == 200
                and row.get("expected_url") == row.get("final_url")
                and all(validation.get(name) is True for name in REQUIRED_VALIDATION)
                and row.get("cookie_values_persisted") is False
                and SHA256_RE.fullmatch(str(row.get("body_sha256") or ""))
                and archive is not None
                and archive["sha256"] == row.get("body_sha256")
                and archive["bytes"] == row.get("byte_length")
            )
            key = str(row.get("canonical_key") or "")
            if not valid or not key or key in accepted:
                invalid.append(
                    {
                        "path": str(path),
                        "canonical_key": key or None,
                        "reason": "invalid_or_duplicate_accepted_receipt",
                    }
                )
                continue
            accepted[key] = {
                "receipt": source_receipt(path, base=run),
                "body_sha256": row.get("body_sha256"),
                "expected_url": row.get("expected_url"),
                "invocation_id": row.get("invocation_id"),
                "batch_id": row.get("batch_id"),
                "request_id": row.get("request_id"),
                "attempt_ordinal": row.get("attempt_ordinal"),
                "archive": archive,
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            invalid.append({"path": str(path), "reason": str(exc)})
    return accepted, invalid


def failure_source_paths(run: Path) -> list[tuple[str, Path]]:
    rows: list[tuple[str, Path]] = []
    rows.extend(
        ("failure_attempt", path)
        for path in sorted((run / "failure_attempts").glob("**/*.json"))
    )
    rows.extend(
        ("historical_quarantine_receipt", path)
        for path in sorted((run / "quarantine").glob("**/failure-*.json"))
    )
    for path in sorted((run / "events").glob("*.json")):
        try:
            row = read_object(path)
        except (OSError, ValueError, json.JSONDecodeError):
            rows.append(("malformed_event", path))
            continue
        if row.get("kind") == "collector.record.failed":
            rows.append(("failed_event", path))
    return rows


def normalize_failure(
    source_kind: str,
    path: Path,
    *,
    run: Path,
    accepted: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    receipt = source_receipt(path, base=run)
    try:
        row = read_object(path)
        key = str(row.get("canonical_key") or "")
        if not key and isinstance(row.get("input"), dict):
            key = str(row["input"].get("canonical_key") or "")
        expected_url = row.get("expected_url") or row.get("command")
        invocation_id = row.get("invocation_id")
        batch_id = row.get("batch_id")
        request_id = row.get("request_id")
        attempt_ordinal = row.get("attempt_ordinal")
        failure_class = (
            row.get("failure_class")
            or row.get("reason")
            or row.get("detail")
            or "surface_rejected"
        )
        malformed = False
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        key = ""
        expected_url = None
        invocation_id = None
        batch_id = None
        request_id = None
        attempt_ordinal = None
        failure_class = f"malformed_failure_evidence:{exc}"
        malformed = True
    recovery = accepted.get(key)
    identity = {
        "source_kind": source_kind,
        "source_sha256": receipt["sha256"],
        "source_path": receipt["path"],
    }
    return {
        "schema": "congress-full-text-failure-catchnet-row-v1",
        "attempt_id": sha256_bytes(canonical_bytes(identity)),
        "canonical_key": key or None,
        "failure_class": failure_class,
        "expected_url": expected_url,
        "invocation_id": invocation_id,
        "batch_id": batch_id,
        "request_id": request_id,
        "attempt_ordinal": attempt_ordinal,
        "source_kind": source_kind,
        "source": receipt,
        "malformed": malformed,
        "recovery_status": (
            "receipt_recovered_archive_rehashed"
            if recovery
            else "unresolved"
        ),
        "accepted_receipt": recovery,
        "candidate_deleted": False,
    }


def batch_accounting(
    run: Path,
    *,
    discovery: dict[str, str],
    accepted: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    unaccounted: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    accepted_bindings = {
        (value.get("batch_id"), key): value
        for key, value in accepted.items()
        if value.get("batch_id")
    }
    failure_bindings = {
        (row.get("batch_id"), row.get("canonical_key")): row
        for row in failures
        if row.get("batch_id") and row.get("canonical_key")
    }

    def artifact_path(value: Any) -> Path:
        path = Path(str(value or ""))
        return path if path.is_absolute() else run / path

    def terminal_valid(
        outcome: dict[str, Any],
        *,
        key: str,
        expected_url: str,
        batch_id: str,
        invocation_id: str,
        strict_v2: bool,
    ) -> bool:
        status = outcome.get("status")
        if status not in {"accepted", "resumed", "failed"}:
            return False
        if outcome.get("retry_waits") != 0:
            return False
        if strict_v2 and outcome.get("expected_url") != expected_url:
            return False
        if status == "failed":
            receipt_path = artifact_path(
                outcome.get("terminal_receipt") or outcome.get("terminal_artifact")
            )
            if not receipt_path.is_file():
                return False
            try:
                receipt = read_object(receipt_path)
            except (OSError, ValueError, json.JSONDecodeError):
                return False
            valid = bool(
                receipt.get("canonical_key") == key
                and receipt.get("expected_url") == expected_url
                and str(receipt.get("schema") or "").startswith("congress-httr-failure")
                and receipt.get("cookie_values_persisted") is False
            )
            if strict_v2:
                valid = bool(
                    valid
                    and receipt.get("batch_id") == batch_id
                    and receipt.get("invocation_id") == invocation_id
                    and receipt.get("request_id") == outcome.get("request_id")
                    and receipt.get("attempt_ordinal") == outcome.get("attempt_ordinal")
                )
            return valid
        value = accepted.get(key)
        if not value or value.get("expected_url") != expected_url:
            return False
        if strict_v2:
            receipt_path = artifact_path(outcome.get("terminal_receipt"))
            if not receipt_path.is_file():
                return False
            try:
                receipt = read_object(receipt_path)
            except (OSError, ValueError, json.JSONDecodeError):
                return False
            return bool(
                receipt.get("canonical_key") == key
                and receipt.get("expected_url") == expected_url
                and receipt.get("batch_id") == batch_id
                and receipt.get("invocation_id") == invocation_id
                and receipt.get("request_id") == outcome.get("request_id")
                and receipt.get("attempt_ordinal") == outcome.get("attempt_ordinal")
                and value.get("batch_id") == batch_id
                and value.get("invocation_id") == invocation_id
            )
        return True

    dispatch_paths = sorted((run / "batches").glob("*/dispatch.json"))
    dispatch_dirs = {path.parent.resolve() for path in dispatch_paths}
    for completion_path in sorted((run / "batches").glob("*/completion.json")):
        if completion_path.parent.resolve() not in dispatch_dirs:
            malformed.append(
                {
                    "completion_path": str(completion_path),
                    "reason": "orphan_completion_without_dispatch",
                }
            )

    for dispatch_path in dispatch_paths:
        completion_path = dispatch_path.with_name("completion.json")
        try:
            dispatch = read_object(dispatch_path)
            dispatch_source = source_receipt(dispatch_path, base=run)
            batch_id = str(dispatch.get("batch_id") or "")
            invocation_id = str(dispatch.get("invocation_id") or "")
            batch_keys = [str(value) for value in dispatch.get("canonical_keys") or []]
            expected_urls = [str(value) for value in dispatch.get("expected_urls") or []]
            dispatch_schema = str(dispatch.get("schema") or "")
            strict_v2 = dispatch_schema == "congress-httr-batch-dispatch-v2"
            identity_pairs_valid = bool(
                len(batch_keys) == len(expected_urls)
                and all(discovery.get(key) == url for key, url in zip(batch_keys, expected_urls))
            )
            dispatch_valid = bool(
                dispatch_schema in {
                    "congress-httr-batch-dispatch-v1",
                    "congress-httr-batch-dispatch-v2",
                }
                and batch_id == dispatch_path.parent.name
                and invocation_id
                and batch_keys
                and len(batch_keys) == len(set(batch_keys))
                and identity_pairs_valid
                and dispatch.get("worker_count") == len(batch_keys)
                and dispatch.get("retry_waits") == 0
                and dispatch.get("candidate_deletion_allowed") is False
            )
            completion = read_object(completion_path) if completion_path.is_file() else None
            completion_valid = False
            terminal_receipts_valid = False
            if completion is not None:
                outcomes = completion.get("outcomes") or []
                outcome_by_key = {
                    str(outcome.get("canonical_key") or ""): outcome
                    for outcome in outcomes
                    if isinstance(outcome, dict)
                }
                keys_exact = bool(
                    len(outcome_by_key) == len(outcomes) == len(batch_keys)
                    and set(outcome_by_key) == set(batch_keys)
                )
                terminal_receipts_valid = bool(
                    keys_exact
                    and all(
                        terminal_valid(
                            outcome_by_key[key],
                            key=key,
                            expected_url=url,
                            batch_id=batch_id,
                            invocation_id=invocation_id,
                            strict_v2=strict_v2,
                        )
                        for key, url in zip(batch_keys, expected_urls)
                    )
                )
                completion_schema_valid = (
                    completion.get("schema") == (
                        "congress-httr-batch-completion-v2"
                        if strict_v2
                        else "congress-httr-batch-completion-v1"
                    )
                )
                v2_bindings_valid = bool(
                    not strict_v2
                    or (
                        completion.get("dispatch_receipt_sha256") == dispatch_source["sha256"]
                        and completion.get("dispatched_expected_urls") == expected_urls
                    )
                )
                completion_valid = bool(
                    completion_schema_valid
                    and completion.get("batch_id") == batch_id
                    and completion.get("invocation_id") == invocation_id
                    and completion.get("dispatched_canonical_keys") == batch_keys
                    and completion.get("outcome_count") == len(outcomes)
                    and completion.get("every_dispatched_key_has_terminal_outcome") is True
                    and keys_exact
                    and terminal_receipts_valid
                    and v2_bindings_valid
                    and completion.get("retry_waits") == 0
                    and completion.get("candidate_deletion_allowed") is False
                )
            if not dispatch_valid or (completion is not None and not completion_valid):
                malformed.append(
                    {
                        "batch_id": batch_id or None,
                        "dispatch_path": str(dispatch_path),
                        "completion_path": str(completion_path) if completion_path.exists() else None,
                        "reason": "invalid_dispatch_or_completion_receipt",
                    }
                )
            key_statuses = []
            for key in batch_keys:
                bound_accepted = accepted_bindings.get((batch_id, key))
                bound_failure = failure_bindings.get((batch_id, key))
                status = (
                    "terminal_batch_completion"
                    if completion_valid
                    else "accepted_receipt_after_incomplete_dispatch"
                    if bound_accepted and bound_accepted.get("invocation_id") == invocation_id
                    else "failure_receipt_after_incomplete_dispatch"
                    if bound_failure and bound_failure.get("invocation_id") == invocation_id
                    else "unaccounted_dispatch"
                )
                key_row = {"canonical_key": key, "accounting_status": status}
                key_statuses.append(key_row)
                if status == "unaccounted_dispatch":
                    unaccounted.append(
                        {
                            "batch_id": batch_id or None,
                            "invocation_id": invocation_id or None,
                            "canonical_key": key,
                            "dispatch": dispatch_source,
                        }
                    )
            rows.append(
                {
                    "schema": "congress-httr-batch-accounting-row-v2",
                    "batch_id": batch_id or None,
                    "invocation_id": invocation_id or None,
                    "dispatch": dispatch_source,
                    "completion": source_receipt(completion_path, base=run)
                    if completion_path.is_file()
                    else None,
                    "dispatch_valid": dispatch_valid,
                    "completion_valid": completion_valid,
                    "terminal_receipts_valid": terminal_receipts_valid,
                    "dispatched_key_count": len(batch_keys),
                    "unknown_discovery_keys": sorted(set(batch_keys) - set(discovery)),
                    "key_accounting": key_statuses,
                }
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            malformed.append(
                {
                    "dispatch_path": str(dispatch_path),
                    "completion_path": str(completion_path) if completion_path.exists() else None,
                    "reason": str(exc),
                }
            )
    return rows, unaccounted, malformed


def main() -> int:
    args = parse_args()
    run = args.run.resolve()
    discovery = args.discovery.resolve()
    out_root = (
        args.out.resolve() if args.out else run / "failure_catchnet"
    )
    catalog = discovery_catalog(discovery)
    keys = set(catalog)
    accepted, invalid_accepted = accepted_receipts(run)
    failures = [
        normalize_failure(kind, path, run=run, accepted=accepted)
        for kind, path in failure_source_paths(run)
    ]
    failures.sort(
        key=lambda row: (
            str(row.get("canonical_key") or ""),
            row["source_kind"],
            row["attempt_id"],
        )
    )
    unresolved = [row for row in failures if row["recovery_status"] == "unresolved"]
    recovered = [
        row
        for row in failures
        if row["recovery_status"] == "receipt_recovered_archive_rehashed"
    ]
    unknown_failure_keys = sorted(
        {
            str(row.get("canonical_key") or "")
            for row in failures
            if row.get("canonical_key") and row.get("canonical_key") not in keys
        }
    )
    malformed_count = sum(bool(row.get("malformed")) for row in failures)
    batches, unaccounted_dispatches, malformed_batches = batch_accounting(
        run,
        discovery=catalog,
        accepted=accepted,
        failures=failures,
    )
    unknown_batch_keys = sorted(
        {
            key
            for batch in batches
            for key in batch.get("unknown_discovery_keys") or []
        }
    )
    identity = {
        "producer_sha256": sha256_file(Path(__file__).resolve()),
        "discovery": source_receipt(discovery, base=run),
        "accepted": sorted(
            (key, value["receipt"]["sha256"]) for key, value in accepted.items()
        ),
        "failures": [row["attempt_id"] for row in failures],
        "batches": [
            (
                row.get("batch_id"),
                row["dispatch"]["sha256"],
                (row.get("completion") or {}).get("sha256"),
            )
            for row in batches
        ],
        "unaccounted_dispatches": sorted(
            (row.get("batch_id"), row.get("canonical_key"))
            for row in unaccounted_dispatches
        ),
        "malformed_batches": malformed_batches,
    }
    snapshot_id = sha256_bytes(canonical_bytes(identity))
    snapshot = out_root / "snapshots" / snapshot_id
    snapshot.mkdir(parents=True, exist_ok=True)

    outputs = {
        "failure_attempts.ndjson": ndjson_body(failures),
        "recovered_failures.ndjson": ndjson_body(recovered),
        "unresolved_failures.ndjson": ndjson_body(unresolved),
        "invalid_accepted_receipts.ndjson": ndjson_body(invalid_accepted),
        "batch_accounting.ndjson": ndjson_body(batches),
        "unaccounted_dispatches.ndjson": ndjson_body(unaccounted_dispatches),
        "malformed_batch_receipts.ndjson": ndjson_body(malformed_batches),
    }
    output_receipts: dict[str, dict[str, Any]] = {}
    for name, body in outputs.items():
        path = snapshot / name
        atomic_write(path, body, overwrite=False)
        output_receipts[name] = {
            "path": name,
            "bytes": len(body),
            "sha256": sha256_bytes(body),
        }

    status = (
        "PASS_RECEIPT_LEVEL_WITH_UNRESOLVED_FAILURES"
        if unresolved and not invalid_accepted and not unknown_failure_keys and not malformed_count and not unaccounted_dispatches and not malformed_batches and not unknown_batch_keys
        else "PASS_RECEIPT_LEVEL_NO_UNRESOLVED_FAILURES"
        if not unresolved and not invalid_accepted and not unknown_failure_keys and not malformed_count and not unaccounted_dispatches and not malformed_batches and not unknown_batch_keys
        else "FAIL_CLOSED"
    )
    manifest = {
        "schema": "congress-full-text-failure-catchnet-manifest-v1",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": snapshot_id,
        "producer_sha256": sha256_file(Path(__file__).resolve()),
        "discovery_candidate_count": len(keys),
        "accepted_candidate_count": len(set(accepted) & keys),
        "failure_evidence_row_count": len(failures),
        "failure_candidate_count": len(
            {row.get("canonical_key") for row in failures if row.get("canonical_key")}
        ),
        "recovered_failure_row_count": len(recovered),
        "accepted_archive_body_rehash_performed": True,
        "recovery_is_provisional_until_archive_rehash": False,
        "unresolved_failure_row_count": len(unresolved),
        "unresolved_failure_candidate_count": len(
            {row.get("canonical_key") for row in unresolved if row.get("canonical_key")}
        ),
        "invalid_accepted_receipt_count": len(invalid_accepted),
        "unknown_failure_keys": unknown_failure_keys,
        "malformed_failure_evidence_count": malformed_count,
        "batch_dispatch_count": len(batches),
        "batch_completion_count": sum(bool(row.get("completion_valid")) for row in batches),
        "unaccounted_dispatch_count": len(unaccounted_dispatches),
        "unaccounted_dispatch_candidate_count": len(
            {row.get("canonical_key") for row in unaccounted_dispatches}
        ),
        "malformed_batch_receipt_count": len(malformed_batches),
        "unknown_batch_keys": unknown_batch_keys,
        "every_dispatched_key_terminally_accounted": not (
            unaccounted_dispatches or malformed_batches or unknown_batch_keys
        ),
        "terminal_receipt_verified_batch_count": sum(
            bool(row.get("terminal_receipts_valid")) for row in batches
        ),
        "outputs": output_receipts,
        "safety": {
            "collector_modified": False,
            "network_executed": False,
            "candidate_deleted": False,
            "failure_silently_skipped": False,
            "scoring_performed": False,
            "production_write_performed": False,
            "sync_or_promotion_performed": False,
        },
    }
    manifest_body = json_body(manifest)
    atomic_write(snapshot / "manifest.json", manifest_body, overwrite=False)
    pointer = {
        "schema": "congress-full-text-failure-catchnet-pointer-v1",
        "snapshot_id": snapshot_id,
        "manifest": {
            "path": str((snapshot / "manifest.json").resolve()),
            "bytes": len(manifest_body),
            "sha256": sha256_bytes(manifest_body),
        },
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write(out_root / "current.json", json_body(pointer), overwrite=True)
    print(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if status != "FAIL_CLOSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

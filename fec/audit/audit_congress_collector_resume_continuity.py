#!/usr/bin/env python3
"""Fail-closed, read-only audit of Congress collector resume continuity.

The audit distinguishes collector-run/timeout metadata rematerialization from
semantic query-record drift.  It also proves that the current query/page
artifacts still validate and that the candidate-review ledger and partitions
remain byte-bound to a pre-existing partition audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FEC_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(FEC_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(FEC_BOOTSTRAP))

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes


SCHEMA = "acu-congress-collector-resume-continuity-audit-v1"
QUERY_SEMANTIC_FIELDS = (
    "query_id",
    "result_total",
    "unique_record_count",
    "page_count",
    "counts_by_congress",
    "counts_by_type",
    "records_sha256",
    "validation",
)
COLLECTION_BINDING_FIELDS = (
    "schema",
    "primary_instrument",
    "quorum_used",
    "registry_file",
    "registry_sha256",
    "collection_contract",
    "page_contract",
    "selected_query_ids",
)
# Producer-defined runtime fields; unknown changes are not presumed harmless metadata.
RUN_METADATA_FIELDS = (
    "status", "started_at", "completed_at", "failed_at", "error",
    "automatic_verification_timeout",
)
HONORED_PRIOR_STATE_FIELDS = (
    "observed_at", "consecutive_timeouts", "next_retry_not_before",
)
REQUIRED_VALIDATION_FIELDS = (
    "exact_result_total",
    "unique_result_total",
    "every_page_exact",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def javascript_json_sha256(value: Any) -> str:
    """Match stableHash(JSON.stringify(value)) in the Node collector."""
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def count_nonempty_lines(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for line in handle:
            if line.rstrip(b"\r\n"):
                count += 1
    return count


def _safe_relative_file(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("artifact path must be a non-empty string")
    root = root.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact escapes run directory: {relative}") from exc
    if not resolved.is_file():
        raise ValueError(f"artifact is missing: {relative}")
    return resolved


def _query_projection(query: dict[str, Any]) -> dict[str, Any]:
    return {field: query.get(field) for field in QUERY_SEMANTIC_FIELDS}


def _query_index(
    manifest: dict[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    queries = manifest.get("queries")
    if not isinstance(queries, list):
        return [], {}, ["queries_is_not_a_list"]
    ids: list[str] = []
    index: dict[str, dict[str, Any]] = {}
    for position, query in enumerate(queries):
        if not isinstance(query, dict):
            errors.append(f"query_{position}_is_not_an_object")
            continue
        query_id = query.get("query_id")
        if not isinstance(query_id, str) or not query_id:
            errors.append(f"query_{position}_has_invalid_query_id")
            continue
        ids.append(query_id)
        if query_id in index:
            errors.append(f"duplicate_query_id:{query_id}")
        index[query_id] = query
    return ids, index, errors


def _value_differences(
    baseline: Any, current: Any, path: str = ""
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    if type(baseline) is not type(current):
        return [
            {
                "path": path or "$",
                "baseline": baseline,
                "current": current,
            }
        ]
    if isinstance(baseline, dict):
        for key in sorted(set(baseline) | set(current)):
            child = f"{path}.{key}" if path else key
            if key not in baseline:
                differences.append(
                    {"path": child, "baseline": None, "current": current[key]}
                )
            elif key not in current:
                differences.append(
                    {"path": child, "baseline": baseline[key], "current": None}
                )
            else:
                differences.extend(
                    _value_differences(baseline[key], current[key], child)
                )
        return differences
    if isinstance(baseline, list):
        if baseline != current:
            differences.append(
                {"path": path or "$", "baseline": baseline, "current": current}
            )
        return differences
    if baseline != current:
        differences.append(
            {"path": path or "$", "baseline": baseline, "current": current}
        )
    return differences


def _counts(records: Iterable[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(str(record.get(field)) for record in records if isinstance(record, dict))
    return dict(sorted(counts.items()))


def validate_current_query(
    run_dir: Path, manifest_entry: dict[str, Any]
) -> dict[str, Any]:
    query_id = manifest_entry.get("query_id")
    errors: list[str] = []
    try:
        query_path = _safe_relative_file(
            run_dir, manifest_entry.get("query_file")
        )
        query = load_json(query_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "query_id": query_id,
            "query_file": manifest_entry.get("query_file"),
            "status": "FAIL",
            "errors": [str(exc)],
        }

    if not isinstance(query, dict):
        return {
            "query_id": query_id,
            "query_file": manifest_entry.get("query_file"),
            "status": "FAIL",
            "errors": ["query_artifact_is_not_an_object"],
        }
    records = query.get("records")
    pages = query.get("pages")
    if not isinstance(records, list):
        records = []
        errors.append("records_is_not_a_list")
    if not isinstance(pages, list):
        pages = []
        errors.append("pages_is_not_a_list")

    for field in QUERY_SEMANTIC_FIELDS:
        if query.get(field) != manifest_entry.get(field):
            errors.append(f"manifest_query_mismatch:{field}")
    if query.get("schema") != "congress-advanced-search-query-v1":
        errors.append("unexpected_query_schema")

    record_hash = javascript_json_sha256(records)
    canonical_keys = [
        record.get("canonical_key")
        for record in records
        if isinstance(record, dict)
    ]
    valid_canonical_keys = [key for key in canonical_keys if isinstance(key, str) and key]
    unique_key_count = len(set(valid_canonical_keys))
    if len(records) != query.get("result_total"):
        errors.append("record_count_mismatch")
    if unique_key_count != query.get("unique_record_count"):
        errors.append("unique_record_count_mismatch")
    if len(valid_canonical_keys) != len(records):
        errors.append("invalid_canonical_key")
    if record_hash != query.get("records_sha256"):
        errors.append("records_sha256_mismatch")
    if _counts(records, "congress") != query.get("counts_by_congress"):
        errors.append("counts_by_congress_mismatch")
    if _counts(records, "type") != query.get("counts_by_type"):
        errors.append("counts_by_type_mismatch")
    if len(pages) != query.get("page_count"):
        errors.append("page_count_mismatch")

    validation = query.get("validation")
    if not isinstance(validation, dict):
        errors.append("validation_is_not_an_object")
    else:
        for field in REQUIRED_VALIDATION_FIELDS:
            if validation.get(field) is not True:
                errors.append(f"validation_not_true:{field}")
        if any(value is not True for value in validation.values()):
            errors.append("one_or_more_validation_booleans_not_true")

    concatenated_page_records: list[dict[str, Any]] = []
    page_hash_bindings: list[list[str]] = []
    for position, page_ref in enumerate(pages, 1):
        if not isinstance(page_ref, dict):
            errors.append(f"page_ref_{position}_is_not_an_object")
            continue
        try:
            page_path = _safe_relative_file(run_dir, page_ref.get("file"))
            page = load_json(page_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"page_{position}:{exc}")
            continue
        page_hash_bindings.append(
            [str(page_ref.get("file")), sha256_file(page_path)]
        )
        if not isinstance(page, dict):
            errors.append(f"page_{position}:artifact_is_not_an_object")
            continue
        page_records = page.get("records")
        if not isinstance(page_records, list):
            errors.append(f"page_{position}:records_is_not_a_list")
            continue
        concatenated_page_records.extend(page_records)
        page_records_hash = javascript_json_sha256(page_records)
        if page.get("query_id") != query_id:
            errors.append(f"page_{position}:query_id_mismatch")
        if page.get("page_number") != position:
            errors.append(f"page_{position}:page_number_mismatch")
        if page_ref.get("page_number") != position:
            errors.append(f"page_ref_{position}:page_number_mismatch")
        if page_records_hash != page.get("records_sha256"):
            errors.append(f"page_{position}:artifact_records_hash_mismatch")
        if page_records_hash != page_ref.get("records_sha256"):
            errors.append(f"page_{position}:reference_records_hash_mismatch")
        if len(page_records) != page_ref.get("record_count"):
            errors.append(f"page_{position}:record_count_mismatch")
        if page.get("result_range") != page_ref.get("result_range"):
            errors.append(f"page_{position}:result_range_mismatch")
        result_range = page_ref.get("result_range")
        if not isinstance(result_range, dict) or any(
            type(result_range.get(field)) is not int for field in ("start", "end", "total")
        ):
            errors.append(f"page_{position}:invalid_result_range")
        else:
            expected = result_range.get("end", 0) - result_range.get(
                "start", 0
            ) + 1
            if expected != len(page_records):
                errors.append(f"page_{position}:range_size_mismatch")
            if result_range.get("total") != query.get("result_total"):
                errors.append(f"page_{position}:range_total_mismatch")
        page_validation = page.get("validation")
        if not isinstance(page_validation, dict) or any(
            value is not True for value in page_validation.values()
        ):
            errors.append(f"page_{position}:validation_not_all_true")

    if javascript_json_sha256(concatenated_page_records) != record_hash:
        errors.append("page_record_concatenation_differs_from_query_records")

    return {
        "query_id": query_id,
        "query_file": manifest_entry.get("query_file"),
        "query_file_sha256": sha256_file(query_path),
        "completed_at": query.get("completed_at"),
        "result_total": query.get("result_total"),
        "unique_record_count": query.get("unique_record_count"),
        "page_count": query.get("page_count"),
        "records_sha256": query.get("records_sha256"),
        "recomputed_records_sha256": record_hash,
        "page_artifact_file_hash_binding_sha256": canonical_json_sha256(
            page_hash_bindings
        ),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
    }


def validate_partition_binding(partition_audit_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    partition_audit_path = partition_audit_path.resolve()
    sidecar_path = partition_audit_path.with_suffix(
        partition_audit_path.suffix + ".sha256"
    )
    audit = load_json(partition_audit_path)
    audit_file_sha = sha256_file(partition_audit_path)
    try:
        sidecar_parts = sidecar_path.read_text(encoding="ascii").strip().split()
    except OSError as exc:
        sidecar_parts = []
        errors.append(f"partition_audit_sidecar_unreadable:{exc}")
    if len(sidecar_parts) != 2:
        errors.append("partition_audit_sidecar_invalid")
    else:
        if sidecar_parts[0] != audit_file_sha:
            errors.append("partition_audit_sidecar_hash_mismatch")
        if sidecar_parts[1] != partition_audit_path.name:
            errors.append("partition_audit_sidecar_filename_mismatch")

    payload = dict(audit)
    expected_payload_sha = payload.pop("audit_payload_sha256", None)
    if canonical_json_sha256(payload) != expected_payload_sha:
        errors.append("partition_audit_payload_hash_mismatch")
    if audit.get("status") != "PASS" or audit.get("blocking_issue_count") != 0:
        errors.append("partition_audit_is_not_pass")
    exactness = audit.get("partition_exactness", {})
    if exactness.get("ledger_and_partition_key_hashes_equal") is not True:
        errors.append("partition_key_union_is_not_ledger")
    if (
        exactness.get("partition_stable_membership_key_union_sha256")
        != audit.get("source_bindings", {}).get(
            "ledger_stable_membership_key_set_sha256"
        )
    ):
        errors.append("partition_key_hash_binding_mismatch")

    bindings: list[dict[str, Any]] = []
    source_bindings = audit.get("source_bindings", {})
    requested = [
        (
            "review_manifest",
            source_bindings.get("manifest_file"),
            source_bindings.get("manifest_file_sha256"),
            None,
        ),
        (
            "membership_review_ledger",
            source_bindings.get("ledger_file"),
            source_bindings.get("ledger_file_sha256"),
            source_bindings.get("ledger_line_count"),
        ),
    ]
    for name, partition in audit.get("partition_files", {}).items():
        requested.append(
            (
                name,
                partition.get("file"),
                partition.get("file_sha256"),
                partition.get("line_count"),
            )
        )
    for name, raw_path, expected_sha, expected_lines in requested:
        binding_errors: list[str] = []
        path = Path(raw_path) if isinstance(raw_path, str) else Path("")
        if not raw_path or not path.is_file():
            observed_sha = None
            observed_lines = None
            binding_errors.append("missing_file")
        else:
            observed_sha = sha256_file(path)
            observed_lines = (
                count_nonempty_lines(path)
                if expected_lines is not None
                else None
            )
            if observed_sha != expected_sha:
                binding_errors.append("sha256_mismatch")
            if (
                expected_lines is not None
                and observed_lines != expected_lines
            ):
                binding_errors.append("line_count_mismatch")
        errors.extend(f"{name}:{error}" for error in binding_errors)
        bindings.append(
            {
                "name": name,
                "file": raw_path,
                "expected_sha256": expected_sha,
                "observed_sha256": observed_sha,
                "expected_line_count": expected_lines,
                "observed_line_count": observed_lines,
                "unchanged_and_hash_bound": not binding_errors,
            }
        )

    dispositions = audit.get("ledger_disposition_counts", {})
    return {
        "partition_audit_file": str(partition_audit_path),
        "partition_audit_file_sha256": audit_file_sha,
        "partition_audit_payload_sha256": expected_payload_sha,
        "partition_audit_sidecar_valid": not any(
            error.startswith("partition_audit_sidecar")
            for error in errors
        ),
        "ledger_line_count": source_bindings.get("ledger_line_count"),
        "disposition_counts": dispositions,
        "all_review_files_unchanged_and_hash_bound": all(
            item["unchanged_and_hash_bound"] for item in bindings
        ),
        "bindings": bindings,
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
    }


def validate_timeout_receipt(
    manifest_path: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Validate the current immutable automatic-verification receipt."""

    errors: list[str] = []
    timeout = manifest.get("automatic_verification_timeout")
    if not isinstance(timeout, dict):
        return {
            "status": "FAIL",
            "receipt_file": None,
            "declared_sha256": None,
            "observed_sha256": None,
            "immutable_receipt_hash_valid": False,
            "safety_contract_valid": False,
            "errors": ["automatic_verification_timeout_is_missing_or_invalid"],
        }
    raw_receipt = timeout.get("receipt_file")
    receipt_path: Path | None = None
    if not isinstance(raw_receipt, str) or not raw_receipt.strip():
        errors.append("timeout_receipt_file_is_missing")
    else:
        try:
            receipt_path = _safe_relative_file(
                manifest_path.resolve().parent, raw_receipt
            )
        except ValueError as exc:
            errors.append(f"timeout_receipt_invalid:{exc}")
    observed_sha = (
        sha256_file(receipt_path)
        if receipt_path is not None
        else None
    )
    declared_sha = timeout.get("receipt_sha256")
    if observed_sha is not None and declared_sha != observed_sha:
        errors.append("timeout_receipt_sha256_mismatch")
    safety_contract_valid = bool(
        timeout.get("classification")
        == "automatic_verification_timeout"
        and timeout.get("interaction_performed") is False
        and timeout.get("captcha_bypass_attempted") is False
        and timeout.get("collection_stopped_without_result_acceptance") is True
    )
    if not safety_contract_valid:
        errors.append("timeout_safety_contract_drift")
    return {
        "status": "PASS" if not errors else "FAIL",
        "receipt_file": (
            str(receipt_path) if receipt_path is not None else raw_receipt
        ),
        "declared_sha256": declared_sha,
        "observed_sha256": observed_sha,
        "immutable_receipt_hash_valid": bool(
            observed_sha is not None and declared_sha == observed_sha
        ),
        "safety_contract_valid": safety_contract_valid,
        "consecutive_timeouts": timeout.get("consecutive_timeouts"),
        "cooldown_ms": timeout.get("cooldown_ms"),
        "next_retry_not_before": timeout.get("next_retry_not_before"),
        "errors": errors,
    }


def audit_continuity(
    baseline_manifest_path: Path,
    current_manifest_path: Path,
    partition_audit_path: Path,
    *,
    expected_query_count: int = 22,
) -> dict[str, Any]:
    baseline_manifest_path = baseline_manifest_path.resolve()
    current_manifest_path = current_manifest_path.resolve()
    baseline = load_json(baseline_manifest_path)
    current = load_json(current_manifest_path)
    if not isinstance(baseline, dict) or not isinstance(current, dict):
        raise ValueError("baseline and current manifests must be objects")
    baseline_ids, baseline_index, baseline_index_errors = _query_index(
        baseline
    )
    current_ids, current_index, current_index_errors = _query_index(current)
    continuity_errors = baseline_index_errors + current_index_errors
    if len(baseline_ids) != expected_query_count:
        continuity_errors.append("baseline_query_count_mismatch")
    if len(current_ids) != expected_query_count:
        continuity_errors.append("current_query_count_mismatch")
    if baseline_ids != current_ids:
        continuity_errors.append("query_id_order_or_membership_mismatch")

    semantic_differences: list[dict[str, Any]] = []
    query_binding_differences: list[dict[str, Any]] = []
    for query_id in sorted(set(baseline_index) | set(current_index)):
        baseline_query = baseline_index.get(query_id)
        current_query = current_index.get(query_id)
        if baseline_query is None or current_query is None:
            semantic_differences.append(
                {
                    "query_id": query_id,
                    "field": "presence",
                    "baseline": baseline_query is not None,
                    "current": current_query is not None,
                }
            )
            continue
        for field in QUERY_SEMANTIC_FIELDS:
            if baseline_query.get(field) != current_query.get(field):
                semantic_differences.append(
                    {
                        "query_id": query_id,
                        "field": field,
                        "baseline": baseline_query.get(field),
                        "current": current_query.get(field),
                    }
                )
        if baseline_query.get("query_file") != current_query.get("query_file"):
            query_binding_differences.append(
                {
                    "query_id": query_id,
                    "baseline": baseline_query.get("query_file"),
                    "current": current_query.get("query_file"),
                }
            )

    collection_binding_differences: list[dict[str, Any]] = []
    for field in COLLECTION_BINDING_FIELDS:
        if baseline.get(field) != current.get(field):
            collection_binding_differences.append(
                {
                    "field": field,
                    "baseline": baseline.get(field),
                    "current": current.get(field),
                }
            )
    metadata_views: list[dict[str, Any]] = []
    policy_views: list[dict[str, Any]] = []
    unknown_views: list[dict[str, Any]] = []
    excluded = set(COLLECTION_BINDING_FIELDS) | set(RUN_METADATA_FIELDS) | {
        "queries", "automatic_verification_policy"
    }
    for label, manifest in (("baseline", baseline), ("current", current)):
        metadata = {key: manifest[key] for key in RUN_METADATA_FIELDS if key in manifest}
        policy = manifest.get("automatic_verification_policy", {})
        if not isinstance(policy, dict):
            continuity_errors.append(f"{label}_automatic_verification_policy_is_not_an_object")
            policy = {}
        static_policy = dict(policy)
        if "honored_prior_state" in static_policy:
            honored = static_policy.pop("honored_prior_state")
            if not isinstance(honored, dict) or set(honored) - set(HONORED_PRIOR_STATE_FIELDS):
                continuity_errors.append(f"{label}_honored_prior_state_has_unknown_shape_or_fields")
            else:
                metadata["honored_prior_state"] = honored
        metadata_views.append(metadata)
        policy_views.append(static_policy)
        unknown_views.append({key: value for key, value in manifest.items() if key not in excluded})
    for prefix, views in (("automatic_verification_policy", policy_views), ("unknown_manifest_field", unknown_views)):
        for difference in _value_differences(*views):
            collection_binding_differences.append({
                "field": f"{prefix}.{difference['path']}",
                "baseline": difference["baseline"], "current": difference["current"],
            })
    run_metadata_differences = _value_differences(*metadata_views)

    query_results = [
        validate_current_query(
            current_manifest_path.parent, current_index[query_id]
        )
        for query_id in current_ids
        if query_id in current_index
    ]
    partition_binding = validate_partition_binding(partition_audit_path)
    timeout_receipt_binding = validate_timeout_receipt(
        current_manifest_path, current
    )
    current_query_error_count = sum(
        len(result["errors"]) for result in query_results
    )
    blocking_issue_count = (
        len(continuity_errors)
        + len(semantic_differences)
        + len(query_binding_differences)
        + len(collection_binding_differences)
        + current_query_error_count
        + len(partition_binding["errors"])
        + len(timeout_receipt_binding["errors"])
    )
    semantic_record_drift = bool(semantic_differences)
    manifest_bytes_differ = (
        sha256_file(baseline_manifest_path)
        != sha256_file(current_manifest_path)
    )
    if semantic_record_drift:
        classification = "semantic_record_drift"
    elif (
        manifest_bytes_differ
        and run_metadata_differences
        and not collection_binding_differences
        and not query_binding_differences
        and not continuity_errors
    ):
        classification = (
            "run_timestamp_timeout_receipt_rematerialization_only"
        )
    elif manifest_bytes_differ:
        classification = "non_record_manifest_difference"
    else:
        classification = "byte_identical_manifests"

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if blocking_issue_count == 0 else "FAIL",
        "blocking_issue_count": blocking_issue_count,
        "read_only_scope": {
            "source_artifact_write_operations": 0,
            "collector_invocations": 0,
            "production_sync_or_promotion_operations": 0,
            "interpretation": (
                "Only this derived audit report and its checksum sidecar may "
                "be written; collector, source-ledger, review, production, "
                "and mainframe artifacts are read-only inputs."
            ),
        },
        "manifest_continuity": {
            "baseline_manifest": str(baseline_manifest_path),
            "baseline_manifest_sha256": sha256_file(
                baseline_manifest_path
            ),
            "current_manifest": str(current_manifest_path),
            "current_manifest_sha256": sha256_file(current_manifest_path),
            "manifest_bytes_differ": manifest_bytes_differ,
            "expected_query_count": expected_query_count,
            "baseline_query_count": len(baseline_ids),
            "current_query_count": len(current_ids),
            "baseline_query_ids": baseline_ids,
            "current_query_ids": current_ids,
            "query_ids_identical_in_order": baseline_ids == current_ids,
            "query_semantic_fields": list(QUERY_SEMANTIC_FIELDS),
            "baseline_semantic_snapshot_sha256": canonical_json_sha256(
                [
                    _query_projection(baseline_index[query_id])
                    for query_id in baseline_ids
                    if query_id in baseline_index
                ]
            ),
            "current_semantic_snapshot_sha256": canonical_json_sha256(
                [
                    _query_projection(current_index[query_id])
                    for query_id in current_ids
                    if query_id in current_index
                ]
            ),
            "semantic_query_differences": semantic_differences,
            "query_file_binding_differences": query_binding_differences,
            "collection_binding_differences": (
                collection_binding_differences
            ),
            "continuity_contract_errors": continuity_errors,
            "semantic_record_drift_detected": semantic_record_drift,
            "run_metadata_difference_count": len(
                run_metadata_differences
            ),
            "run_metadata_differences": run_metadata_differences,
            "difference_classification": classification,
            "interpretation": (
                "Different manifest bytes are collector-run rematerialization "
                "when differences are confined to timestamps, honored "
                "cooldown state, immutable timeout receipts, and failure "
                "metadata while every per-query semantic field and collection "
                "binding remains identical. Static verification safety policy and "
                "unknown manifest changes are not presumed run metadata. This proves "
                "retained semantic continuity, not collector completion or feed health."
            ),
        },
        "current_query_artifact_validation": {
            "referenced_query_file_count": len(query_results),
            "all_referenced_query_and_page_files_validate": (
                current_query_error_count == 0
                and len(query_results) == expected_query_count
            ),
            "error_count": current_query_error_count,
            "queries": query_results,
        },
        "candidate_review_partition_binding": partition_binding,
        "current_timeout_receipt_binding": timeout_receipt_binding,
    }
    report["audit_payload_sha256"] = canonical_json_sha256(report)
    return report


def write_report(
    report: dict[str, Any],
    output_path: Path,
    protected: Iterable[Path] = (),
    evidence_roots: Iterable[Path] = (),
) -> str:
    output_path = output_path.resolve()
    sidecar = output_path.with_suffix(output_path.suffix + ".sha256")
    # Every query, page and timeout receipt the manifest references resolves
    # inside its run directory (_safe_relative_file), so refusing the whole
    # tree protects all of them, including ones a later run will add.
    for root in evidence_roots:
        root = Path(root).resolve()
        for target in (output_path, sidecar):
            if target.is_relative_to(root):
                raise ValueError("report output must not be written inside an evidence tree")
    for value in protected:
        source = Path(value).resolve()
        for target in (output_path, sidecar):
            if target == source or (
                target.exists() and source.exists() and target.samefile(source)
            ):
                raise ValueError("report output must not overwrite a bound input")
    body = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    file_sha = hashlib.sha256(body).hexdigest()
    atomic_write_bytes(output_path, body)
    atomic_write_bytes(sidecar, f"{file_sha}  {output_path.name}\n".encode("ascii"))
    return file_sha


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--current-manifest", type=Path, required=True)
    parser.add_argument("--partition-audit", type=Path, required=True)
    parser.add_argument("--expected-query-count", type=int, default=22)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = audit_continuity(
            args.baseline_manifest,
            args.current_manifest,
            args.partition_audit,
            expected_query_count=args.expected_query_count,
        )
        review_files = [
            Path(item["file"])
            for item in report["candidate_review_partition_binding"]["bindings"]
            if isinstance(item.get("file"), str) and item["file"]
        ]
        file_sha = write_report(
            report,
            args.output,
            [args.baseline_manifest, args.current_manifest, args.partition_audit,
             *review_files],
            evidence_roots=[args.current_manifest.resolve().parent],
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"continuity audit failed before report generation: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "blocking_issue_count": report["blocking_issue_count"],
                "difference_classification": report[
                    "manifest_continuity"
                ]["difference_classification"],
                "output": str(args.output.resolve()),
                "output_file_sha256": file_sha,
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())

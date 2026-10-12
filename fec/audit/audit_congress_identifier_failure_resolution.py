#!/usr/bin/env python3
"""Prove that every Congress identifier fast-run failure was later resolved.

Failure receipts are immutable historical evidence.  This audit never removes
or rewrites them.  A receipt is resolved only when:

* its own filename, declared digest, and content digest agree;
* progress and the final manifest are current and exactly complete;
* the failed query is present exactly once in that manifest; and
* the referenced query/page artifacts exist and reconcile semantically and by
  their declared record hashes.

The generated ledger is an audit receipt only.  It does not modify collection
evidence or authorize scoring, synchronization, or promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = (
    ROOT
    / "audits"
    / "legfix5"
    / "identifier_probe_fast_20260726"
)
DEFAULT_OUTPUT = DEFAULT_RUN / "failure_resolution_receipt.json"
EXPECTED_MANIFEST_SCHEMA = "acu-congress-advanced-pages-manifest-v1"
EXPECTED_PROGRESS_SCHEMA = "congress-identifier-fast-progress-v1"
EXPECTED_QUERY_SCHEMA = "congress-advanced-search-query-v1"
EXPECTED_PAGE_SCHEMA = "congress-advanced-search-page-v1"
EXPECTED_FAILURE_SCHEMA = "congress-identifier-fast-failure-v1"
PRIMARY_INSTRUMENT = "Congress.gov Advanced Search - Legislation"
QUERY_MANIFEST_FIELDS = (
    "query_id",
    "label",
    "scoring_category",
    "record_kind",
    "parent_category_id",
    "parent_category_label",
    "identifier_text",
    "title_alias",
    "ambiguous_short_act_alias",
    "membership_non_dispositive",
    "source_paragraph",
    "result_total",
    "unique_record_count",
    "page_count",
    "counts_by_congress",
    "counts_by_type",
    "records_sha256",
    "validation",
)
QUERY_VALIDATION_FIELDS = (
    "exact_result_total",
    "unique_result_total",
    "every_page_exact",
    "single_result_redirect_proven",
)


class FailureResolutionAuditError(RuntimeError):
    """Raised when one or more historical failures remain unresolved."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def javascript_json_bytes(value: Any) -> bytes:
    """Match JSON.stringify for the evidence structures used by the collector."""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_receipt_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def record_hash(records: list[Any]) -> str:
    return sha256_bytes(javascript_json_bytes(records))


def file_item(path: Path, base: Path) -> dict[str, Any]:
    return {
        "file": path.relative_to(base).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def read_json_bytes(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = path.read_bytes()
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FailureResolutionAuditError(
            f"invalid JSON object: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise FailureResolutionAuditError(f"expected JSON object: {path}")
    return value, payload


def resolve_evidence_path(run_dir: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise FailureResolutionAuditError("missing evidence path")
    candidate = (run_dir / Path(relative.replace("/", os.sep))).resolve()
    try:
        candidate.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise FailureResolutionAuditError(
            f"evidence path escapes run directory: {relative}"
        ) from exc
    return candidate


def all_true_fields(value: Any, required: tuple[str, ...]) -> bool:
    return isinstance(value, dict) and all(value.get(field) is True for field in required)


def dedupe_reasons(reasons: list[str]) -> list[str]:
    return list(dict.fromkeys(reasons))


def audit_query_artifact(
    run_dir: Path,
    manifest_entry: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    query_id = manifest_entry.get("query_id")
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    sources: list[dict[str, Any]] = []
    query_file_value = manifest_entry.get("query_file")
    try:
        query_path = resolve_evidence_path(run_dir, query_file_value)
    except FailureResolutionAuditError as exc:
        reasons.append(str(exc))
        return {
            "query_file": query_file_value,
            "checks": checks,
            "resolved": False,
            "unresolved_reasons": reasons,
        }, sources

    checks["query_artifact_exists"] = query_path.is_file()
    if not query_path.is_file():
        reasons.append(f"query artifact missing: {query_file_value}")
        return {
            "query_file": query_file_value,
            "checks": checks,
            "resolved": False,
            "unresolved_reasons": reasons,
        }, sources

    sources.append(file_item(query_path, run_dir))
    try:
        artifact, _ = read_json_bytes(query_path)
    except FailureResolutionAuditError as exc:
        reasons.append(str(exc))
        return {
            "query_file": query_file_value,
            "checks": checks,
            "resolved": False,
            "unresolved_reasons": reasons,
        }, sources

    checks["query_schema"] = artifact.get("schema") == EXPECTED_QUERY_SCHEMA
    checks["query_id"] = artifact.get("query_id") == query_id
    checks["primary_instrument"] = (
        artifact.get("primary_instrument") == PRIMARY_INSTRUMENT
    )
    checks["manifest_fields_match"] = all(
        artifact.get(field) == manifest_entry.get(field)
        for field in QUERY_MANIFEST_FIELDS
    )
    records = artifact.get("records")
    checks["records_is_list"] = isinstance(records, list)
    if not isinstance(records, list):
        records = []
    actual_records_hash = record_hash(records)
    checks["records_hash_recomputed"] = (
        artifact.get("records_sha256") == actual_records_hash
    )
    checks["manifest_records_hash"] = (
        manifest_entry.get("records_sha256") == actual_records_hash
    )
    canonical_keys = [
        record.get("canonical_key")
        for record in records
        if isinstance(record, dict)
    ]
    checks["records_are_objects"] = len(canonical_keys) == len(records)
    checks["record_count_exact"] = (
        artifact.get("result_total")
        == artifact.get("unique_record_count")
        == len(records)
    )
    checks["canonical_keys_unique"] = (
        None not in canonical_keys
        and len(canonical_keys) == len(set(canonical_keys))
    )
    checks["record_query_binding"] = all(
        record.get("source_query_id") == query_id
        for record in records
        if isinstance(record, dict)
    )
    checks["query_validation"] = all_true_fields(
        artifact.get("validation"),
        QUERY_VALIDATION_FIELDS,
    )

    pages = artifact.get("pages")
    checks["pages_is_list"] = isinstance(pages, list)
    if not isinstance(pages, list):
        pages = []
    checks["page_count_exact"] = artifact.get("page_count") == len(pages)
    page_records: list[dict[str, Any]] = []
    page_numbers: list[int] = []
    page_checks: list[dict[str, Any]] = []

    for ordinal, descriptor in enumerate(pages, 1):
        page_result: dict[str, Any] = {
            "ordinal": ordinal,
            "file": descriptor.get("file") if isinstance(descriptor, dict) else None,
            "checks": {},
            "resolved": False,
        }
        local_reasons: list[str] = []
        if not isinstance(descriptor, dict):
            local_reasons.append("page descriptor is not an object")
            page_result["unresolved_reasons"] = local_reasons
            page_checks.append(page_result)
            continue
        try:
            page_path = resolve_evidence_path(run_dir, descriptor.get("file"))
        except FailureResolutionAuditError as exc:
            local_reasons.append(str(exc))
            page_result["unresolved_reasons"] = local_reasons
            page_checks.append(page_result)
            continue
        page_result["checks"]["page_artifact_exists"] = page_path.is_file()
        if not page_path.is_file():
            local_reasons.append(f"page artifact missing: {descriptor.get('file')}")
            page_result["unresolved_reasons"] = local_reasons
            page_checks.append(page_result)
            continue
        sources.append(file_item(page_path, run_dir))
        try:
            page, _ = read_json_bytes(page_path)
        except FailureResolutionAuditError as exc:
            local_reasons.append(str(exc))
            page_result["unresolved_reasons"] = local_reasons
            page_checks.append(page_result)
            continue
        page_records_value = page.get("records")
        if not isinstance(page_records_value, list):
            page_records_value = []
        actual_page_hash = record_hash(page_records_value)
        page_result["checks"].update(
            {
                "page_schema": page.get("schema") == EXPECTED_PAGE_SCHEMA,
                "query_id": page.get("query_id") == query_id,
                "page_number": (
                    page.get("page_number") == descriptor.get("page_number")
                ),
                "result_total": (
                    page.get("result_total") == artifact.get("result_total")
                ),
                "result_range": (
                    page.get("result_range") == descriptor.get("result_range")
                ),
                "record_count": (
                    len(page_records_value) == descriptor.get("record_count")
                ),
                "records_hash_recomputed": (
                    page.get("records_sha256") == actual_page_hash
                ),
                "descriptor_records_hash": (
                    descriptor.get("records_sha256") == actual_page_hash
                ),
                "page_validation": (
                    isinstance(page.get("validation"), dict)
                    and bool(page["validation"])
                    and all(value is True for value in page["validation"].values())
                ),
                "record_query_binding": all(
                    isinstance(record, dict)
                    and record.get("source_query_id") == query_id
                    for record in page_records_value
                ),
            }
        )
        for check_name, passed in page_result["checks"].items():
            if not passed:
                local_reasons.append(f"page {ordinal} failed {check_name}")
        page_result["resolved"] = not local_reasons
        page_result["unresolved_reasons"] = local_reasons
        page_checks.append(page_result)
        page_records.extend(page_records_value)
        page_number = page.get("page_number")
        if isinstance(page_number, int):
            page_numbers.append(page_number)

    checks["page_numbers_contiguous"] = page_numbers == list(
        range(1, len(pages) + 1)
    )
    checks["page_records_match_query"] = record_hash(page_records) == actual_records_hash
    checks["every_page_resolved"] = len(page_checks) == len(pages) and all(
        item["resolved"] for item in page_checks
    )
    for check_name, passed in checks.items():
        if not passed:
            reasons.append(f"query artifact failed {check_name}")
    for page in page_checks:
        reasons.extend(page["unresolved_reasons"])

    return {
        "query_file": query_file_value,
        "query_artifact_sha256": sources[0]["sha256"] if sources else None,
        "checks": checks,
        "pages": page_checks,
        "resolved": not reasons,
        "unresolved_reasons": dedupe_reasons(reasons),
    }, sources


def audit_failure_resolution(
    run_dir: Path,
    expected_total: int = 210,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest_path = run_dir / "manifest.json"
    progress_path = run_dir / "progress.json"
    failure_dir = run_dir / "failure_receipts"
    global_errors: list[str] = []

    manifest, _ = read_json_bytes(manifest_path)
    progress, _ = read_json_bytes(progress_path)
    source_manifest = file_item(manifest_path, run_dir)
    source_progress = file_item(progress_path, run_dir)

    selected_ids = manifest.get("selected_query_ids")
    manifest_queries = manifest.get("queries")
    if not isinstance(selected_ids, list):
        selected_ids = []
    if not isinstance(manifest_queries, list):
        manifest_queries = []
    query_ids = [
        entry.get("query_id")
        for entry in manifest_queries
        if isinstance(entry, dict)
    ]
    completion_checks = {
        "manifest_schema": manifest.get("schema") == EXPECTED_MANIFEST_SCHEMA,
        "manifest_status_complete": manifest.get("status") == "complete",
        "manifest_primary_instrument": (
            manifest.get("primary_instrument") == PRIMARY_INSTRUMENT
        ),
        "manifest_quorum_unused": manifest.get("quorum_used") is False,
        "progress_schema": progress.get("schema") == EXPECTED_PROGRESS_SCHEMA,
        "progress_status_complete": progress.get("status") == "complete",
        "progress_completed_exact": (
            progress.get("completed_queries") == expected_total
        ),
        "progress_total_exact": progress.get("total_queries") == expected_total,
        "manifest_selected_count_exact": len(selected_ids) == expected_total,
        "manifest_query_count_exact": len(manifest_queries) == expected_total,
        "manifest_selected_unique": len(selected_ids) == len(set(selected_ids)),
        "manifest_query_ids_unique": len(query_ids) == len(set(query_ids)),
        "selected_query_set_matches_manifest": set(selected_ids) == set(query_ids),
    }
    for check_name, passed in completion_checks.items():
        if not passed:
            global_errors.append(f"completion failed {check_name}")
    complete_current = not global_errors

    manifest_entries: dict[str, dict[str, Any]] = {}
    for entry in manifest_queries:
        if isinstance(entry, dict) and isinstance(entry.get("query_id"), str):
            manifest_entries.setdefault(entry["query_id"], entry)

    receipt_files = (
        sorted(failure_dir.glob("*.json")) if failure_dir.is_dir() else []
    )
    receipt_ledger: list[dict[str, Any]] = []
    receipt_sources: list[dict[str, Any]] = []
    grouped_receipts: dict[str, list[dict[str, Any]]] = {}

    for failure_path in receipt_files:
        source = file_item(failure_path, run_dir)
        receipt_sources.append(source)
        item: dict[str, Any] = {
            "file": source["file"],
            "file_sha256": source["sha256"],
            "query_id": None,
            "integrity_checks": {},
            "resolved": False,
            "unresolved_reasons": [],
        }
        try:
            failure, _ = read_json_bytes(failure_path)
        except FailureResolutionAuditError as exc:
            item["unresolved_reasons"].append(str(exc))
            receipt_ledger.append(item)
            continue
        query_id = failure.get("query_id")
        declared_hash = failure.get("receipt_sha256")
        hash_body = dict(failure)
        hash_body.pop("receipt_sha256", None)
        computed_hash = sha256_bytes(javascript_json_bytes(hash_body))
        item["query_id"] = query_id
        item["declared_receipt_sha256"] = declared_hash
        item["computed_receipt_sha256"] = computed_hash
        item["integrity_checks"] = {
            "schema": failure.get("schema") == EXPECTED_FAILURE_SCHEMA,
            "query_id_present": isinstance(query_id, str) and bool(query_id),
            "filename_matches_declared": failure_path.stem == declared_hash,
            "content_matches_declared": computed_hash == declared_hash,
        }
        for check_name, passed in item["integrity_checks"].items():
            if not passed:
                item["unresolved_reasons"].append(
                    f"failure receipt failed {check_name}"
                )
        if isinstance(query_id, str) and query_id:
            grouped_receipts.setdefault(query_id, []).append(item)
        receipt_ledger.append(item)

    query_ledger: list[dict[str, Any]] = []
    query_sources: list[dict[str, Any]] = []
    for query_id in sorted(grouped_receipts):
        receipts = grouped_receipts[query_id]
        entry = manifest_entries.get(query_id)
        item: dict[str, Any] = {
            "query_id": query_id,
            "failure_receipts": [receipt["file"] for receipt in receipts],
            "manifest_present": entry is not None,
            "complete_210_of_210_current": complete_current,
            "resolved": False,
            "unresolved_reasons": [],
        }
        if not complete_current:
            item["unresolved_reasons"].append(
                "final manifest/progress is not exactly complete and current"
            )
        if entry is None:
            item["unresolved_reasons"].append("failed query absent from final manifest")
        else:
            artifact_audit, sources = audit_query_artifact(run_dir, entry)
            item["artifact"] = artifact_audit
            query_sources.extend(sources)
            if not artifact_audit["resolved"]:
                item["unresolved_reasons"].extend(
                    artifact_audit["unresolved_reasons"]
                )
        if any(receipt["unresolved_reasons"] for receipt in receipts):
            item["unresolved_reasons"].append(
                "one or more historical failure receipts failed integrity"
            )
        item["unresolved_reasons"] = dedupe_reasons(item["unresolved_reasons"])
        item["resolved"] = not item["unresolved_reasons"]
        query_ledger.append(item)
        for receipt in receipts:
            receipt["resolved"] = (
                item["resolved"] and not receipt["unresolved_reasons"]
            )
            if not item["resolved"]:
                receipt["unresolved_reasons"] = dedupe_reasons(
                    receipt["unresolved_reasons"]
                    + [f"query unresolved: {query_id}"]
                )

    unresolved_receipts = [
        {
            "file": item["file"],
            "query_id": item["query_id"],
            "reasons": item["unresolved_reasons"],
        }
        for item in receipt_ledger
        if not item["resolved"]
    ]
    unresolved_queries = [
        {
            "query_id": item["query_id"],
            "reasons": item["unresolved_reasons"],
        }
        for item in query_ledger
        if not item["resolved"]
    ]
    resolved_receipts = sum(item["resolved"] for item in receipt_ledger)
    resolved_queries = sum(item["resolved"] for item in query_ledger)
    source_hashes = {
        "manifest": source_manifest,
        "progress": source_progress,
        "failure_receipts": receipt_sources,
        "failed_query_and_page_artifacts": sorted(
            {item["file"]: item for item in query_sources}.values(),
            key=lambda item: item["file"],
        ),
    }

    receipt_body: dict[str, Any] = {
        "schema": "congress-identifier-failure-resolution-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "PASS"
            if complete_current
            and resolved_receipts == len(receipt_ledger)
            and resolved_queries == len(query_ledger)
            else "FAIL"
        ),
        "policy": {
            "failure_receipts_preserved": True,
            "collection_evidence_modified": False,
            "scoring_sync_promotion_authorized": False,
            "resolution_requires_exact_complete_progress": True,
            "resolution_requires_hash_and_semantic_reconciliation": True,
        },
        "expected_query_total": expected_total,
        "completion_checks": completion_checks,
        "counts": {
            "historical_failure_receipt_count": len(receipt_ledger),
            "unique_failed_query_count": len(query_ledger),
            "resolved_failure_receipt_count": resolved_receipts,
            "resolved_query_count": resolved_queries,
            "unresolved_failure_receipt_count": len(unresolved_receipts),
            "unresolved_query_count": len(unresolved_queries),
        },
        "global_errors": global_errors,
        "unresolved_failure_receipts": unresolved_receipts,
        "unresolved_queries": unresolved_queries,
        "query_resolution_ledger": query_ledger,
        "failure_receipt_ledger": receipt_ledger,
        "source_hashes": source_hashes,
    }
    receipt_body["receipt_sha256"] = sha256_bytes(
        canonical_receipt_bytes(receipt_body)
    )
    return receipt_body


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-total", type=int, default=210)
    args = parser.parse_args()
    receipt = audit_failure_resolution(args.run_dir, args.expected_total)
    atomic_write_json(args.output, receipt)
    counts = receipt["counts"]
    print(
        f"{receipt['status']}: "
        f"{counts['resolved_failure_receipt_count']}/"
        f"{counts['historical_failure_receipt_count']} receipts; "
        f"{counts['resolved_query_count']}/"
        f"{counts['unique_failed_query_count']} failed queries"
    )
    print(args.output.resolve())
    if receipt["status"] != "PASS":
        raise FailureResolutionAuditError(
            "one or more historical identifier collection failures remain unresolved"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

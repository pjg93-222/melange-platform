#!/usr/bin/env python3
"""Build a read-only culling and corpus-integration readiness snapshot.

The output is audit state only.  Historical accepted generations, shadow
receipts, and regression receipts are intentionally not inherited: those
gates remain false until new artifacts are bound to the final culling hashes.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from legislation.legislation_culling_integration_contract import evaluate_integration_gate
from legislation.manage_legislation_junk_pile import compute_effective_state


ROOT = Path(__file__).resolve().parents[2]
AUDIT_ROOT = ROOT / "audits" / "legfix5"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--discovery-manifest",
        type=Path,
        default=AUDIT_ROOT
        / "congress_advanced_pages_20260725"
        / "manifest.json",
    )
    parser.add_argument(
        "--full-text-progress",
        type=Path,
        default=AUDIT_ROOT
        / "congress_advanced_pages_20260725"
        / "text_relevance_httr_full_stage_20260726"
        / "progress.json",
    )
    parser.add_argument(
        "--billstatus-manifest",
        type=Path,
        default=AUDIT_ROOT
        / "billstatus_bulk_enrichment"
        / "fetch_runs"
        / "c7ca822fd5951478a33a814d228b71ffdf69745e6d823001fefaba620282c435"
        / "run_manifest.json",
    )
    parser.add_argument(
        "--review-manifest",
        type=Path,
        default=AUDIT_ROOT
        / "congress_advanced_pages_20260725"
        / "candidate_membership_review"
        / "manifest.json",
    )
    parser.add_argument(
        "--quorum-floor-receipt",
        type=Path,
        default=AUDIT_ROOT
        / "quorum_floor_20260726"
        / "reconciliation_receipt.json",
    )
    parser.add_argument(
        "--identifier-receipt",
        type=Path,
        default=AUDIT_ROOT
        / "identifier_probe_fast_20260726"
        / "failure_resolution_receipt.json",
    )
    parser.add_argument(
        "--baseline-control",
        type=Path,
        default=AUDIT_ROOT / "PRE_LEGISLATION_RANK_CONTROL_2026-07-25.json",
    )
    parser.add_argument(
        "--junk-root",
        type=Path,
        default=AUDIT_ROOT / "legislation_junk_pile",
    )
    parser.add_argument(
        "--candidate-evidence-manifest",
        type=Path,
        default=AUDIT_ROOT
        / "culling_integration"
        / "candidate_evidence_manifest.json",
    )
    parser.add_argument(
        "--partition-manifest",
        type=Path,
        default=AUDIT_ROOT
        / "culling_integration"
        / "candidate_partition_manifest.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=AUDIT_ROOT / "culling_integration" / "current.json",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_ndjson_rows_hash(path: Path) -> str:
    """Match stableHash(rows) from the JavaScript discovery collector."""

    digest = hashlib.sha256()
    digest.update(b"[")
    first = True
    with path.open("r", encoding="utf-8-sig") as stream:
        for raw_line in stream:
            if not raw_line.strip():
                continue
            if not first:
                digest.update(b",")
            first = False
            row = json.loads(raw_line)
            digest.update(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
    digest.update(b"]")
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def optional_receipt(path: Path) -> dict[str, Any] | None:
    return receipt(path) if path.is_file() else None


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def verify_baseline(control_path: Path) -> tuple[bool, dict[str, Any]]:
    try:
        control = read_json(control_path)
        baseline = control.get("baseline") or {}
        manifest_binding = baseline.get("manifest") or {}
        manifest_path = ROOT / str(manifest_binding.get("path") or "")
        if (
            control.get("status") != "PRESERVED_HASH_BOUND_CONTROL"
            or baseline.get("file_count") != 55
            or not manifest_path.is_file()
            or sha256_file(manifest_path) != manifest_binding.get("sha256")
        ):
            return False, {"reason": "baseline control or manifest binding failed"}
        manifest = read_json(manifest_path)
        files = manifest.get("files") or []
        if manifest.get("file_count") != 55 or len(files) != 55:
            return False, {"reason": "baseline file count drifted"}
        failures = []
        for row in files:
            path = Path(str(row.get("copy") or ""))
            if (
                not path.is_file()
                or path.stat().st_size != row.get("size")
                or sha256_file(path) != row.get("sha256")
            ):
                failures.append(str(path))
        return not failures, {
            "control": receipt(control_path),
            "manifest": receipt(manifest_path),
            "file_count": len(files),
            "verification_failures": failures,
        }
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        return False, {"reason": f"{type(exc).__name__}: {exc}"}


def resolve_declared(path_value: Any, manifest_path: Path) -> Path | None:
    """Resolve a path a manifest declares for one of its own outputs."""

    if not isinstance(path_value, str) or not path_value.strip():
        return None
    candidate = Path(path_value.replace("\\", "/"))
    if candidate.is_absolute():
        return candidate
    rooted = ROOT / candidate
    if rooted.is_file():
        return rooted
    sibling = manifest_path.parent / candidate
    if sibling.is_file():
        return sibling
    return rooted


def verify_review_ledger(
    manifest: dict[str, Any], manifest_path: Path
) -> tuple[bool, dict[str, Any]]:
    """Verify the review ledger BODY against the sha256 its manifest declares.

    Schema, ``membership_count`` and ``discovery_manifest_sha256`` cannot tell
    two review ledgers apart.  The consumed ledger under
    ``congress_advanced_pages_20260725/candidate_membership_review/``
    (200,363,847 B, 83 fields per row) and the unread one under
    ``core62_membership_review_staging/`` (312,129,291 B, 94 fields per row)
    carry the identical schema and the identical 62,687 rows, so every one of
    those three conditions held for both and the gate reported the same truth
    whichever it was pointed at.

    A manifest that declares no hash for its own ledger cannot be shown to
    describe the body on disk, so it is not current either.
    """

    outputs = manifest.get("outputs")
    binding = outputs.get("ledger") if isinstance(outputs, dict) else None
    binding = binding if isinstance(binding, dict) else {}

    declared_sha = binding.get("sha256")
    declared_bytes = binding.get("bytes")
    declared_path = binding.get("path") or manifest.get("ledger_file")

    evidence: dict[str, Any] = {
        "verified": False,
        "declared_path": declared_path,
        "declared_bytes": declared_bytes,
        "declared_sha256": declared_sha,
    }

    if not isinstance(declared_sha, str) or len(declared_sha) != 64:
        evidence["reason"] = (
            "manifest declares no sha256 for its own review ledger; the body "
            "cannot be bound to this manifest"
        )
        return False, evidence

    resolved = resolve_declared(declared_path, manifest_path)
    evidence["resolved_path"] = str(resolved) if resolved is not None else None
    if resolved is None or not resolved.is_file():
        evidence["reason"] = "declared review ledger is not on disk"
        return False, evidence

    actual_bytes = resolved.stat().st_size
    evidence["actual_bytes"] = actual_bytes
    if isinstance(declared_bytes, int) and actual_bytes != declared_bytes:
        evidence["reason"] = (
            "review ledger byte count differs from the manifest declaration"
        )
        return False, evidence

    actual_sha = sha256_file(resolved)
    evidence["actual_sha256"] = actual_sha
    if actual_sha != declared_sha:
        evidence["reason"] = (
            "review ledger body does not match the sha256 its manifest declares"
        )
        return False, evidence

    evidence["verified"] = True
    return True, evidence


def manifest_complete(
    manifest: dict[str, Any] | None,
    *,
    schema: str,
    candidate_count: int,
    membership_count: int,
) -> bool:
    if not manifest or manifest.get("schema") != schema:
        return False
    return bool(
        manifest.get("status") in {"complete", "PASS"}
        and manifest.get("candidate_count") == candidate_count
        and manifest.get("membership_count") == membership_count
    )


def main() -> int:
    args = parse_args()
    discovery = read_json(args.discovery_manifest.resolve())
    full_text = read_json(args.full_text_progress.resolve())
    billstatus = read_json(args.billstatus_manifest.resolve())
    review = read_json(args.review_manifest.resolve())
    quorum_floor = read_json(args.quorum_floor_receipt.resolve())
    identifier = read_json(args.identifier_receipt.resolve())
    evidence_manifest = optional_json(args.candidate_evidence_manifest.resolve())
    partition_manifest = optional_json(args.partition_manifest.resolve())

    union = discovery.get("union") or {}
    candidate_count = int(union.get("unique_record_count") or 0)
    membership_count = int(union.get("membership_count") or 0)
    selected_query_ids = discovery.get("selected_query_ids") or []
    queries = discovery.get("queries") or []

    records_path = args.discovery_manifest.resolve().parent / str(
        union.get("records_file") or ""
    )
    memberships_path = args.discovery_manifest.resolve().parent / str(
        union.get("memberships_file") or ""
    )
    discovery_hashes_match = bool(
        records_path.is_file()
        and memberships_path.is_file()
        and stable_ndjson_rows_hash(records_path) == union.get("records_sha256")
        and stable_ndjson_rows_hash(memberships_path)
        == union.get("memberships_sha256")
    )

    baseline_ok, baseline_evidence = verify_baseline(
        args.baseline_control.resolve()
    )
    full_text_complete = bool(
        full_text.get("status") == "complete"
        and full_text.get("current") == candidate_count
        and full_text.get("total") == candidate_count
        and full_text.get("failure_count") == 0
    )
    billstatus_partition = billstatus.get("partition") or {}
    billstatus_complete = bool(
        billstatus.get("status") == "COMPLETE"
        and billstatus_partition.get("all_candidates") == candidate_count
        and billstatus_partition.get("top_level_completed")
        == billstatus_partition.get("top_level_planned")
        and billstatus_partition.get("top_level_failed") == 0
    )
    # The ledger BODY is rehashed here.  Without it the three conditions below
    # are satisfied identically by two different 62,687-row ledgers, so the
    # gate could not tell which one it was reporting on.
    review_ledger_ok, review_ledger_evidence = verify_review_ledger(
        review, args.review_manifest.resolve()
    )
    discovery_manifest_sha256 = sha256_file(args.discovery_manifest.resolve())
    review_binds_discovery = (
        review.get("discovery_manifest_sha256") == discovery_manifest_sha256
    )
    review_current = bool(
        review.get("schema") == "acu-legislation-membership-review-v3"
        and review.get("membership_count") == membership_count
        and review_binds_discovery
        and review_ledger_ok
    )
    review_complete = bool(
        review_current
        and review.get("unresolved_count") == 0
        and review.get("promotion_gate_passed") is True
    )

    quorum_counts = quorum_floor.get("counts") or {}
    quorum_floor_ok = bool(
        quorum_floor.get("schema")
        == "acu-legislation-quorum-recall-floor-audit-v1"
        and quorum_floor.get("status") == "PASS"
        and quorum_counts.get("unresolved") == 0
        and quorum_counts.get("floor_unique_identifiers")
        == quorum_counts.get("present_in_primary_union")
    )
    identifier_checks = identifier.get("completion_checks") or {}
    identifier_ok = bool(
        identifier.get("schema") == "congress-identifier-failure-resolution-v1"
        and identifier.get("status") == "PASS"
        and identifier.get("expected_query_total") == 210
        and identifier_checks
        and all(value is True for value in identifier_checks.values())
        and (identifier.get("counts") or {}).get("unresolved_query_count") == 0
    )

    try:
        junk_state = compute_effective_state(args.junk_root.resolve())
        junk_ok = True
        junk_counts = {
            "exclusions": len(junk_state.get("exclusions") or []),
            "restorations": len(junk_state.get("restorations") or []),
            "current": len(junk_state.get("current") or []),
            "restored": len(junk_state.get("restored") or []),
        }
    except Exception as exc:  # fail closed; serialized as audit evidence
        junk_ok = False
        junk_counts = {"error": str(exc)}

    evidence_complete = manifest_complete(
        evidence_manifest,
        schema="acu-legislation-candidate-evidence-manifest-v1",
        candidate_count=candidate_count,
        membership_count=membership_count,
    )
    partition_complete = manifest_complete(
        partition_manifest,
        schema="acu-legislation-culling-partition-manifest-v1",
        candidate_count=candidate_count,
        membership_count=membership_count,
    )
    roles_complete = bool(
        evidence_complete
        and (evidence_manifest or {}).get("relationship_roles_complete") is True
        and billstatus_complete
    )
    stages_complete = bool(
        evidence_complete
        and (evidence_manifest or {}).get("stage_actions_complete") is True
    )

    gates = {
        "baseline_snapshot_verified": baseline_ok,
        "discovery_complete": discovery.get("status") == "complete",
        "congress_advanced_is_primary": discovery.get("primary_instrument")
        == "Congress.gov Advanced Search - Legislation",
        "quorum_unused": discovery.get("quorum_used") is False,
        "registry_complete": len(selected_query_ids) == 62 and len(queries) == 62,
        "discovery_reconciliation_exact": discovery_hashes_match
        and candidate_count == 18557
        and membership_count == 64004,
        "quorum_floor_recall_audited": quorum_floor_ok,
        "identifier_probe_revalidation_complete": identifier_ok,
        "legacy_snapshot_reaudit_complete": bool(
            evidence_complete
            and (evidence_manifest or {}).get("legacy_bill_count") == 465
            and (evidence_manifest or {}).get("legacy_edge_count") == 10986
            and (evidence_manifest or {}).get("legacy_snapshot_reaudit_complete")
            is True
        ),
        "full_text_coverage_complete": full_text_complete,
        "full_text_failures_resolved": full_text_complete,
        "resumed_full_text_archives_reverified": bool(
            evidence_complete
            and (evidence_manifest or {}).get("resumed_archives_reverified") is True
        ),
        "all_exclusion_text_versions_reviewed": bool(
            evidence_complete
            and (evidence_manifest or {}).get(
                "all_exclusion_text_versions_reviewed"
            )
            is True
        ),
        "semantic_parser_parity_passed": bool(
            evidence_complete
            and (evidence_manifest or {}).get("semantic_parser_parity_passed")
            is True
            and (evidence_manifest or {}).get("semantic_parser_parity_count") == 109
        ),
        "amendment_evidence_closed": bool(
            evidence_complete
            and (evidence_manifest or {}).get("amendment_candidate_count") == 3703
            and (evidence_manifest or {}).get("amendment_unresolved_count") == 0
        ),
        "candidate_evidence_partition_complete": evidence_complete,
        "membership_adjudication_complete": review_complete,
        "membership_partition_exact": bool(
            partition_complete
            and (partition_manifest or {}).get("membership_partition_exact")
            is True
            and sum(
                int((partition_manifest or {}).get(name) or 0)
                for name in (
                    "keep_membership_count",
                    "junk_membership_count",
                    "flagged_membership_count",
                )
            )
            == membership_count
        ),
        "candidate_partition_exact": partition_complete,
        "junk_ledger_audit_passed": junk_ok,
        "relationship_roles_complete": roles_complete,
        "stage_actions_complete": stages_complete,
        # New generation-bound receipts are intentionally required later.
        "accepted_generation_hash_bound": False,
        "shadow_recompute_passed": False,
        "rank_drift_reviewed": False,
        "presync_audit_passed": False,
        "final_regression_hash_current": False,
    }
    result = evaluate_integration_gate(
        gates,
        candidate_count=candidate_count,
        membership_count=membership_count,
    )
    result.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "architecture_version": "acu-legislation-culling-integration-v1",
            "current_counts": {
                "discovery_candidates": candidate_count,
                "discovery_memberships": membership_count,
                "full_text_current": full_text.get("current"),
                "full_text_total": full_text.get("total"),
                "full_text_failures": full_text.get("failure_count"),
                "review_memberships": review.get("membership_count"),
                "review_unresolved": review.get("unresolved_count"),
                "billstatus_top_level_completed": billstatus_partition.get(
                    "top_level_completed"
                ),
                "billstatus_nested_amendments": billstatus_partition.get(
                    "nested_only_amendments_retained_unresolved"
                ),
                "junk_ledger": junk_counts,
            },
            "baseline_evidence": baseline_evidence,
            "review_binding": {
                "review_current": review_current,
                "review_complete": review_complete,
                "schema_matches": review.get("schema")
                == "acu-legislation-membership-review-v3",
                "membership_count_matches": review.get("membership_count")
                == membership_count,
                "discovery_manifest_sha256_matches": review_binds_discovery,
                "ledger_body_verified": review_ledger_ok,
                "ledger": review_ledger_evidence,
            },
            "inputs": {
                "discovery_manifest": receipt(args.discovery_manifest.resolve()),
                "full_text_progress": receipt(args.full_text_progress.resolve()),
                "billstatus_manifest": receipt(args.billstatus_manifest.resolve()),
                "review_manifest": receipt(args.review_manifest.resolve()),
                "quorum_floor_receipt": receipt(
                    args.quorum_floor_receipt.resolve()
                ),
                "identifier_receipt": receipt(args.identifier_receipt.resolve()),
                "candidate_evidence_manifest": optional_receipt(
                    args.candidate_evidence_manifest.resolve()
                ),
                "partition_manifest": optional_receipt(
                    args.partition_manifest.resolve()
                ),
            },
            "safety": {
                "candidate_deletion_performed": False,
                "scoring_performed": False,
                "production_write_performed": False,
                "main_corpus_write_performed": False,
                "sync_or_promotion_performed": False,
                "historical_downstream_receipts_inherited": False,
            },
        }
    )
    write_json_atomic(args.out.resolve(), result)
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

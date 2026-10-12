#!/usr/bin/env python3
"""Roll receipt-only Congress.gov timeouts through staged audit evidence.

This supervisor helper is intentionally zero-network and fail closed.  It
only accepts a newly selected strict manifest when the previous and current
collector checkpoints have exact query/page/record continuity and the current
immutable timeout receipt rehashes cleanly.  It never starts or stops the
collector and never authorizes culling, scoring, sync, or production writes.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import audit.audit_congress_collector_resume_continuity as CONTINUITY
import audit.audit_legislation_downstream_readiness as READINESS
import audit.audit_legislation_query_specificity as SPECIFICITY
from legislation import augment_partial_review_prefill_structured_relevance as AUGMENT
from legislation import refresh_billstatus_snapshot_evidence as SNAPSHOT
from legislation import run_legislation_presync_audit as PRESYNC


ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "audits" / "legfix5" / "congress_advanced_pages_20260725"
REGISTRY = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_search_registry.json"
)
PARTITION_AUDIT = (
    ROOT
    / "audits"
    / "legfix5"
    / "candidate_membership_review_partition_audit"
    / "candidate_membership_review_partition_audit.json"
)
SNAPSHOT_ROOT = (
    ROOT / "audits" / "legfix5" / "billstatus_bulk_enrichment" / "snapshots"
)
QUERY_SPECIFICITY_CURRENT = (
    ROOT
    / "audits"
    / "legfix5"
    / "query_specificity"
    / "current"
    / "query_specificity_audit.json"
)
PRESYNC_CONFIG = (
    ROOT / "audits" / "legfix5" / "presync_audit_config.json"
)
PRESYNC_OUT = (
    ROOT / "audits" / "legfix5" / "shadow_recompute" / "presync_current"
)
AUGMENT_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "partial_review_structured_augmentation"
    / "current.json"
)
READINESS_OUT = ROOT / "audits" / "legfix5" / "downstream_readiness"
ROLLOVER_ROOT = ROOT / "audits" / "legfix5" / "timeout_rollover"
CURRENT_POINTER = ROLLOVER_ROOT / "current.json"
SCHEMA = "legislation-timeout-semantic-rollover-receipt-v1"
POINTER_SCHEMA = "legislation-timeout-semantic-rollover-pointer-v1"


class RolloverError(ValueError):
    """Raised when a timeout transition cannot be proven locally."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RolloverError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
    require(isinstance(value, dict), f"JSON object required: {path}")
    return value


def file_receipt(path: Path) -> dict[str, Any]:
    path = path.resolve()
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def receipt_is_current(value: Any, path: Path | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return False
    observed = Path(raw_path).resolve()
    expected = path.resolve() if path is not None else observed
    recorded_bytes = value.get("bytes", value.get("size"))
    return bool(
        observed == expected
        and expected.is_file()
        and recorded_bytes == expected.stat().st_size
        and value.get("sha256") == sha256_file(expected)
    )


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(body)
    os.replace(temporary, path)


def selected_strict_manifest() -> Path:
    blockers: list[dict[str, Any]] = []
    selected = PRESYNC.select_strict_manifest(
        {"coverage": {"strict_run": str(RUN_DIR)}},
        workspace_root=ROOT,
        registry_path=REGISTRY,
        blockers=blockers,
    )
    require(
        selected is not None and not blockers,
        f"Strict-manifest selection failed: {blockers}",
    )
    return selected.resolve()


def selected_manifest_state() -> dict[str, Any]:
    """Return the validated local strict selection without writing anything."""

    selected = selected_strict_manifest()
    manifest = read_object(selected)
    progress = PRESYNC.strict_manifest_exact_progress(
        manifest,
        registry_sha256=sha256_file(REGISTRY),
        manifest_path=selected,
    )
    require(progress is not None, "Selected strict progress is invalid")
    return {
        "status": "CURRENT_SELECTION",
        "selected_strict_manifest": file_receipt(selected),
        "exact_query_count": progress,
        "final_coverage_complete": bool(
            selected.name == "manifest.json"
            and manifest.get("status") == "complete"
            and progress == len(manifest.get("selected_query_ids") or [])
            == 62
        ),
        "network_executed": False,
        "sync_or_promotion_performed": False,
    }


def validate_timeout_manifest(path: Path) -> dict[str, Any]:
    manifest = read_object(path)
    progress = PRESYNC.strict_manifest_exact_progress(
        manifest,
        registry_sha256=sha256_file(REGISTRY),
        manifest_path=path,
    )
    require(progress is not None, f"Strict manifest is not eligible: {path}")
    timeout = CONTINUITY.validate_timeout_receipt(path, manifest)
    require(
        timeout["status"] == "PASS"
        and timeout["immutable_receipt_hash_valid"] is True
        and timeout["safety_contract_valid"] is True,
        f"Timeout receipt is not immutable and safe: {timeout['errors']}",
    )
    return {
        "manifest": manifest,
        "progress": progress,
        "receipt": file_receipt(path),
        "timeout": timeout,
    }


def transition_needed(
    previous_manifest_receipt: dict[str, Any],
    selected_manifest_path: Path,
) -> bool:
    """Return true exactly when the selected strict receipt changed."""

    require(
        receipt_is_current(previous_manifest_receipt),
        "Previous strict-manifest receipt is stale",
    )
    return (
        previous_manifest_receipt["sha256"]
        != sha256_file(selected_manifest_path)
    )


def capped_timeout_transition(
    previous_manifest_path: Path,
    current_manifest_path: Path,
) -> dict[str, Any]:
    """Validate a monotonic receipt-only capped-timeout transition."""

    previous_manifest_path = previous_manifest_path.resolve()
    current_manifest_path = current_manifest_path.resolve()
    previous = read_object(previous_manifest_path)
    current = read_object(current_manifest_path)
    previous_timeout = CONTINUITY.validate_timeout_receipt(
        previous_manifest_path, previous
    )
    current_timeout = CONTINUITY.validate_timeout_receipt(
        current_manifest_path, current
    )
    require(
        previous_timeout["status"] == current_timeout["status"] == "PASS",
        "Capped-timeout receipt validation failed",
    )
    changed = (
        sha256_file(previous_manifest_path)
        != sha256_file(current_manifest_path)
    )
    previous_count = previous_timeout.get("consecutive_timeouts")
    current_count = current_timeout.get("consecutive_timeouts")
    require(
        isinstance(previous_count, int)
        and not isinstance(previous_count, bool)
        and isinstance(current_count, int)
        and not isinstance(current_count, bool),
        "Capped-timeout counters are invalid",
    )
    require(
        (not changed and current_count == previous_count)
        or (changed and current_count > previous_count),
        "Capped-timeout counter did not advance monotonically",
    )
    return {
        "changed": changed,
        "previous_count": previous_count,
        "current_count": current_count,
        "previous_receipt_sha256": previous_timeout["observed_sha256"],
        "current_receipt_sha256": current_timeout["observed_sha256"],
        "cooldown_ms": current_timeout.get("cooldown_ms"),
        "status": "PASS",
    }


def prior_manifest_path() -> Path:
    if CURRENT_POINTER.is_file():
        pointer = read_object(CURRENT_POINTER)
        prior = pointer.get("selected_strict_manifest")
        if receipt_is_current(prior):
            return Path(str(prior["path"])).resolve()
    require(
        QUERY_SPECIFICITY_CURRENT.is_file(),
        "No prior rollover or query-specificity current receipt exists",
    )
    specificity = read_object(QUERY_SPECIFICITY_CURRENT)
    prior = (specificity.get("inputs") or {}).get(
        "selected_strict_manifest"
    )
    require(
        receipt_is_current(prior),
        "Prior query-specificity strict receipt is stale",
    )
    return Path(str(prior["path"])).resolve()


def current_pointer_result(selected: Path) -> dict[str, Any] | None:
    if not CURRENT_POINTER.is_file():
        return None
    try:
        pointer = read_object(CURRENT_POINTER)
        if not (
            pointer.get("schema") == POINTER_SCHEMA
            and receipt_is_current(
                pointer.get("producer"), Path(__file__).resolve()
            )
            and receipt_is_current(
                pointer.get("selected_strict_manifest"), selected
            )
            and receipt_is_current(pointer.get("receipt"))
        ):
            return None
        receipt_path = Path(str(pointer["receipt"]["path"])).resolve()
        receipt = read_object(receipt_path)
        outputs = receipt.get("outputs") or {}
        producer_paths = {
            "rollover": Path(__file__),
            "continuity": Path(CONTINUITY.__file__),
            "snapshot": Path(SNAPSHOT.__file__),
            "specificity": Path(SPECIFICITY.__file__),
            "presync": Path(PRESYNC.__file__),
            "augmentation": Path(AUGMENT.__file__),
            "readiness": Path(READINESS.__file__),
        }
        producers = (
            (receipt.get("input_address") or {}).get("producers") or {}
        )
        if not (
            receipt.get("schema") == SCHEMA
            and receipt.get("rollover_id") == receipt_path.parent.name
            and receipt.get("network_executed") is False
            and receipt.get("collector_process_modified") is False
            and receipt.get("candidate_exclusion_performed") is False
            and receipt.get("scoring_performed") is False
            and receipt.get("sync_or_promotion_performed") is False
            and receipt.get("production_write_performed") is False
            and set(producers) == set(producer_paths)
            and all(
                receipt_is_current(producers[label], path)
                for label, path in producer_paths.items()
            )
            and all(receipt_is_current(item) for item in outputs.values())
        ):
            return None
        return {
            "status": "CURRENT",
            "created": False,
            "rollover_id": receipt["rollover_id"],
            "selected_strict_manifest": file_receipt(selected),
            "receipt": file_receipt(receipt_path),
            "network_executed": False,
            "sync_or_promotion_performed": False,
        }
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def write_specificity(selected: Path) -> Path:
    result = SPECIFICITY.audit(
        REGISTRY, selected_manifest_path=selected
    )
    require(
        result.get("status") == "PASS" and result.get("blockers") == [],
        "Current query-specificity audit did not pass",
    )
    atomic_json(QUERY_SPECIFICITY_CURRENT, result)
    return QUERY_SPECIFICITY_CURRENT


def ensure_snapshot(selected: Path) -> dict[str, Any]:
    return SNAPSHOT.ensure_snapshot_bundle(
        snapshot_root=SNAPSHOT_ROOT,
        strict_manifest_path=selected,
        records_path=RUN_DIR / "congress_advanced_records.ndjson",
        memberships_path=RUN_DIR / "congress_advanced_memberships.ndjson",
        exclusion_ledger_path=RUN_DIR / "exclusion_ledger.ndjson",
        exclusion_provenance_paths=(
            RUN_DIR
            / "candidate_membership_review"
            / "false_positive_exclusions.ndjson",
        ),
        cumulative_manifest_path=RUN_DIR / "manifest.json",
        registry_path=REGISTRY,
        review_ledger_path=(
            RUN_DIR
            / "candidate_membership_review"
            / "membership_review_ledger.ndjson"
        ),
        gap_ledger_path=RUN_DIR / "coverage_gap_ledger.ndjson",
    )


def refresh_presync() -> Path:
    args = SimpleNamespace(
        config=PRESYNC_CONFIG,
        out=PRESYNC_OUT,
        resume=True,
        resume_coverage=False,
        check_only=True,
        batch_size=250,
        stage_timeout_seconds=7200,
        python_executable=sys.executable,
        workspace_root=ROOT,
    )
    checkpoint = PRESYNC.orchestrate(args)
    require(
        checkpoint.get("staged_only") is True
        and checkpoint.get("production_or_mainframe_written") is False
        and checkpoint.get("production_commands_executed") is False,
        "Pre-sync check-only safety contract drift",
    )
    return PRESYNC_OUT / "checkpoint.json"


def refresh_augmentation(checkpoint_path: Path) -> tuple[Path, Path]:
    prefill_manifest = AUGMENT.manifest_from_pointer(
        AUGMENT.DEFAULT_PREFILL_POINTER,
        schema="legislation-partial-review-prefill-pointer-v1",
        label="partial-review prefill",
    )
    structured_manifest = AUGMENT.manifest_from_pointer(
        AUGMENT.DEFAULT_STRUCTURED_POINTER,
        schema="govinfo-billstatus-structured-relevance-pointer-v1",
        label="structured relevance",
    )
    out_dir, manifest, _ = AUGMENT.build_augmentation(
        prefill_manifest_path=prefill_manifest,
        structured_manifest_path=structured_manifest,
        registry_path=REGISTRY,
        out_root=AUGMENT.DEFAULT_OUT_ROOT,
        pointer_path=AUGMENT_POINTER,
        rollover_checkpoint_path=checkpoint_path,
    )
    coverage = manifest.get("coverage") or {}
    require(
        manifest.get("status") == "PASS_STAGED_EVIDENCE_ONLY"
        and coverage.get("every_membership_preserved_exactly_once") is True
        and coverage.get("source_memberships")
        == coverage.get("membership_evidence_rows")
        and coverage.get("unresolved_amendment_bytes_preserved") is True
        and manifest.get("network_executed") is False
        and manifest.get("candidate_exclusion_performed") is False
        and manifest.get("automatic_scoring_performed") is False
        and manifest.get("sync_or_promotion_performed") is False
        and manifest.get("production_write_performed") is False,
        "Structured augmentation preservation/authority contract drift",
    )
    return AUGMENT_POINTER, out_dir / "manifest.json"


def refresh_readiness() -> Path:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(READINESS.__file__).resolve()),
            "--discovery-run",
            str(RUN_DIR),
            "--out",
            str(READINESS_OUT),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    require(
        completed.returncode in {0, 2},
        "Downstream readiness refresh failed: "
        + (completed.stderr or completed.stdout).strip(),
    )
    path = READINESS_OUT / "downstream_readiness.json"
    value = read_object(path)
    require(
        value.get("sync_allowed") is False
        and value.get("hard_freeze") is True,
        "Downstream readiness unexpectedly authorized sync",
    )
    return path


def count_true_gates(path: Path) -> int:
    gates = read_object(path).get("gates") or {}
    return sum(value is True for value in gates.values())


def run_rollover() -> dict[str, Any]:
    selected = selected_strict_manifest()
    current = current_pointer_result(selected)
    if current is not None:
        return current
    prior = prior_manifest_path()
    previous = validate_timeout_manifest(prior)
    present = validate_timeout_manifest(selected)
    transition = capped_timeout_transition(prior, selected)
    changed = transition["changed"]
    require(
        changed == transition_needed(previous["receipt"], selected),
        "Timeout transition receipt comparison drift",
    )

    expected_queries = len(previous["manifest"].get("queries") or [])
    continuity = CONTINUITY.audit_continuity(
        prior,
        selected,
        PARTITION_AUDIT,
        expected_query_count=expected_queries,
    )
    require(
        continuity.get("status") == "PASS"
        and continuity.get("blocking_issue_count") == 0
        and continuity.get("manifest_continuity", {}).get(
            "semantic_record_drift_detected"
        )
        is False
        and continuity.get("current_timeout_receipt_binding", {}).get(
            "immutable_receipt_hash_valid"
        )
        is True,
        "Collector timeout continuity did not pass",
    )

    before_checkpoint = (
        read_object(PRESYNC_OUT / "checkpoint.json")
        if (PRESYNC_OUT / "checkpoint.json").is_file()
        else {"blockers": []}
    )
    readiness_path = READINESS_OUT / "downstream_readiness.json"
    before_gates = (
        count_true_gates(readiness_path)
        if readiness_path.is_file()
        else 0
    )

    snapshot = ensure_snapshot(selected)
    specificity_path = write_specificity(selected)
    checkpoint_path = refresh_presync()
    augmentation_pointer, augmentation_manifest = refresh_augmentation(
        checkpoint_path
    )
    readiness_path = refresh_readiness()

    checkpoint = read_object(checkpoint_path)
    augmentation = read_object(augmentation_manifest)
    augmentation_binding = (
        augmentation_manifest.parent / "semantic_rollover_binding.json"
    )
    readiness = read_object(readiness_path)
    require(
        checkpoint.get("coverage", {})
        .get("query_specificity_evidence", {})
        .get("status")
        == "PASS"
        and readiness.get("gates", {}).get(
            "query_specificity_current_and_passed"
        )
        is True,
        "Query-specificity currentness did not close",
    )

    address = {
        "schema": SCHEMA,
        "producers": {
            "rollover": file_receipt(Path(__file__)),
            "continuity": file_receipt(Path(CONTINUITY.__file__)),
            "snapshot": file_receipt(Path(SNAPSHOT.__file__)),
            "specificity": file_receipt(Path(SPECIFICITY.__file__)),
            "presync": file_receipt(Path(PRESYNC.__file__)),
            "augmentation": file_receipt(Path(AUGMENT.__file__)),
            "readiness": file_receipt(Path(READINESS.__file__)),
        },
        "prior_strict_manifest": previous["receipt"],
        "selected_strict_manifest": present["receipt"],
        "timeout_receipt": {
            "sha256": present["timeout"]["observed_sha256"],
            "consecutive_timeouts": present["timeout"][
                "consecutive_timeouts"
            ],
        },
        "partition_audit": file_receipt(PARTITION_AUDIT),
        "presync_config": file_receipt(PRESYNC_CONFIG),
    }
    rollover_id = stable_sha256(address)
    snapshot_root = ROLLOVER_ROOT / "snapshots"
    final_dir = snapshot_root / rollover_id
    temporary = snapshot_root / f".timeout-rollover.{os.getpid()}"
    require(not final_dir.exists(), f"Unexpected rollover collision: {final_dir}")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    continuity_path = temporary / "continuity.json"
    continuity_path.write_text(
        json.dumps(continuity, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    outputs = {
        "continuity": file_receipt(continuity_path),
        "billstatus_bundle_receipt": file_receipt(
            Path(snapshot["bundle_receipt"])
        ),
        "billstatus_snapshot_plan": file_receipt(
            Path(snapshot["snapshot_plan"])
        ),
        "query_specificity_current": file_receipt(specificity_path),
        "presync_checkpoint": file_receipt(checkpoint_path),
        "structured_augmentation_pointer": file_receipt(
            augmentation_pointer
        ),
        "structured_augmentation_manifest": file_receipt(
            augmentation_manifest
        ),
        "structured_augmentation_rollover_binding": file_receipt(
            augmentation_binding
        ),
        "downstream_readiness": file_receipt(readiness_path),
    }
    # The temporary continuity path becomes final with its directory.
    outputs["continuity"]["path"] = str(
        (final_dir / "continuity.json").resolve()
    )
    receipt = {
        "schema": SCHEMA,
        "rollover_id": rollover_id,
        "status": "PASS",
        "input_address": address,
        "prior_strict_manifest": previous["receipt"],
        "selected_strict_manifest": present["receipt"],
        "timeout_receipt_binding": present["timeout"],
        "continuity": {
            "status": continuity["status"],
            "blocking_issue_count": continuity["blocking_issue_count"],
            "difference_classification": continuity[
                "manifest_continuity"
            ]["difference_classification"],
            "query_count": continuity[
                "current_query_artifact_validation"
            ]["referenced_query_file_count"],
            "all_query_and_page_files_validate": continuity[
                "current_query_artifact_validation"
            ]["all_referenced_query_and_page_files_validate"],
        },
        "billstatus_bundle_id": snapshot["bundle_id"],
        "billstatus_bundle_created": snapshot["created"],
        "preserved_counts": {
            "selected_records": augmentation["input_counts"][
                "selected_records"
            ],
            "memberships": augmentation["coverage"][
                "source_memberships"
            ],
            "membership_evidence_rows": augmentation["coverage"][
                "membership_evidence_rows"
            ],
            "structured_candidates": augmentation["coverage"][
                "structured_candidates_preserved_by_receipt"
            ],
            "unresolved_amendments": augmentation["coverage"][
                "unresolved_amendments_preserved"
            ],
            "exclusion_provenance_receipts": augmentation["coverage"][
                "exclusion_provenance_receipts_preserved"
            ],
        },
        "blocker_change": {
            "before": len(before_checkpoint.get("blockers") or []),
            "after": len(checkpoint.get("blockers") or []),
        },
        "readiness_gate_change": {
            "before": before_gates,
            "after": count_true_gates(readiness_path),
            "total": len(readiness.get("gates") or {}),
        },
        "outputs": outputs,
        "collector_process_modified": False,
        "network_executed": False,
        "candidate_exclusion_performed": False,
        "scoring_performed": False,
        "sync_or_promotion_performed": False,
        "production_write_performed": False,
        "authoritative_for_discovery": False,
        "authoritative_for_semantic_exclusion": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
    }
    receipt_path = temporary / "rollover_receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, final_dir)
    receipt_path = final_dir / "rollover_receipt.json"
    pointer = {
        "schema": POINTER_SCHEMA,
        "rollover_id": rollover_id,
        "selected_strict_manifest": present["receipt"],
        "producer": file_receipt(Path(__file__)),
        "receipt": file_receipt(receipt_path),
        "network_executed": False,
        "sync_or_promotion_performed": False,
    }
    atomic_json(CURRENT_POINTER, pointer)
    return {
        "status": "PASS",
        "created": True,
        "rollover_id": rollover_id,
        "selected_strict_manifest": present["receipt"],
        "receipt": file_receipt(receipt_path),
        "blocker_change": receipt["blocker_change"],
        "readiness_gate_change": receipt["readiness_gate_change"],
        "preserved_counts": receipt["preserved_counts"],
        "network_executed": False,
        "sync_or_promotion_performed": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Retained for supervisor readability; execution is always safe.",
    )
    parser.add_argument(
        "--selected-only",
        action="store_true",
        help="Print the validated selected strict receipt without writes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = (
            selected_manifest_state()
            if args.selected_only
            else run_rollover()
        )
    except (
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        PRESYNC.PresyncError,
        SNAPSHOT.RefreshError,
        AUGMENT.ContractError,
    ) as exc:
        print(f"timeout rollover failed closed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

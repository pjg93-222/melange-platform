#!/usr/bin/env python3
"""Create or select immutable, input-addressed BILLSTATUS audit bundles."""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Sequence

import audit.audit_billstatus_snapshot_divergence as DIVERGENCE
import audit.audit_legislation_query_specificity as SPECIFICITY
from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "billstatus-snapshot-evidence-bundle-v1"
PLAN_NAME = "snapshot_plan.json"
RECEIPT_NAME = "bundle_receipt.json"
DIVERGENCE_SUBDIR = "divergence"
SPECIFICITY_NAME = "query_specificity_audit.json"


class RefreshError(ValueError):
    """Raised when an immutable bundle is missing, stale, or tampered."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RefreshError(message)


def receipt(path: Path) -> dict[str, Any]:
    return BILLSTATUS.file_receipt(path.resolve())


def input_address(
    *,
    strict_manifest_path: Path,
    records_path: Path,
    memberships_path: Path,
    exclusion_ledger_path: Path,
    exclusion_provenance_paths: Sequence[Path],
    cumulative_manifest_path: Path,
    registry_path: Path,
    review_ledger_path: Path,
    gap_ledger_path: Path,
) -> tuple[str, dict[str, Any]]:
    inputs = {
        "selected_strict_manifest": receipt(strict_manifest_path),
        "advanced_search_records": receipt(records_path),
        "advanced_search_memberships": receipt(memberships_path),
        "exclusion_ledger": receipt(exclusion_ledger_path),
        "exclusion_provenance": [
            receipt(path) for path in exclusion_provenance_paths
        ],
        "cumulative_manifest": receipt(cumulative_manifest_path),
        "query_registry": receipt(registry_path),
        "membership_review": receipt(review_ledger_path),
        "coverage_gap_ledger": receipt(gap_ledger_path),
        "producer_sources": {
            "snapshot_plan": receipt(Path(BILLSTATUS.__file__)),
            "divergence_audit": receipt(Path(DIVERGENCE.__file__)),
            "query_specificity_audit": receipt(Path(SPECIFICITY.__file__)),
        },
    }
    address_payload = {
        key: (
            [
                {"bytes": item["bytes"], "sha256": item["sha256"]}
                for item in value
            ]
            if isinstance(value, list)
            else (
                {
                    subkey: {
                        "bytes": subvalue["bytes"],
                        "sha256": subvalue["sha256"],
                    }
                    for subkey, subvalue in value.items()
                }
                if key == "producer_sources"
                else {
                    "bytes": value["bytes"],
                    "sha256": value["sha256"],
                }
            )
        )
        for key, value in inputs.items()
    }
    return BILLSTATUS.stable_json_sha256(address_payload), inputs


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON object required: {path}")
    return value


def _receipt_current(value: Any, path: Path) -> bool:
    return bool(
        isinstance(value, dict)
        and Path(str(value.get("path") or "")).resolve() == path.resolve()
        and value.get("bytes") == path.stat().st_size
        and value.get("sha256") == BILLSTATUS.sha256_file(path)
    )


def validate_bundle(
    bundle_dir: Path,
    *,
    expected_bundle_id: str,
    expected_inputs: dict[str, Any],
    strict_manifest_path: Path,
) -> dict[str, Any]:
    receipt_path = bundle_dir / RECEIPT_NAME
    plan_path = bundle_dir / PLAN_NAME
    divergence_path = (
        bundle_dir / DIVERGENCE_SUBDIR / "divergence_audit.json"
    )
    specificity_path = bundle_dir / SPECIFICITY_NAME
    require(receipt_path.is_file(), "immutable bundle receipt is missing")
    require(plan_path.is_file(), "immutable snapshot plan is missing")
    require(divergence_path.is_file(), "immutable divergence audit is missing")
    require(specificity_path.is_file(), "immutable specificity audit is missing")
    bundle = _read_object(receipt_path)
    require(bundle.get("schema") == SCHEMA, "bundle schema drift")
    require(
        bundle.get("bundle_id") == expected_bundle_id
        and bundle_dir.name == expected_bundle_id,
        "bundle content address drift",
    )
    require(bundle.get("inputs") == expected_inputs, "bundle input receipts drift")
    outputs = bundle.get("outputs") or {}
    require(
        _receipt_current(outputs.get("snapshot_plan"), plan_path),
        "bundle snapshot-plan receipt drift",
    )
    require(
        _receipt_current(outputs.get("divergence_audit"), divergence_path),
        "bundle divergence-audit receipt drift",
    )
    require(
        _receipt_current(outputs.get("query_specificity_audit"), specificity_path),
        "bundle query-specificity receipt drift",
    )
    plan = _read_object(plan_path)
    BILLSTATUS.validate_snapshot_plan(
        plan, expected_strict_manifest=strict_manifest_path
    )
    divergence = _read_object(divergence_path)
    require(
        divergence.get("schema") == DIVERGENCE.SCHEMA
        and divergence.get("snapshot_only") is True
        and divergence.get("network_executed") is False
        and divergence.get("candidate_exclusions_performed") is False
        and divergence.get("candidate_deletions_performed") is False
        and divergence.get("sync_or_promotion_performed") is False,
        "bundle divergence safety contract drift",
    )
    for label in ("proven_candidates", "unproven_candidates"):
        output = (divergence.get("outputs") or {}).get(label)
        ledger = Path(str((output or {}).get("path") or ""))
        require(
            ledger.is_file() and _receipt_current(output, ledger),
            f"bundle {label} receipt drift",
        )
    specificity = _read_object(specificity_path)
    strict_input = (specificity.get("inputs") or {}).get(
        "selected_strict_manifest"
    )
    registry_input = (specificity.get("inputs") or {}).get("registry")
    expected_registry = expected_inputs.get("query_registry")
    require(
        specificity.get("schema") == "legislation-query-specificity-audit-v1"
        and specificity.get("status") == "PASS"
        and specificity.get("quorum_used") is False
        and specificity.get("semantic_exclusions_performed") is False
        and isinstance(strict_input, dict)
        and strict_input.get("sha256")
        == expected_inputs["selected_strict_manifest"]["sha256"]
        and isinstance(registry_input, dict)
        and registry_input.get("sha256") == expected_registry["sha256"],
        "bundle query-specificity safety/input contract drift",
    )
    return {
        "bundle_id": expected_bundle_id,
        "bundle_dir": str(bundle_dir.resolve()),
        "bundle_receipt": str(receipt_path.resolve()),
        "snapshot_plan": str(plan_path.resolve()),
        "divergence_audit": str(divergence_path.resolve()),
        "query_specificity_audit": str(specificity_path.resolve()),
        "created": False,
        "network_executed": False,
        "sync_or_promotion_performed": False,
    }


def ensure_snapshot_bundle(
    *,
    snapshot_root: Path,
    strict_manifest_path: Path,
    records_path: Path,
    memberships_path: Path,
    exclusion_ledger_path: Path,
    exclusion_provenance_paths: Sequence[Path],
    cumulative_manifest_path: Path,
    registry_path: Path,
    review_ledger_path: Path,
    gap_ledger_path: Path,
    enforce_workspace_audits: bool = True,
) -> dict[str, Any]:
    root = snapshot_root.resolve()
    if enforce_workspace_audits:
        audit_root = (ROOT / "audits").resolve()
        require(
            root != audit_root
            and root.is_relative_to(audit_root),
            "snapshot root must be a child of workspace audits",
        )
    bundle_id, inputs = input_address(
        strict_manifest_path=strict_manifest_path,
        records_path=records_path,
        memberships_path=memberships_path,
        exclusion_ledger_path=exclusion_ledger_path,
        exclusion_provenance_paths=exclusion_provenance_paths,
        cumulative_manifest_path=cumulative_manifest_path,
        registry_path=registry_path,
        review_ledger_path=review_ledger_path,
        gap_ledger_path=gap_ledger_path,
    )
    bundle_dir = root / bundle_id
    if bundle_dir.exists():
        require(bundle_dir.is_dir(), "bundle address is not a directory")
        return validate_bundle(
            bundle_dir,
            expected_bundle_id=bundle_id,
            expected_inputs=inputs,
            strict_manifest_path=strict_manifest_path,
        )

    root.mkdir(parents=True, exist_ok=True)
    bundle_dir.mkdir()
    try:
        plan = BILLSTATUS.build_fetch_plan(
            records_path,
            exclusion_ledger_path,
            tuple(exclusion_provenance_paths),
            strict_manifest_path,
            cumulative_manifest_path,
            memberships_path,
        )
        BILLSTATUS.validate_snapshot_plan(
            plan, expected_strict_manifest=strict_manifest_path
        )
        plan_path = bundle_dir / PLAN_NAME
        with plan_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")

        divergence, proven, unproven = DIVERGENCE.build_audit(
            records_path=records_path,
            strict_manifest_path=strict_manifest_path,
            memberships_path=memberships_path,
            review_ledger_path=review_ledger_path,
            false_positive_exclusions_path=(
                exclusion_provenance_paths[0]
                if exclusion_provenance_paths
                else None
            ),
            gap_ledger_path=gap_ledger_path,
            aggregate_manifest_path=cumulative_manifest_path,
        )
        divergence_path = DIVERGENCE.write_audit(
            bundle_dir / DIVERGENCE_SUBDIR,
            divergence,
            proven,
            unproven,
        )
        specificity = SPECIFICITY.audit(
            registry_path,
            selected_manifest_path=strict_manifest_path,
        )
        require(
            specificity.get("status") == "PASS",
            "query-specificity audit blocked for selected strict manifest",
        )
        specificity_path = bundle_dir / SPECIFICITY_NAME
        with specificity_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(specificity, indent=2, ensure_ascii=False) + "\n"
            )
        bundle_receipt = {
            "schema": SCHEMA,
            "bundle_id": bundle_id,
            "immutable": True,
            "snapshot_only": True,
            "network_executed": False,
            "candidate_exclusions_performed": False,
            "candidate_deletions_performed": False,
            "sync_or_promotion_performed": False,
            "inputs": inputs,
            "outputs": {
                "snapshot_plan": receipt(plan_path),
                "divergence_audit": receipt(divergence_path),
                "query_specificity_audit": receipt(specificity_path),
            },
        }
        receipt_path = bundle_dir / RECEIPT_NAME
        with receipt_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(bundle_receipt, indent=2, ensure_ascii=False) + "\n"
            )
        result = validate_bundle(
            bundle_dir,
            expected_bundle_id=bundle_id,
            expected_inputs=inputs,
            strict_manifest_path=strict_manifest_path,
        )
        result["created"] = True
        return result
    except BaseException:
        # Half-built bundle would wedge this content address; the receipt is written last.
        shutil.rmtree(bundle_dir, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--strict-manifest", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--memberships", type=Path, required=True)
    parser.add_argument("--exclusion-ledger", type=Path, required=True)
    parser.add_argument(
        "--exclusion-provenance", type=Path, action="append", default=[]
    )
    parser.add_argument("--cumulative-manifest", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--review-ledger", type=Path, required=True)
    parser.add_argument("--gap-ledger", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = ensure_snapshot_bundle(
        snapshot_root=args.snapshot_root,
        strict_manifest_path=args.strict_manifest,
        records_path=args.records,
        memberships_path=args.memberships,
        exclusion_ledger_path=args.exclusion_ledger,
        exclusion_provenance_paths=tuple(args.exclusion_provenance),
        cumulative_manifest_path=args.cumulative_manifest,
        registry_path=args.registry,
        review_ledger_path=args.review_ledger,
        gap_ledger_path=args.gap_ledger,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

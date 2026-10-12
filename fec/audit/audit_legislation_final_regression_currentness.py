#!/usr/bin/env python3
"""Fail-closed currentness audit for the historical legislation regression run.

This audit does not rerun or rewrite the historical regression receipt.  It
physically rehashes that receipt, the current semantic-timeout rollover graph,
and the gate artifacts produced by the rollover.  It also records a complete
current implementation/test inventory.  Because the historical receipt did not
record the source/test paths and hashes used by its test run, this audit must
not describe the historical test results as current.

No network, collector, scoring, production, sync, or promotion operation is
performed by this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORICAL_RECEIPT = (
    ROOT / "audits" / "legfix5" / "final_regression_audit" / "receipt.json"
)
DEFAULT_HISTORICAL_REPORT = (
    ROOT
    / "audits"
    / "legfix5"
    / "final_regression_audit"
    / "FINAL_REGRESSION_AUDIT_2026-07-26.md"
)
DEFAULT_ROLLOVER_POINTER = (
    ROOT / "audits" / "legfix5" / "timeout_rollover" / "current.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "audits"
    / "legfix5"
    / "final_regression_currentness"
    / "currentness.json"
)
DEFAULT_POINTER = DEFAULT_OUTPUT.parent / "current.json"
DEFAULT_CURRENT_REGRESSION_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "final_regression_current"
    / "current.json"
)

SCHEMA = "legislation-final-regression-currentness-v1"
POINTER_SCHEMA = "legislation-final-regression-currentness-pointer-v1"
HISTORICAL_SCHEMA = "legislation-final-regression-audit-receipt-v1"
CURRENT_REGRESSION_SCHEMA = "legislation-final-regression-current-v1"
CURRENT_REGRESSION_POINTER_SCHEMA = (
    "legislation-final-regression-current-pointer-v1"
)
ROLLOVER_POINTER_SCHEMA = "legislation-timeout-semantic-rollover-pointer-v1"
ROLLOVER_RECEIPT_SCHEMA = "legislation-timeout-semantic-rollover-receipt-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CODE_SUFFIXES = {".py", ".mjs", ".cjs", ".ps1"}
SAFETY_FALSE_FIELDS = (
    "network_executed",
    "collector_process_modified",
    "candidate_exclusion_performed",
    "scoring_performed",
    "sync_or_promotion_performed",
    "production_write_performed",
)
ROLLOVER_GATE_MAP = {
    "strict_manifest_sha256": ("selected_strict_manifest", None),
    "query_specificity_sha256": (
        "outputs",
        "query_specificity_current",
    ),
    "billstatus_snapshot_plan_sha256": (
        "outputs",
        "billstatus_snapshot_plan",
    ),
    "structured_augmentation_pointer_sha256": (
        "outputs",
        "structured_augmentation_pointer",
    ),
    "presync_checkpoint_sha256": ("outputs", "presync_checkpoint"),
    "downstream_readiness_sha256": ("outputs", "downstream_readiness"),
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value.casefold()) is not None


def validate_file_receipt(
    value: Any,
    *,
    expected: Path | None = None,
) -> tuple[bool, str | None, Path | None]:
    if not isinstance(value, dict):
        return False, "receipt is not an object", None
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return False, "receipt path is absent", None
    path = Path(raw_path).resolve()
    if expected is not None and path != expected.resolve():
        return False, f"receipt path differs from expected path: {path}", path
    if not path.is_file():
        return False, f"receipt path is not a file: {path}", path
    expected_size = value.get("bytes", value.get("size"))
    if expected_size != path.stat().st_size:
        return False, f"receipt byte count is stale: {path}", path
    expected_sha = str(value.get("sha256") or "").casefold()
    if not _valid_sha(expected_sha) or expected_sha != sha256_file(path):
        return False, f"receipt SHA-256 is stale: {path}", path
    return True, None, path


def current_implementation_test_inventory(root: Path = ROOT) -> dict[str, Any]:
    """Hash every executable source/test file under ``fec``.

    The inventory is deliberately broader than the historical "focused" test
    description.  A broad current inventory is useful evidence, but it cannot
    recreate an unstored historical baseline.
    """

    fec_root = root / "fec"
    entries: list[dict[str, Any]] = []
    for path in sorted(fec_root.rglob("*"), key=lambda item: item.as_posix()):
        if (
            not path.is_file()
            or path.suffix.casefold() not in CODE_SUFFIXES
            or "__pycache__" in path.parts
        ):
            continue
        relative = path.relative_to(root).as_posix()
        is_test = relative.startswith("fec/tests/")
        entries.append(
            {
                "path": relative,
                "kind": "test" if is_test else "implementation",
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    implementations = sum(row["kind"] == "implementation" for row in entries)
    tests = sum(row["kind"] == "test" for row in entries)
    return {
        "scope": "all .py/.mjs/.cjs/.ps1 files recursively under fec",
        "entry_count": len(entries),
        "implementation_count": implementations,
        "test_count": tests,
        "inventory_sha256": stable_sha256(entries),
        "entries": entries,
    }


def historical_inventory_binding(receipt: dict[str, Any]) -> dict[str, Any]:
    """Describe whether the historical test claims bind a physical inventory."""

    candidates = (
        receipt.get("source_test_inventory"),
        receipt.get("implementation_test_inventory"),
        receipt.get("test_inventory"),
    )
    inventory = next((item for item in candidates if isinstance(item, dict)), None)
    if inventory is None:
        return {
            "bound": False,
            "reason": (
                "historical receipt has counts but no source/test path-and-hash "
                "inventory baseline"
            ),
        }
    digest = inventory.get("inventory_sha256")
    entries = inventory.get("entries")
    bound = bool(
        _valid_sha(digest)
        and isinstance(entries, list)
        and entries
        and stable_sha256(entries) == str(digest).casefold()
    )
    return {
        "bound": bound,
        "reason": (
            None
            if bound
            else "historical inventory object is incomplete or not self-consistent"
        ),
        "inventory_sha256": digest,
        "entry_count": len(entries) if isinstance(entries, list) else None,
    }


def validate_current_regression(
    pointer_path: Path,
    current_inventory: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    """Validate a superseding, source-inventory-bound full regression run."""

    pointer_path = pointer_path.resolve()
    if not pointer_path.is_file():
        return {
            "present": False,
            "valid": False,
            "reason": "current full-regression pointer is absent",
        }
    try:
        pointer = read_object(pointer_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"current full-regression pointer is unreadable: {exc}")
        return {"present": True, "valid": False, "reason": str(exc)}

    local_errors: list[str] = []
    if pointer.get("schema") != CURRENT_REGRESSION_POINTER_SCHEMA:
        local_errors.append("pointer schema is invalid")
    for field in (
        "network_executed",
        "scoring_performed",
        "production_write_performed",
        "sync_or_promotion_performed",
    ):
        if pointer.get(field) is not False:
            local_errors.append(f"pointer safety field is not false: {field}")

    runner_ok, runner_issue, runner_path = validate_file_receipt(
        pointer.get("runner")
    )
    receipt_ok, receipt_issue, receipt_path = validate_file_receipt(
        pointer.get("receipt")
    )
    if runner_issue:
        local_errors.append(f"runner: {runner_issue}")
    if receipt_issue:
        local_errors.append(f"receipt: {receipt_issue}")
    if not receipt_ok or receipt_path is None:
        errors.extend(
            f"current full regression: {issue}" for issue in local_errors
        )
        return {
            "present": True,
            "valid": False,
            "pointer": file_receipt(pointer_path),
            "reason": "; ".join(local_errors),
        }

    try:
        receipt = read_object(receipt_path)
    except (ValueError, json.JSONDecodeError) as exc:
        local_errors.append(f"receipt is unreadable: {exc}")
        receipt = {}

    if receipt.get("schema") != CURRENT_REGRESSION_SCHEMA:
        local_errors.append("receipt schema is invalid")
    if receipt.get("run_id") != pointer.get("run_id"):
        local_errors.append("pointer and receipt run IDs differ")
    if receipt.get("status") not in {
        "PASS_CURRENT",
        "BLOCKED_ACCEPTANCE_GATES_NO_IMPLEMENTATION_REGRESSIONS",
    }:
        local_errors.append("receipt status does not prove zero regressions")
    if receipt.get("actual_implementation_regression_count") != 0:
        local_errors.append("receipt reports implementation regressions")
    if receipt.get("source_test_inventory_stable") is not True:
        local_errors.append("source/test inventory changed during the run")

    before = receipt.get("source_test_inventory_before") or {}
    after = receipt.get("source_test_inventory_after") or {}
    current_entries = [
        {
            "path": entry.get("path"),
            "bytes": entry.get("bytes"),
            "sha256": entry.get("sha256"),
        }
        for entry in current_inventory.get("entries") or []
    ]
    current_digest = stable_sha256(current_entries)
    recorded_digest = before.get("inventory_sha256")
    if (
        recorded_digest != current_digest
        or after.get("inventory_sha256") != current_digest
        or before.get("entries") != current_entries
        or after.get("entries") != current_entries
        or pointer.get("source_test_inventory_sha256") != current_digest
        or (receipt.get("input_address") or {}).get(
            "implementation_test_inventory_sha256"
        )
        != current_digest
    ):
        local_errors.append("current source/test inventory is not receipt-bound")

    address_runner = (receipt.get("input_address") or {}).get("runner")
    if (
        not runner_ok
        or runner_path is None
        or address_runner != pointer.get("runner")
    ):
        local_errors.append("runner receipt is not consistently bound")

    compilation = receipt.get("compilation") or {}
    python_suite = receipt.get("python_regression_suite") or {}
    node_suite = receipt.get("node_regression_suite") or {}
    if compilation.get("failed") != 0:
        local_errors.append("Python compilation failures are present")
    if python_suite.get("implementation_regression_failures") not in ([], None):
        local_errors.append("Python implementation regression failures are present")
    if node_suite.get("exit_code") != 0:
        local_errors.append("Node regression suite did not pass")

    authority = receipt.get("authority") or {}
    if (
        authority.get("primary_discovery_instrument")
        != "Congress.gov Advanced Search - Legislation"
        or authority.get("quorum_used") is not False
        or authority.get("test_currentness_authority") is not True
    ):
        local_errors.append("current regression authority contract is invalid")
    safety = receipt.get("safety") or {}
    for field in (
        "network_executed",
        "collector_process_modified",
        "candidate_exclusion_performed",
        "scoring_performed",
        "production_write_performed",
        "sync_or_promotion_performed",
    ):
        if safety.get(field) is not False:
            local_errors.append(f"receipt safety field is not false: {field}")

    errors.extend(f"current full regression: {issue}" for issue in local_errors)
    return {
        "present": True,
        "valid": not local_errors,
        "pointer": file_receipt(pointer_path),
        "runner": file_receipt(runner_path) if runner_ok and runner_path else None,
        "receipt": file_receipt(receipt_path),
        "run_id": receipt.get("run_id"),
        "status": receipt.get("status"),
        "source_test_inventory_sha256": current_digest,
        "source_test_inventory_stable": receipt.get(
            "source_test_inventory_stable"
        ),
        "actual_implementation_regression_count": receipt.get(
            "actual_implementation_regression_count"
        ),
        "explicit_acceptance_blocker_failure_count": receipt.get(
            "explicit_acceptance_blocker_failure_count"
        ),
        "errors": local_errors,
    }


def validate_historical(
    receipt_path: Path,
    report_path: Path,
    errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        receipt = read_object(receipt_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"historical receipt is unreadable: {exc}")
        return {}, {
            "receipt": None,
            "report": None,
            "structural_valid": False,
            "inventory_binding": {"bound": False, "reason": str(exc)},
        }

    structural = True
    if receipt.get("schema") != HISTORICAL_SCHEMA:
        errors.append("historical receipt schema is invalid")
        structural = False
    gate_hashes = receipt.get("gate_hashes")
    if not isinstance(gate_hashes, dict) or not gate_hashes:
        errors.append("historical receipt gate hashes are absent")
        structural = False
    elif any(not _valid_sha(value) for value in gate_hashes.values()):
        errors.append("historical receipt contains an invalid gate SHA-256")
        structural = False
    authority = receipt.get("authority")
    if not isinstance(authority, dict):
        errors.append("historical authority receipt is absent")
        structural = False
    else:
        for key in (
            "quorum_used",
            "scoring_authority",
            "scoring_input_allowed",
            "sync_or_promotion_performed",
            "production_or_mainframe_written",
        ):
            if authority.get(key) is not False:
                errors.append(f"historical authority field is not false: {key}")
                structural = False
    if not report_path.is_file():
        errors.append("historical final regression report is absent")
        structural = False

    return receipt, {
        "receipt": file_receipt(receipt_path),
        "report": file_receipt(report_path) if report_path.is_file() else None,
        "schema": receipt.get("schema"),
        "recorded_status": receipt.get("status"),
        "recorded_test_summary": {
            "compilation": receipt.get("compilation"),
            "python_regression_suite": receipt.get("python_regression_suite"),
            "node_regression_suite": receipt.get("node_regression_suite"),
        },
        "recorded_gate_hashes": gate_hashes,
        "structural_valid": structural,
        "inventory_binding": historical_inventory_binding(receipt),
    }


def _validate_receipts(
    rows: Iterable[tuple[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for label, row in rows:
        passed, issue, path = validate_file_receipt(row)
        evidence[label] = {
            "valid": passed,
            "receipt": row,
            "observed": file_receipt(path) if passed and path is not None else None,
        }
        if issue:
            errors.append(f"{label}: {issue}")
    return evidence


def validate_rollover(
    pointer_path: Path,
    historical: dict[str, Any],
    errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        pointer = read_object(pointer_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"semantic-rollover pointer is unreadable: {exc}")
        return {}, {"pointer": None, "physical_chain_valid": False}

    if pointer.get("schema") != ROLLOVER_POINTER_SCHEMA:
        errors.append("semantic-rollover pointer schema is invalid")
    if pointer.get("network_executed") is not False:
        errors.append("semantic-rollover pointer does not prove zero network")
    if pointer.get("sync_or_promotion_performed") is not False:
        errors.append("semantic-rollover pointer does not prove zero sync/promotion")

    pointer_evidence = _validate_receipts(
        (
            ("rollover producer", pointer.get("producer")),
            ("selected strict manifest", pointer.get("selected_strict_manifest")),
            ("rollover receipt", pointer.get("receipt")),
        ),
        errors,
    )
    receipt_path = None
    passed, _, receipt_path = validate_file_receipt(pointer.get("receipt"))
    if not passed or receipt_path is None:
        return {}, {
            "pointer": file_receipt(pointer_path),
            "pointer_receipts": pointer_evidence,
            "physical_chain_valid": False,
        }

    try:
        rollover = read_object(receipt_path)
    except (ValueError, json.JSONDecodeError) as exc:
        errors.append(f"semantic-rollover receipt is unreadable: {exc}")
        return {}, {
            "pointer": file_receipt(pointer_path),
            "pointer_receipts": pointer_evidence,
            "physical_chain_valid": False,
        }

    if rollover.get("schema") != ROLLOVER_RECEIPT_SCHEMA:
        errors.append("semantic-rollover receipt schema is invalid")
    if rollover.get("status") != "PASS":
        errors.append("semantic-rollover receipt status is not PASS")
    if (
        rollover.get("rollover_id") != pointer.get("rollover_id")
        or receipt_path.parent.name != pointer.get("rollover_id")
    ):
        errors.append("semantic-rollover identity chain is inconsistent")
    for field in SAFETY_FALSE_FIELDS:
        if rollover.get(field) is not False:
            errors.append(f"semantic-rollover safety field is not false: {field}")

    input_address = rollover.get("input_address")
    outputs = rollover.get("outputs")
    if not isinstance(input_address, dict):
        input_address = {}
        errors.append("semantic-rollover input address is absent")
    if not isinstance(outputs, dict):
        outputs = {}
        errors.append("semantic-rollover outputs are absent")

    input_rows: list[tuple[str, Any]] = [
        ("prior strict manifest", input_address.get("prior_strict_manifest")),
        ("rollover selected strict manifest", input_address.get("selected_strict_manifest")),
        ("partition audit", input_address.get("partition_audit")),
        ("presync config", input_address.get("presync_config")),
    ]
    producers = input_address.get("producers")
    if isinstance(producers, dict):
        input_rows.extend(
            (f"producer {label}", value) for label, value in sorted(producers.items())
        )
    else:
        errors.append("semantic-rollover producer inventory is absent")
    input_evidence = _validate_receipts(input_rows, errors)
    output_evidence = _validate_receipts(
        ((f"output {label}", value) for label, value in sorted(outputs.items())),
        errors,
    )

    selected = input_address.get("selected_strict_manifest")
    if selected != pointer.get("selected_strict_manifest"):
        errors.append("pointer and receipt select different strict manifests")
    historical_strict = (
        (historical.get("gate_hashes") or {}).get("strict_manifest_sha256")
    )
    prior_strict = input_address.get("prior_strict_manifest") or {}
    anchor_valid = bool(
        _valid_sha(historical_strict)
        and str(prior_strict.get("sha256") or "").casefold()
        == str(historical_strict).casefold()
    )
    if not anchor_valid:
        errors.append(
            "historical strict-manifest hash does not anchor the rollover prior state"
        )

    selected_path = Path(str((selected or {}).get("path") or "")).resolve()
    timeout = {}
    timeout_receipt_evidence: dict[str, Any] | None = None
    if selected_path.is_file():
        try:
            manifest = read_object(selected_path)
            timeout = manifest.get("automatic_verification_timeout") or {}
            raw_timeout_path = timeout.get("receipt_file")
            if isinstance(raw_timeout_path, str) and raw_timeout_path:
                timeout_path = (selected_path.parent / raw_timeout_path).resolve()
                observed = file_receipt(timeout_path) if timeout_path.is_file() else None
                expected_sha = str(timeout.get("receipt_sha256") or "").casefold()
                rollover_timeout = input_address.get("timeout_receipt") or {}
                timeout_valid = bool(
                    observed
                    and observed["sha256"] == expected_sha
                    and expected_sha
                    == str(rollover_timeout.get("sha256") or "").casefold()
                    and timeout.get("interaction_performed") is False
                    and timeout.get("captcha_bypass_attempted") is False
                    and timeout.get("collection_stopped_without_result_acceptance")
                    is True
                )
                timeout_receipt_evidence = {
                    "valid": timeout_valid,
                    "manifest_binding": timeout,
                    "observed": observed,
                }
                if not timeout_valid:
                    errors.append("current immutable timeout receipt binding is invalid")
            else:
                errors.append("selected manifest has no timeout receipt path")
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"selected strict manifest is unreadable: {exc}")
    else:
        errors.append("selected strict manifest is absent")

    return rollover, {
        "pointer": file_receipt(pointer_path),
        "pointer_receipts": pointer_evidence,
        "receipt": file_receipt(receipt_path),
        "input_receipts": input_evidence,
        "output_receipts": output_evidence,
        "historical_strict_anchor_valid": anchor_valid,
        "timeout_receipt": timeout_receipt_evidence,
        "physical_chain_valid": not errors,
    }


def validate_current_gates(
    historical: dict[str, Any],
    rollover: dict[str, Any],
    errors: list[str],
) -> tuple[dict[str, Any], bool]:
    outputs = rollover.get("outputs") if isinstance(rollover, dict) else {}
    inputs = rollover.get("input_address") if isinstance(rollover, dict) else {}
    outputs = outputs if isinstance(outputs, dict) else {}
    inputs = inputs if isinstance(inputs, dict) else {}
    historical_hashes = historical.get("gate_hashes") or {}
    comparison: dict[str, Any] = {}

    for historical_key, address in ROLLOVER_GATE_MAP.items():
        if address[0] == "selected_strict_manifest":
            current = inputs.get("selected_strict_manifest")
        else:
            current = outputs.get(address[1])
        comparison[historical_key] = {
            "historical_sha256": historical_hashes.get(historical_key),
            "current_receipt": current,
            "changed": (
                isinstance(current, dict)
                and historical_hashes.get(historical_key) != current.get("sha256")
            ),
            "semantic_rollover_bound": isinstance(current, dict),
        }
        if not isinstance(current, dict):
            errors.append(f"rollover does not bind current gate: {historical_key}")

    objects: dict[str, dict[str, Any]] = {}
    for label in (
        "query_specificity_current",
        "presync_checkpoint",
        "downstream_readiness",
    ):
        row = outputs.get(label)
        if not isinstance(row, dict):
            continue
        path = Path(str(row.get("path") or "")).resolve()
        try:
            objects[label] = read_object(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"current gate object is unreadable ({label}): {exc}")

    selected = inputs.get("selected_strict_manifest") or {}
    selected_sha = selected.get("sha256")
    specificity = objects.get("query_specificity_current") or {}
    specificity_selected = (
        (specificity.get("inputs") or {}).get("selected_strict_manifest") or {}
    )
    specificity_valid = bool(
        specificity.get("schema") == "legislation-query-specificity-audit-v1"
        and specificity.get("status") == "PASS"
        and specificity.get("primary_discovery")
        == "Congress.gov Advanced Search - Legislation"
        and specificity.get("quorum_used") is False
        and (specificity.get("counts") or {}).get("generic_bare_act_category_terms")
        == 0
        and specificity_selected.get("sha256") == selected_sha
    )
    if not specificity_valid:
        errors.append("current query-specificity gate is not semantically valid")

    presync = objects.get("presync_checkpoint") or {}
    presync_coverage = presync.get("coverage") or {}
    presync_valid = bool(
        presync.get("schema") == "legislation-presync-audit-checkpoint-v1"
        and presync.get("status") == "BLOCKED"
        and presync.get("staged_only") is True
        and presync.get("production_or_mainframe_written") is False
        and (presync_coverage.get("strict_manifest") or {}).get("sha256")
        == selected_sha
        and isinstance(presync.get("blockers"), list)
        and presync.get("blockers")
    )
    if not presync_valid:
        errors.append("current pre-sync checkpoint is not safely bound and blocked")

    readiness = objects.get("downstream_readiness") or {}
    readiness_coverage = readiness.get("coverage") or {}
    readiness_valid = bool(
        readiness.get("schema") == "legislation-downstream-readiness-v4"
        and readiness.get("sync_allowed") is False
        and readiness.get("hard_freeze") is True
        and readiness.get("production_is_read_only") is True
        and (
            (readiness_coverage.get("selected_discovery_manifest") or {}).get(
                "sha256"
            )
            == selected_sha
        )
    )
    if not readiness_valid:
        errors.append("current downstream-readiness gate is not safely frozen")

    semantic_valid = specificity_valid and presync_valid and readiness_valid
    return {
        "hash_rollover": comparison,
        "query_specificity": {
            "semantic_valid": specificity_valid,
            "status": specificity.get("status"),
            "counts": specificity.get("counts"),
        },
        "presync": {
            "semantic_valid": presync_valid,
            "status": presync.get("status"),
            "blocker_count": len(presync.get("blockers") or []),
            "strict_query_count": presync_coverage.get("strict_query_count"),
        },
        "downstream_readiness": {
            "semantic_valid": readiness_valid,
            "sync_allowed": readiness.get("sync_allowed"),
            "passed_gate_count": sum(
                value is True for value in (readiness.get("gates") or {}).values()
            ),
            "gate_count": len(readiness.get("gates") or {}),
        },
    }, semantic_valid


def build_audit(
    *,
    historical_receipt_path: Path = DEFAULT_HISTORICAL_RECEIPT,
    historical_report_path: Path = DEFAULT_HISTORICAL_REPORT,
    rollover_pointer_path: Path = DEFAULT_ROLLOVER_POINTER,
    current_regression_pointer_path: Path = DEFAULT_CURRENT_REGRESSION_POINTER,
    inventory_root: Path = ROOT,
) -> dict[str, Any]:
    errors: list[str] = []
    historical, historical_evidence = validate_historical(
        historical_receipt_path.resolve(),
        historical_report_path.resolve(),
        errors,
    )
    errors_before_rollover = len(errors)
    rollover, rollover_evidence = validate_rollover(
        rollover_pointer_path.resolve(),
        historical,
        errors,
    )
    rollover_chain_valid = (
        len(errors) == errors_before_rollover
        and rollover_evidence.get("physical_chain_valid") is True
    )
    errors_before_gates = len(errors)
    current_gates, gates_semantically_valid = validate_current_gates(
        historical,
        rollover,
        errors,
    )
    gates_semantically_valid = (
        gates_semantically_valid and len(errors) == errors_before_gates
    )
    inventory = current_implementation_test_inventory(inventory_root.resolve())
    current_regression = validate_current_regression(
        current_regression_pointer_path.resolve(),
        inventory,
        errors,
    )
    current_regression_valid = current_regression.get("valid") is True
    binding = historical_evidence.get("inventory_binding") or {}
    historical_inventory_bound = binding.get("bound") is True
    semantic_rollover_valid = bool(
        historical_evidence.get("structural_valid")
        and rollover_chain_valid
        and gates_semantically_valid
    )

    blockers: list[dict[str, Any]] = []
    if not semantic_rollover_valid:
        blockers.append(
            {
                "code": "CURRENT_SEMANTIC_GATE_CHAIN_INVALID",
                "details": list(errors),
            }
        )
    if not historical_inventory_bound and not current_regression_valid:
        blockers.append(
            {
                "code": "BLOCKED_UNBOUND_HISTORICAL_TEST_INVENTORY",
                "reason": binding.get("reason"),
                "effect": (
                    "historical compilation/test results remain historical facts "
                    "and are not certified as current"
                ),
            }
        )

    if not semantic_rollover_valid:
        status = "FAIL_CLOSED_CURRENTNESS_VALIDATION"
    elif not historical_inventory_bound and not current_regression_valid:
        status = "BLOCKED_UNBOUND_HISTORICAL_TEST_INVENTORY"
    else:
        status = "CURRENT"

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "historical_receipt": historical_evidence,
        "semantic_rollover": {
            **rollover_evidence,
            "semantically_validated": semantic_rollover_valid,
            "historical_receipt_rewritten": False,
        },
        "current_gate_artifacts": current_gates,
        "current_full_regression": current_regression,
        "current_implementation_test_inventory": {
            **inventory,
            "historical_baseline_bound": historical_inventory_bound,
            "historical_tests_certified_current": bool(
                historical_inventory_bound and semantic_rollover_valid
            ),
            "current_tests_certified_current": current_regression_valid,
            "conclusion": (
                "CURRENT_REGRESSION_BOUND"
                if current_regression_valid
                else (
                    "HISTORICAL_BASELINE_BOUND"
                    if historical_inventory_bound
                    else "UNBOUND_HISTORICAL_BASELINE"
                )
            ),
        },
        "semantic_chain": {
            "historical_receipt_sha256": (
                historical_evidence.get("receipt") or {}
            ).get("sha256"),
            "historical_strict_manifest_sha256": (
                historical.get("gate_hashes") or {}
            ).get("strict_manifest_sha256"),
            "rollover_prior_strict_manifest_sha256": (
                (rollover.get("input_address") or {}).get(
                    "prior_strict_manifest"
                )
                or {}
            ).get("sha256"),
            "rollover_current_strict_manifest_sha256": (
                (rollover.get("input_address") or {}).get(
                    "selected_strict_manifest"
                )
                or {}
            ).get("sha256"),
            "rollover_receipt_sha256": (
                rollover_evidence.get("receipt") or {}
            ).get("sha256"),
            "semantic_gate_rollover_valid": semantic_rollover_valid,
            "historical_test_inventory_currentness_valid": (
                historical_inventory_bound
            ),
            "current_full_regression_valid": current_regression_valid,
            "current_full_regression_receipt_sha256": (
                current_regression.get("receipt") or {}
            ).get("sha256"),
        },
        "blockers": blockers,
        "validation_errors": errors,
        "authority": {
            "primary_discovery_instrument": (
                "Congress.gov Advanced Search - Legislation"
            ),
            "quorum_used": False,
            "historical_test_results_currentness_authority": (
                historical_inventory_bound and semantic_rollover_valid
            ),
            "current_test_results_currentness_authority": (
                current_regression_valid
            ),
            "semantic_gate_rollover_observation_authority": (
                semantic_rollover_valid
            ),
            "membership_disposition_authority": False,
            "scoring_authority": False,
            "sync_or_promotion_authority": False,
        },
        "safety": {
            "network_executed": False,
            "collector_process_modified": False,
            "candidate_exclusion_performed": False,
            "scoring_performed": False,
            "production_write_performed": False,
            "sync_or_promotion_performed": False,
        },
    }


def write_audit(
    audit: dict[str, Any],
    *,
    output_path: Path = DEFAULT_OUTPUT,
    pointer_path: Path = DEFAULT_POINTER,
) -> dict[str, Any]:
    output_path = output_path.resolve()
    pointer_path = pointer_path.resolve()
    atomic_json(output_path, audit)
    pointer = {
        "schema": POINTER_SCHEMA,
        "status": audit["status"],
        "producer": file_receipt(Path(__file__).resolve()),
        "audit": file_receipt(output_path),
        "historical_receipt": audit["historical_receipt"]["receipt"],
        "semantic_gate_rollover_valid": audit["semantic_chain"][
            "semantic_gate_rollover_valid"
        ],
        "historical_tests_certified_current": audit[
            "current_implementation_test_inventory"
        ]["historical_tests_certified_current"],
        "current_tests_certified_current": audit[
            "current_implementation_test_inventory"
        ]["current_tests_certified_current"],
        "current_full_regression": audit["current_full_regression"].get(
            "receipt"
        ),
        "network_executed": False,
        "collector_process_modified": False,
        "scoring_performed": False,
        "production_write_performed": False,
        "sync_or_promotion_performed": False,
    }
    atomic_json(pointer_path, pointer)
    return pointer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--historical-receipt",
        type=Path,
        default=DEFAULT_HISTORICAL_RECEIPT,
    )
    parser.add_argument(
        "--historical-report",
        type=Path,
        default=DEFAULT_HISTORICAL_REPORT,
    )
    parser.add_argument(
        "--rollover-pointer",
        type=Path,
        default=DEFAULT_ROLLOVER_POINTER,
    )
    parser.add_argument(
        "--current-regression-pointer",
        type=Path,
        default=DEFAULT_CURRENT_REGRESSION_POINTER,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pointer", type=Path, default=DEFAULT_POINTER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit = build_audit(
        historical_receipt_path=args.historical_receipt,
        historical_report_path=args.historical_report,
        rollover_pointer_path=args.rollover_pointer,
        current_regression_pointer_path=args.current_regression_pointer,
    )
    pointer = write_audit(
        audit,
        output_path=args.output,
        pointer_path=args.pointer,
    )
    print(
        json.dumps(
            {
                "status": audit["status"],
                "audit": pointer["audit"],
                "pointer": file_receipt(args.pointer.resolve()),
                "semantic_gate_rollover_valid": pointer[
                    "semantic_gate_rollover_valid"
                ],
                "historical_tests_certified_current": pointer[
                    "historical_tests_certified_current"
                ],
                "current_tests_certified_current": pointer[
                    "current_tests_certified_current"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if audit["semantic_chain"]["semantic_gate_rollover_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

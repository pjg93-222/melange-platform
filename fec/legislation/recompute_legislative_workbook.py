# -*- coding: utf-8 -*-
"""Fail-closed staged legislative workbook recomputation.

Python validates the accepted-workbook generation, selected role policy,
shadow step ledger, and a complete numeric matrix plan.  It never opens or
edits XLSX bytes.  All workbook reads, writes, evaluation, formula scanning,
rendering, and export are delegated to the CommonJS
``recompute_legislative_workbook_artifact.cjs`` helper using
``@oai/artifact-tool``.

The current live workbook's efficiency columns are not fully identified by
the audit.  Consequently this adapter requires an explicit structured
efficiency-definition approval in the accepted generation.  Absence or
ambiguity blocks before the helper is invoked.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from paths import ROOT as MB_ROOT


CODE_ROOT = Path(__file__).resolve().parents[2]
HELPER = CODE_ROOT / "fec" / "recompute_legislative_workbook_artifact.cjs"
RECEIPT_SCHEMA = "legislation-workbook-recompute-receipt-v1"
ACCEPTED_RECEIPT_SCHEMA = "accepted-legislation-workbook-receipt-v1"
PLAN_SCHEMA = "legislation-workbook-numeric-write-plan-v1"
STEP_LEDGER_SCHEMA = "legislation-shadow-step-receipts-v1"
RECEIPT_NAME = "legislation_workbook.receipt.json"
ACCEPTED_RECEIPT_NAME = (
    Path("intermediate") / "accepted_legislation_workbook.receipt.json"
)
STEP_LEDGER_NAME = Path("intermediate") / "shadow_step_receipts.json"
WORKBOOK_NAME = "Moneyball Main Version - Blended.xlsx"
SNAPSHOT_NAME = "data/engine/pillar_inputs_snapshot.json"
EXPECTED_PILLAR_MEMBER_COUNT = 533
HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
EXPECTED_DECAY = {116: 0.16, 117: 0.33, 118: 0.66, 119: 1.0}
ROLE_APPROVAL_SCHEMA = "legislation-role-method-profile-approval-v1"
ROLE_METHOD_PROFILE_ID = "consumer_specific_legacy_preservation"
ROLE_METHOD_PROFILE = {
    "archive_leg": {
        "method": "fixed_role_weights",
        "weights": {
            "sponsor": 3.0,
            "original_cosponsor": 2.0,
            "cosponsor": 1.0,
        },
    },
    "workbook_grid": {
        "method": "fixed_role_weights",
        "weights": {
            "sponsor": 4.0,
            "original_cosponsor": 2.0,
            "cosponsor": 1.0,
        },
    },
    "stance": {
        "method": "fixed_role_weights",
        "weights": {
            "sponsor": 1.0,
            "original_cosponsor": 1.0,
            "cosponsor": 1.0,
        },
    },
    "say_do": {
        "method": "delivery_multiplier",
        "weights": {
            "sponsor": 1.0,
            "original_cosponsor": 0.5,
            "cosponsor": 0.25,
        },
    },
    "base_network": {
        "method": "cosponsor_count_normalized_edge",
        "original_cosponsor_numerator": 1.0,
        "cosponsor_numerator": 0.5,
        "denominator": "sqrt(current_cosponsor_count)",
    },
}

ROLE_POLICIES = frozenset(
    {
        "archive_legacy_3_2_1",
        "workbook_legacy_4_2_1",
        "stance_legacy_1_1_1",
    }
)
CATEGORIES = (
    "Examination & Supervision",
    "CU Capital Requirements",
    "NCUA Budget & Insurance",
    "Interchange",
    "Payments",
    "Technology & Emerging",
    "Fraud Prevention",
    "Data Privacy & Cyber",
    "Charter Modernization",
    "Defense CU Issues",
    "Field of Membership",
    "CDFI Fund",
    "CFPB",
    "Credit Card Mandates",
    "TCPA",
    "Member Business Lending",
    "Mortgage & Housing",
    "CU Tax Status",
)
BLOCK_RANGES = {
    category: block
    for category, block in zip(
        CATEGORIES,
        (
            "C3:J535",
            "K3:R535",
            "S3:Z535",
            "AA3:AH535",
            "AI3:AP535",
            "AQ3:AX535",
            "AY3:BF535",
            "BG3:BN535",
            "BO3:BV535",
            "BW3:CD535",
            "CE3:CL535",
            "CM3:CT535",
            "CU3:DB535",
            "DC3:DJ535",
            "DK3:DR535",
            "DS3:DZ535",
            "EA3:EH535",
            "EW3:FD535",
        ),
    )
}
SUBLAYER_CATEGORY_COLUMNS = tuple(
    [chr(ord("C") + offset) for offset in range(13)]
    + ["Q", "R", "S", "T", "U"]
)
SUBLAYER_EFFICIENCY_RANGE = "AG4:AW536"
EFFICIENCY_COLUMNS = (
    "AG",
    "AH",
    "AI",
    "AJ",
    "AK",
    "AL",
    "AM",
    "AN",
    "AO",
    "AP",
    "AQ",
    "AR",
    "AS",
    "AT",
    "AU",
    "AV",
    "AW",
)
PRIOR_STEPS = (
    "materialize_archive_and_legislative_workbook",
    "materialize_rollcall_aggregate",
    "materialize_base_network_and_prebehavior_bipartisan",
    "recompute_behavior_panel",
    "recompute_behavioral_dispositions",
    "recompute_cooperation",
    "recompute_receptivity",
    "recompute_augmented_network",
    "recompute_category_stance",
    "recompute_say_do",
)


class WorkbookRecomputeError(RuntimeError):
    """A generation, method, matrix, runtime, or verification gate failed."""


def required_nonbill_numeric_policy(
    manual_evidence_sha256: str,
) -> dict[str, Any]:
    """Return the mandatory treatment of audited non-standard raw columns."""
    sha = str(manual_evidence_sha256 or "").casefold()
    if not HEX_RE.fullmatch(sha):
        raise WorkbookRecomputeError(
            "numeric plan manual-evidence SHA-256 is invalid"
        )
    return {
        "cfpb_sign_on_letters": {
            "raw_ranges": [
                "Legislative Score - Raw!CU3:CU535",
                "Legislative Score - Raw!CW3:CW535",
            ],
            "action": "recompute_from_hash_bound_manual_bill_evidence",
            "manual_bill_evidence_sha256": sha,
            "congress_decay": {
                str(key): value for key, value in EXPECTED_DECAY.items()
            },
            "unit": "decay_weighted_sign_on_letter",
            "bill_relationship_rows_prohibited": True,
        },
        "cfpb_dead_zero_siblings": {
            "raw_ranges": [
                "Legislative Score - Raw!CV3:CV535",
                "Legislative Score - Raw!CX3:CX535",
            ],
            "action": "materialize_explicit_zero",
        },
        "bill_derived_generic_success_percentages": {
            "raw_ranges": [
                "Legislative Score - Raw!AV3:AV535",
                "Legislative Score - Raw!DX3:DX535",
            ],
            "action": "recompute_from_current_accepted_bill_relationships",
            "stored_precision": "one_decimal_percentage_points",
            "live_formulas_prohibited": True,
            "old_frozen_values_prohibited": True,
            "normalized_method": "raw_divided_by_column_max",
            "uniform_definition_requires_explicit_user_approval": True,
        },
    }


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def allowed_roots() -> tuple[Path, ...]:
    roots = {
        (CODE_ROOT / "audits" / "legfix5" / "shadow_recompute").resolve(),
        (CODE_ROOT / "tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }
    for name in ("TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            roots.add(Path(value).resolve())
    return tuple(sorted(roots, key=str))


def validate_run_dir(raw: Path) -> Path:
    root = raw.resolve()
    if not root.is_dir():
        raise WorkbookRecomputeError(f"run directory is missing: {root}")
    if root == CODE_ROOT.resolve():
        raise WorkbookRecomputeError("production/workspace root is forbidden")
    if not any(is_relative_to(root, parent) for parent in allowed_roots()):
        raise WorkbookRecomputeError(
            "run directory must be under shadow audit, repo tmp, or OS temp"
        )
    return root


def safe_child(root: Path, raw: Any) -> Path:
    relative = Path(str(raw or ""))
    if (
        not str(raw or "")
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        raise WorkbookRecomputeError(f"unsafe staged path: {raw!r}")
    path = (root / relative).resolve()
    if not is_relative_to(path, root):
        raise WorkbookRecomputeError(f"path escapes staged root: {raw!r}")
    return path


def read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise WorkbookRecomputeError(f"required JSON is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise WorkbookRecomputeError(f"invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkbookRecomputeError(f"JSON root is not an object: {path}")
    return value


def artifact(path: Path, root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file() or not is_relative_to(resolved, root):
        raise WorkbookRecomputeError(f"artifact is missing/outside run: {path}")
    return {
        "path": resolved.relative_to(root).as_posix(),
        "size": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def validate_artifact(
    root: Path, item: Any, *, expected_path: str | None = None
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(item, dict):
        raise WorkbookRecomputeError("artifact receipt is absent")
    path = safe_child(root, item.get("path"))
    expected = str(item.get("sha256") or "").casefold()
    if expected_path and path != safe_child(root, expected_path):
        raise WorkbookRecomputeError(
            f"artifact path is not canonical: {item.get('path')!r}"
        )
    if not path.is_file() or not HEX_RE.fullmatch(expected):
        raise WorkbookRecomputeError(f"artifact path/hash is invalid: {path}")
    if sha256_file(path) != expected:
        raise WorkbookRecomputeError(f"artifact hash mismatch: {path}")
    if item.get("size") is not None and path.stat().st_size != int(
        item["size"]
    ):
        raise WorkbookRecomputeError(f"artifact size mismatch: {path}")
    return path, artifact(path, root)


def validate_step_ledger(root: Path) -> dict[str, Any]:
    ledger_path = root / STEP_LEDGER_NAME
    value = read_object(ledger_path)
    generation_id = str(value.get("generation_id") or "").casefold()
    if not (
        value.get("schema") == STEP_LEDGER_SCHEMA
        and value.get("status") in {"RUNNING", "COMPLETE"}
        and value.get("staged_only") is True
        and value.get("production_written") is False
        and HEX_RE.fullmatch(generation_id)
    ):
        raise WorkbookRecomputeError(
            "shadow step ledger is not a valid staged generation"
        )
    completed = value.get("completed_steps")
    if not isinstance(completed, list):
        raise WorkbookRecomputeError("step ledger completed_steps is absent")
    names = [
        str(item.get("name") or "")
        for item in completed
        if isinstance(item, dict)
    ]
    if names[: len(PRIOR_STEPS)] != list(PRIOR_STEPS):
        raise WorkbookRecomputeError(
            "step ledger is missing/reorders prerequisite consumers"
        )
    if "recompute_legislative_raw_normalized_sublayer" in names:
        raise WorkbookRecomputeError(
            "step ledger already records this recomputation"
        )
    return {
        "generation_id": generation_id,
        "completed_steps": names,
        "receipt": artifact(ledger_path, root),
    }


def validate_efficiency_definition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkbookRecomputeError(
            "legislative efficiency definition is unresolved"
        )
    required_text = (
        "definition_id",
        "numerator",
        "denominator",
        "empty_role_handling",
        "success_action_rule",
        "weighting_basis",
    )
    if not (
        value.get("status") == "APPROVED"
        and value.get("approved_by_user") is True
        and value.get("consistent_across_18_categories") is True
        and value.get("legacy_efficiency_column_count") == 17
        and value.get("resolved_category_count") == 18
        and str(value.get("layout_resolution") or "").strip()
        and all(str(value.get(key) or "").strip() for key in required_text)
    ):
        raise WorkbookRecomputeError(
            "legislative efficiency definition is not explicitly approved, "
            "complete, and consistent across all 18 categories"
        )
    return {key: value[key] for key in sorted(value)}


def validate_efficiency_layout(value: Any) -> dict[str, Any]:
    """Validate the explicit 18-category mapping onto legacy AG:AW."""
    if not isinstance(value, dict):
        raise WorkbookRecomputeError(
            "machine-readable 18-category efficiency layout is absent"
        )
    if not (
        value.get("raw_range") == "Sub Layer - Raw!AG4:AW536"
        and value.get("normalized_range")
        == "Sub Layer - Normalized!AG4:AW536"
        and value.get("physical_column_count") == 17
        and value.get("category_count") == 18
    ):
        raise WorkbookRecomputeError(
            "efficiency layout does not explicitly resolve 18 categories "
            "onto the legacy 17-column AG:AW surface"
        )
    slots = value.get("slots")
    mappings = value.get("category_mappings")
    if not isinstance(slots, list) or len(slots) != len(EFFICIENCY_COLUMNS):
        raise WorkbookRecomputeError(
            "efficiency layout must define every AG:AW slot"
        )
    normalized_slots: list[dict[str, Any]] = []
    occurrences: list[str] = []
    slot_by_column: dict[str, dict[str, Any]] = {}
    for column, item in zip(EFFICIENCY_COLUMNS, slots):
        if not isinstance(item, dict) or item.get("column") != column:
            raise WorkbookRecomputeError(
                f"efficiency slot {column} is absent or reordered"
            )
        categories = item.get("categories")
        aggregation = item.get("aggregation")
        if not (
            isinstance(categories, list)
            and categories
            and all(category in CATEGORIES for category in categories)
            and aggregation in {"identity", "mean"}
            and (len(categories) == 1) == (aggregation == "identity")
        ):
            raise WorkbookRecomputeError(
                f"efficiency slot {column} is unresolved"
            )
        normalized = {
            "column": column,
            "categories": list(categories),
            "aggregation": aggregation,
        }
        normalized_slots.append(normalized)
        slot_by_column[column] = normalized
        occurrences.extend(categories)
    if len(occurrences) != 18 or set(occurrences) != set(CATEGORIES):
        raise WorkbookRecomputeError(
            "every efficiency category must be mapped exactly once"
        )
    if not isinstance(mappings, dict) or set(mappings) != set(CATEGORIES):
        raise WorkbookRecomputeError(
            "efficiency layout category mappings are incomplete"
        )
    normalized_mappings: dict[str, dict[str, Any]] = {}
    for category in CATEGORIES:
        mapping = mappings.get(category)
        if not isinstance(mapping, dict):
            raise WorkbookRecomputeError(
                f"efficiency mapping is absent for {category}"
            )
        column = str(mapping.get("column") or "")
        slot = slot_by_column.get(column)
        peers = [
            item for item in (slot or {}).get("categories", [])
            if item != category
        ]
        expected = {
            "sheet": "Sub Layer - Raw",
            "range": f"{column}4:{column}536",
            "column": column,
            "aggregation": (slot or {}).get("aggregation"),
            "aggregate_with": peers,
            "source_metric": "normalized_generic_success_rate",
        }
        if mapping != expected or category not in (slot or {}).get(
            "categories", []
        ):
            raise WorkbookRecomputeError(
                f"efficiency mapping is inconsistent for {category}"
            )
        normalized_mappings[category] = expected
    return {
        "raw_range": value["raw_range"],
        "normalized_range": value["normalized_range"],
        "physical_column_count": 17,
        "category_count": 18,
        "slots": normalized_slots,
        "category_mappings": normalized_mappings,
    }


def validate_numeric_matrix(value: Any, *, rows: int, cols: int) -> None:
    if (
        not isinstance(value, list)
        or len(value) != rows
        or any(not isinstance(row, list) or len(row) != cols for row in value)
    ):
        raise WorkbookRecomputeError(
            f"numeric matrix must be exactly {rows}x{cols}"
        )
    for row in value:
        for cell in row:
            if (
                isinstance(cell, bool)
                or not isinstance(cell, (int, float))
                or not math.isfinite(float(cell))
            ):
                raise WorkbookRecomputeError(
                    "matrix contains a non-finite/non-numeric cell"
                )


def range_shape(value: str) -> tuple[int, int]:
    match = re.fullmatch(
        r"([A-Z]+)(\d+):([A-Z]+)(\d+)", str(value or "").upper()
    )
    if not match:
        raise WorkbookRecomputeError(f"unsupported plan range: {value!r}")

    def column_number(label: str) -> int:
        result = 0
        for char in label:
            result = result * 26 + ord(char) - ord("A") + 1
        return result

    return (
        int(match.group(4)) - int(match.group(2)) + 1,
        column_number(match.group(3)) - column_number(match.group(1)) + 1,
    )


def required_write_keys() -> set[tuple[str, str]]:
    keys = {
        ("Legislative Score - Raw", cell_range)
        for cell_range in BLOCK_RANGES.values()
    }
    keys |= {
        ("Legislative Score - Normalized", cell_range)
        for cell_range in BLOCK_RANGES.values()
    }
    for sheet in ("Sub Layer - Raw", "Sub Layer - Normalized"):
        keys |= {
            (sheet, f"{column}4:{column}536")
            for column in SUBLAYER_CATEGORY_COLUMNS
        }
        keys.add((sheet, SUBLAYER_EFFICIENCY_RANGE))
    return keys


def validate_numeric_plan(
    root: Path,
    item: Any,
    *,
    generation_id: str,
    selected_policy: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    path, receipt = validate_artifact(root, item)
    value = read_object(path)
    if not (
        value.get("schema") == PLAN_SCHEMA
        and value.get("status") == "PASS"
        and value.get("generation_id") == generation_id
        and value.get("selected_role_weight_policy") == selected_policy
        and value.get("role_weight_policy_reconciled") is True
        and value.get("workbook_role_weights_verified") is True
        and value.get("all_18_categories_recomputed") is True
        and value.get("unexplained_cell_count") == 0
    ):
        raise WorkbookRecomputeError(
            "numeric write plan is not PASS/current/fully reconciled"
        )
    categories = value.get("categories")
    if categories != list(CATEGORIES):
        raise WorkbookRecomputeError(
            "numeric write plan category order/coverage is not canonical 18"
        )
    efficiency = validate_efficiency_definition(
        value.get("legislative_efficiency_definition")
    )
    efficiency_layout = validate_efficiency_layout(
        value.get("efficiency_layout")
    )
    nonbill = value.get("nonbill_numeric_policy")
    letters = (
        nonbill.get("cfpb_sign_on_letters") if isinstance(nonbill, dict) else None
    )
    manual_hash = (
        letters.get("manual_bill_evidence_sha256")
        if isinstance(letters, dict)
        else None
    )
    if nonbill != required_nonbill_numeric_policy(str(manual_hash or "")):
        raise WorkbookRecomputeError(
            "numeric write plan must recompute CU/CW from hash-bound manual "
            "evidence under 1/.66/.33/.16 decay, explicitly zero CV/CX, and "
            "recompute AV/DX from current accepted bill relationships"
        )
    writes = value.get("writes")
    if not isinstance(writes, list):
        raise WorkbookRecomputeError("numeric write plan has no writes")
    normalized: list[dict[str, Any]] = []
    keys: list[tuple[str, str]] = []
    for write in writes:
        if not isinstance(write, dict):
            raise WorkbookRecomputeError("invalid numeric write entry")
        sheet = str(write.get("sheet") or "")
        cell_range = str(write.get("range") or "").upper()
        key = (sheet, cell_range)
        rows, cols = range_shape(cell_range)
        validate_numeric_matrix(write.get("values"), rows=rows, cols=cols)
        normalized.append(
            {
                "sheet": sheet,
                "range": cell_range,
                "values": write["values"],
            }
        )
        keys.append(key)
    required = required_write_keys()
    if len(keys) != len(set(keys)):
        raise WorkbookRecomputeError("numeric write plan has duplicate ranges")
    if set(keys) != required:
        missing = sorted(required - set(keys))
        extra = sorted(set(keys) - required)
        raise WorkbookRecomputeError(
            f"numeric write plan range coverage differs; missing={missing[:8]} "
            f"extra={extra[:8]}"
        )
    validated = {
        "schema": PLAN_SCHEMA,
        "status": "PASS",
        "generation_id": generation_id,
        "selected_role_weight_policy": selected_policy,
        "role_weight_policy_reconciled": True,
        "workbook_role_weights_verified": True,
        "all_18_categories_recomputed": True,
        "categories": list(CATEGORIES),
        "legislative_efficiency_definition": efficiency,
        "efficiency_layout": efficiency_layout,
        "nonbill_numeric_policy": nonbill,
        "unexplained_cell_count": 0,
        "writes": normalized,
    }
    return path, receipt, validated


def validate_accepted_receipt(
    root: Path, ledger: Mapping[str, Any], path: Path
) -> dict[str, Any]:
    value = read_object(path)
    generation_id = str(value.get("generation_id") or "").casefold()
    policy = str(value.get("selected_role_weight_policy") or "")
    if not (
        value.get("schema") == ACCEPTED_RECEIPT_SCHEMA
        and value.get("status") == "PASS"
        and value.get("staged_only") is True
        and value.get("production_written") is False
        and generation_id == ledger["generation_id"]
        and policy in ROLE_POLICIES
        and value.get("role_weight_policy_reconciled") is True
        and value.get("selected_method_profile")
        == ROLE_METHOD_PROFILE_ID
        and value.get("consumer_role_methods") == ROLE_METHOD_PROFILE
    ):
        raise WorkbookRecomputeError(
            "accepted workbook receipt is not PASS/generation-bound or its "
            "role policy is unselected/unreconciled"
        )
    outputs = value.get("outputs")
    if not isinstance(outputs, dict):
        raise WorkbookRecomputeError("accepted workbook outputs are absent")
    approval_path, approval_receipt = validate_artifact(
        root, value.get("role_weight_policy_approval")
    )
    approval = read_object(approval_path)
    if not (
        approval.get("schema") == ROLE_APPROVAL_SCHEMA
        and approval.get("status") == "APPROVED"
        and approval.get("human_approved") is True
        and approval.get("approved_by_user") is True
        and approval.get("selected_role_weight_policy") == policy
        and policy == "workbook_legacy_4_2_1"
        and approval.get("selected_method_profile")
        == ROLE_METHOD_PROFILE_ID
        and approval.get("selected_policy") == ROLE_METHOD_PROFILE_ID
        and approval.get("consumer_methods") == ROLE_METHOD_PROFILE
        and approval.get("applies_to") == list(ROLE_METHOD_PROFILE)
        and str(approval.get("accepted_ledger_sha256") or "").casefold()
        == generation_id
    ):
        raise WorkbookRecomputeError(
            "role-weight policy approval is absent, invalid, or not "
            "hash-bound to the accepted ledger generation"
        )
    workbook_path, workbook_receipt = validate_artifact(
        root, outputs.get(WORKBOOK_NAME), expected_path=WORKBOOK_NAME
    )
    plan_path, plan_receipt, plan = validate_numeric_plan(
        root,
        value.get("numeric_recompute_plan"),
        generation_id=generation_id,
        selected_policy=policy,
    )
    return {
        "generation_id": generation_id,
        "selected_role_weight_policy": policy,
        "selected_method_profile": ROLE_METHOD_PROFILE_ID,
        "consumer_role_methods": ROLE_METHOD_PROFILE,
        "workbook_path": workbook_path,
        "workbook": workbook_receipt,
        "plan_path": plan_path,
        "plan": plan_receipt,
        "validated_plan": plan,
        "role_weight_policy_approval": approval_receipt,
        "receipt": artifact(path, root),
    }


def validate_runtime() -> tuple[Path, Path]:
    raw_node = os.environ.get("ARTIFACT_TOOL_NODE")
    raw_modules = os.environ.get("ARTIFACT_TOOL_NODE_MODULES")
    if not raw_node or not raw_modules:
        raise WorkbookRecomputeError(
            "artifact-tool runtime is not configured; "
            "ARTIFACT_TOOL_NODE and ARTIFACT_TOOL_NODE_MODULES must come "
            "from the accepted producer capability receipt"
        )
    node = Path(raw_node).resolve()
    modules = Path(raw_modules).resolve()
    if not node.is_file() or not modules.is_dir():
        raise WorkbookRecomputeError(
            "configured artifact-tool Node/module paths do not exist"
        )
    return node, modules


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(
        json.dumps(
            value, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        + b"\n"
    )
    os.replace(temporary, path)


def validate_artifact_tool_verification(
    verification: Mapping[str, Any],
    *,
    generation_id: str,
) -> dict[str, Any]:
    required = required_write_keys()
    expected_cells = sum(
        math.prod(range_shape(cell_range))
        for _, cell_range in required
    )
    checks = verification.get("numeric_checks")
    checks = checks if isinstance(checks, list) else []
    keyed_checks: dict[tuple[str, str], Mapping[str, Any]] = {}
    duplicate_checks: set[tuple[str, str]] = set()
    for raw in checks:
        if not isinstance(raw, Mapping):
            continue
        key = (str(raw.get("sheet") or ""), str(raw.get("range") or ""))
        if key in keyed_checks:
            duplicate_checks.add(key)
        keyed_checks[key] = raw
    count_issues: list[str] = []
    if set(keyed_checks) != required:
        count_issues.append("critical inspection range coverage differs")
    if duplicate_checks:
        count_issues.append("duplicate critical inspection ranges")
    for sheet, cell_range in sorted(required):
        item = keyed_checks.get((sheet, cell_range))
        if item is None:
            continue
        rows, cols = range_shape(cell_range)
        expected = {
            "table_record_count": 1,
            "expected_rows": rows,
            "expected_cols": cols,
            "expected_cells": rows * cols,
            "actual_rows": rows,
            "actual_cols": cols,
            "actual_cells": rows * cols,
            "count_match": True,
            "non_numeric_cells": 0,
        }
        if any(item.get(key) != value for key, value in expected.items()):
            count_issues.append(
                f"critical inspection count mismatch: {sheet}!{cell_range}"
            )
    blended = verification.get("blended_inspection")
    blended = blended if isinstance(blended, Mapping) else {}
    expected_blended = {
        "range": "A3:O535",
        "table_record_count": 1,
        "expected_rows": EXPECTED_PILLAR_MEMBER_COUNT,
        "expected_cols": 15,
        "expected_cells": EXPECTED_PILLAR_MEMBER_COUNT * 15,
        "actual_rows": EXPECTED_PILLAR_MEMBER_COUNT,
        "actual_cols": 15,
        "actual_cells": EXPECTED_PILLAR_MEMBER_COUNT * 15,
        "count_match": True,
    }
    if any(blended.get(key) != value for key, value in expected_blended.items()):
        count_issues.append("blended inspection count mismatch")
    field_counts = verification.get("pillar_snapshot_field_counts")
    field_counts = field_counts if isinstance(field_counts, Mapping) else {}
    expected_summary = {
        "critical_inspection_expected_count": len(required),
        "critical_inspection_actual_count": len(required),
        "critical_inspection_exact_count_passes": len(required),
        "critical_inspection_count_mismatches": 0,
        "critical_inspection_expected_cells": expected_cells,
        "critical_inspection_actual_cells": expected_cells,
        "pillar_snapshot_expected_members": EXPECTED_PILLAR_MEMBER_COUNT,
        "pillar_snapshot_members": EXPECTED_PILLAR_MEMBER_COUNT,
        "pillar_snapshot_member_count_match": True,
        "pillar_snapshot_unnamed_rows": 0,
    }
    if any(
        verification.get(key) != value
        for key, value in expected_summary.items()
    ):
        count_issues.append("verification summary count mismatch")
    if verification.get("pillar_snapshot_duplicate_names") != []:
        count_issues.append("pillar snapshot contains duplicate names")
    if any(
        field_counts.get(key) != EXPECTED_PILLAR_MEMBER_COUNT
        for key in ("O", "E", "F")
    ):
        count_issues.append("pillar snapshot field count mismatch")
    base_pass = (
        verification.get("schema")
        == "legislation-workbook-artifact-tool-verification-v1"
        and verification.get("status") == "PASS"
        and verification.get("engine") == "@oai/artifact-tool"
        and verification.get("generation_id") == generation_id
        and verification.get("formula_error_count") == 0
        and verification.get("non_numeric_cells") == 0
        and verification.get("pillar_snapshot_invalid_cells") == 0
        and verification.get("output_written") is True
    )
    if not base_pass or count_issues:
        detail = "; ".join(count_issues[:10])
        raise WorkbookRecomputeError(
            "artifact-tool verification did not pass every "
            f"numeric/error/exact-count gate{': ' + detail if detail else ''}"
        )
    return {
        **expected_summary,
        "blended_inspection": dict(blended),
        "pillar_snapshot_field_counts": {
            key: int(field_counts[key]) for key in ("O", "E", "F")
        },
    }


def invoke_helper(
    *,
    node: Path,
    modules: Path,
    root: Path,
    accepted: Mapping[str, Any],
    validated_plan: Mapping[str, Any],
    suffix: str,
    render: bool,
) -> dict[str, Path]:
    runtime = root / "intermediate" / "artifact_tool_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    helper = runtime / HELPER.name
    shutil.copyfile(HELPER, helper)
    plan_path = runtime / f"validated_numeric_plan_{suffix}.json"
    atomic_json(plan_path, validated_plan)
    output = runtime / f"workbook_{suffix}.xlsx"
    snapshot = runtime / f"pillar_snapshot_{suffix}.json"
    report = runtime / f"verification_{suffix}.json"
    render_dir = runtime / f"renders_{suffix}"
    command = [
        str(node),
        str(helper),
        str(accepted["workbook_path"]),
        str(plan_path),
        str(output),
        str(snapshot),
        str(report),
    ]
    if render:
        command.append(str(render_dir))
    child_env = os.environ.copy()
    child_env["NODE_PATH"] = str(modules)
    completed = subprocess.run(
        command,
        cwd=runtime,
        env=child_env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0:
        raise WorkbookRecomputeError(
            "artifact-tool workbook recompute failed: "
            + (completed.stderr or completed.stdout)[-2000:]
        )
    verification = read_object(report)
    exact_count_verification = validate_artifact_tool_verification(
        verification,
        generation_id=str(accepted["generation_id"]),
    )
    if not (output.is_file() and snapshot.is_file()):
        raise WorkbookRecomputeError(
            "artifact-tool verification passed but output artifacts are absent"
        )
    return {
        "output": output,
        "snapshot": snapshot,
        "report": report,
        "plan": plan_path,
        "helper": helper,
        "exact_count_verification": exact_count_verification,
    }


def install_output(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    if sha256_file(source) != sha256_file(temporary):
        temporary.unlink(missing_ok=True)
        raise WorkbookRecomputeError("staged output hash-copy failed")
    os.replace(temporary, destination)


def run(
    *,
    run_dir: Path,
    accepted_receipt_path: Path | None,
) -> dict[str, Any]:
    root = validate_run_dir(run_dir)
    ledger = validate_step_ledger(root)
    accepted_path = (
        accepted_receipt_path.resolve()
        if accepted_receipt_path
        else root / ACCEPTED_RECEIPT_NAME
    )
    if not is_relative_to(accepted_path, root):
        raise WorkbookRecomputeError(
            "accepted workbook receipt must be inside the staged run"
        )
    prior_receipt_path = root / RECEIPT_NAME
    if prior_receipt_path.exists():
        prior = read_object(prior_receipt_path)
        outputs = prior.get("outputs") or {}
        prior_inputs = prior.get("inputs") or {}
        prior_accepted = (
            prior_inputs.get("accepted_workbook_receipt")
            if isinstance(prior_inputs, dict)
            else None
        )
        prior_verify = prior.get("artifact_tool_verification") or {}
        if not (
            isinstance(outputs, dict)
            and isinstance(prior_accepted, dict)
            and isinstance(prior_verify, dict)
            and prior.get("schema") == RECEIPT_SCHEMA
            and prior.get("status") == "PASS"
            and prior.get("generation_id") == ledger["generation_id"]
            and prior.get("staged_only") is True
            and prior.get("production_written") is False
            and prior_accepted.get("sha256") == sha256_file(accepted_path)
            and prior_verify.get("exact_counts_validated") is True
            and prior_verify.get("critical_inspection_count_mismatches") == 0
            and prior_verify.get("pillar_snapshot_members")
            == EXPECTED_PILLAR_MEMBER_COUNT
        ):
            raise WorkbookRecomputeError(
                "existing workbook recompute receipt is stale/invalid"
            )
        for name in (WORKBOOK_NAME, SNAPSHOT_NAME):
            validate_artifact(root, outputs.get(name), expected_path=name)
        return {**prior, "_idempotent_reuse": True}

    accepted = validate_accepted_receipt(root, ledger, accepted_path)
    node, modules = validate_runtime()
    preserved_input = (
        root / "intermediate" / "accepted_legislative_workbook_input.xlsx"
    )
    preserved_input.parent.mkdir(parents=True, exist_ok=True)
    install_output(accepted["workbook_path"], preserved_input)
    accepted_for_helper = {
        **accepted,
        "workbook_path": preserved_input,
        "preserved_input_workbook": artifact(preserved_input, root),
    }
    workbook_before = accepted["workbook"]["sha256"]
    first = invoke_helper(
        node=node,
        modules=modules,
        root=root,
        accepted=accepted_for_helper,
        validated_plan=accepted["validated_plan"],
        suffix="run1",
        render=True,
    )
    second = invoke_helper(
        node=node,
        modules=modules,
        root=root,
        accepted=accepted_for_helper,
        validated_plan=accepted["validated_plan"],
        suffix="run2",
        render=False,
    )
    if sha256_file(preserved_input) != workbook_before:
        raise WorkbookRecomputeError(
            "accepted staged workbook changed during helper execution"
        )
    if (
        sha256_file(first["output"]) != sha256_file(second["output"])
        or sha256_file(first["snapshot"]) != sha256_file(second["snapshot"])
        or first["exact_count_verification"]
        != second["exact_count_verification"]
    ):
        raise WorkbookRecomputeError(
            "artifact-tool recomputation is not byte-idempotent"
        )
    install_output(first["output"], root / WORKBOOK_NAME)
    install_output(first["snapshot"], root / SNAPSHOT_NAME)
    outputs = {
        WORKBOOK_NAME: artifact(root / WORKBOOK_NAME, root),
        SNAPSHOT_NAME: artifact(root / SNAPSHOT_NAME, root),
    }
    basis = {
        "generation_id": accepted["generation_id"],
        "selected_role_weight_policy": accepted[
            "selected_role_weight_policy"
        ],
        "selected_method_profile": accepted[
            "selected_method_profile"
        ],
        "consumer_role_methods": accepted[
            "consumer_role_methods"
        ],
        "accepted_receipt_sha256": accepted["receipt"]["sha256"],
        "role_weight_policy_approval_sha256": accepted[
            "role_weight_policy_approval"
        ]["sha256"],
        "numeric_plan_sha256": accepted["plan"]["sha256"],
        "helper_sha256": sha256_file(HELPER),
        "outputs": {
            name: item["sha256"] for name, item in sorted(outputs.items())
        },
    }
    result = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "approved_engine": "@oai/artifact-tool",
        "staged_only": True,
        "production_written": False,
        "mainframe_written": False,
        "generation_id": accepted["generation_id"],
        "selected_role_weight_policy": accepted[
            "selected_role_weight_policy"
        ],
        "selected_method_profile": accepted[
            "selected_method_profile"
        ],
        "consumer_role_methods": accepted[
            "consumer_role_methods"
        ],
        "role_policy_approval_sha256": accepted[
            "role_weight_policy_approval"
        ]["sha256"],
        "role_weight_policy_approval": accepted[
            "role_weight_policy_approval"
        ],
        "role_weight_policy_approval_hash_bound": True,
        "role_weight_policy_reconciled": True,
        "all_18_categories_recomputed": True,
        "legislative_efficiency_definition_consistent": True,
        "legislative_efficiency_definition": accepted["validated_plan"][
            "legislative_efficiency_definition"
        ],
        "unexplained_cell_count": 0,
        "engine": "@oai/artifact-tool",
        "python_xlsx_library_used": False,
        "artifact_tool_runs_byte_idempotent": True,
        "formula_error_count": 0,
        "non_numeric_legislation_dependent_cells": 0,
        "inputs": {
            "step_ledger": ledger["receipt"],
            "accepted_workbook_receipt": accepted["receipt"],
            "accepted_workbook": accepted["workbook"],
            "numeric_recompute_plan": accepted["plan"],
        },
        "input_workbook": accepted_for_helper["preserved_input_workbook"],
        "output_workbook": outputs[WORKBOOK_NAME],
        "outputs": outputs,
        "artifact_tool_verification": {
            "passed": True,
            "exact_counts_validated": True,
            **first["exact_count_verification"],
            "required_sheets_present": True,
            "formula_error_count": 0,
            "non_numeric_critical_cells": 0,
            "required_sheets": [
                "Legislative Score - Raw",
                "Legislative Score - Normalized",
                "Sub Layer - Raw",
                "Sub Layer - Normalized",
                "Blended Model",
            ],
            "required_sheets_passed": True,
            "numeric_sample": {
                "passed": True,
                "non_numeric_cells": 0,
            },
            "formula_error_scan": {
                "passed": True,
                "record_count": 0,
            },
            "run1": artifact(first["report"], root),
            "run2": artifact(second["report"], root),
        },
        "verification": {
            "run1": artifact(first["report"], root),
            "run2": artifact(second["report"], root),
        },
        "generation_basis_sha256": sha256_bytes(canonical_bytes(basis)),
    }
    atomic_json(prior_receipt_path, result)
    return {**result, "_idempotent_reuse": False}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Recompute all legislation-dependent workbook cells in a "
            "generation-bound shadow root using @oai/artifact-tool."
        )
    )
    result.add_argument("--run-dir", type=Path, default=Path(MB_ROOT))
    result.add_argument("--accepted-workbook-receipt", type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = run(
            run_dir=args.run_dir,
            accepted_receipt_path=args.accepted_workbook_receipt,
        )
    except (
        WorkbookRecomputeError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"recompute_legislative_workbook: FAIL CLOSED: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

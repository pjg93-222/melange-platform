# -*- coding: utf-8 -*-
"""Materialize accepted legislation into isolated archive/model workbooks.

This is the only adapter from the accepted relationship generation to the
legacy legislation workbook surfaces.  It is deliberately fail-closed:

* it runs only in a sealed ``audits/legfix5/shadow_recompute`` run;
* it reads only hash-verified staged producer inputs and frozen baseline copies;
* all XLSX import, mutation, rendering, inspection, and export is delegated to
  ``@oai/artifact-tool`` by ``build_accepted_legislation_workbook.cjs``; and
* excluded, withdrawn, non-scoring, and neutral relationships remain visible
  in the archive/routing audit but contribute exactly zero.

The model workbook written at the run root is working state for the later
legislative recompute step, not a final blended-workbook output.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Sequence

from paths import ROOT as MB_ROOT
try:
    from legislation_measure_key import measure_weight, measure_weight_policy
except ImportError:  # pragma: no cover - package import path
    from fec.legislation_measure_key import measure_weight, measure_weight_policy


CODE_ROOT = Path(__file__).resolve().parents[2]
AUDIT_ROOT = (CODE_ROOT / "audits" / "legfix5").resolve()
SHADOW_ROOT = (AUDIT_ROOT / "shadow_recompute").resolve()
BASELINE_MANIFEST = (
    AUDIT_ROOT
    / "production_baseline_full_downstream"
    / "baseline_manifest.json"
)
JS_HELPER = (
    Path(__file__).resolve().parent.parent
    / "build_accepted_legislation_workbook.cjs"
)

SCHEMA = "accepted-legislation-workbook-receipt-v1"
CAPABILITY_SCHEMA = "accepted-legislation-workbook-producer-capability-v1"
ROUTING_SCHEMA = "legislation-routing-audit-v2"
PREPARED_SCHEMA = "accepted-legislation-workbook-payload-v1"
ACCEPTED_SCHEMA = "accepted-legislation-relationship-v1"
ACCEPTED_MANIFEST_SCHEMA = "accepted-legislation-generation-manifest-v1"
DIRECTION_MANIFEST_SCHEMA = "legislation-direction-ledger-manifest-v1"
DIRECTION_SCHEMA = "legislation-direction-decision-v1"
STEP_LEDGER_SCHEMA = "legislation-shadow-step-receipts-v1"
NUMERIC_PLAN_SCHEMA = "legislation-workbook-numeric-write-plan-v1"
STEP_NAME = "materialize_archive_and_legislative_workbook"
EXPECTED_DECAY = {116: 0.16, 117: 0.33, 118: 0.66, 119: 1.0}
ARCHIVE_ROLE_WEIGHT = {
    "sponsor": 3.0,
    "original_cosponsor": 2.0,
    "cosponsor": 1.0,
}
LEGACY_ARCHIVE_POLICY = "archive_legacy_3_2_1"
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
ADVANCE_WEIGHT = {
    "Introduced": 0.0,
    "Passed House": 1.0,
    "Passed Senate": 1.0,
    "Agreed to in Senate": 1.0,
    "Resolving Differences": 1.5,
    "Failed to pass over veto": 1.5,
    "Became Law": 2.0,
}
HEX_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
BIOGUIDE_RE = re.compile(r"^[A-Z]\d{6}$")
MEASURE_RE = re.compile(
    r"^(116|117|118|119):"
    r"(hr|hres|hjres|hconres|hamdt|s|sres|sjres|sconres|samdt):"
    r"([1-9]\d*)$"
)

CATEGORY_ALIASES = {
    "examination_supervision": "Examination & Supervision",
    "credit_union_capital_requirements": "CU Capital Requirements",
    "cu_capital_requirements": "CU Capital Requirements",
    "ncua_budget_share_insurance": "NCUA Budget & Insurance",
    "ncua_budget_insurance": "NCUA Budget & Insurance",
    "interchange": "Interchange",
    "payments": "Payments",
    "technology_emerging_issues": "Technology & Emerging",
    "technology_emerging": "Technology & Emerging",
    "fraud_prevention": "Fraud Prevention",
    "data_privacy_cybersecurity": "Data Privacy & Cyber",
    "data_privacy_cyber": "Data Privacy & Cyber",
    "charter_modernization_governance": "Charter Modernization",
    "charter_modernization": "Charter Modernization",
    "defense_credit_union_issues": "Defense CU Issues",
    "defense_cu_issues": "Defense CU Issues",
    "field_of_membership": "Field of Membership",
    "cdfi_fund": "CDFI Fund",
    "cfpb": "CFPB",
    "credit_card_mandates": "Credit Card Mandates",
    "credit_card_consumer_fee": "Credit Card Mandates",
    "tcpa": "TCPA",
    "telephone_consumer": "TCPA",
    "member_business_lending": "Member Business Lending",
    "mortgage_lending_housing": "Mortgage & Housing",
    "mortgage_housing": "Mortgage & Housing",
    "credit_union_tax_status": "CU Tax Status",
    "cu_tax_status": "CU Tax Status",
}
for _category in tuple(set(CATEGORY_ALIASES.values())):
    CATEGORY_ALIASES[_category.casefold()] = _category

INTERACTION_HEADERS = [
    "Section",
    "Role",
    "Member",
    "Name",
    "Title",
    "Sponsor",
    "CosponsorCount",
    "Committee",
    "LastAction",
    "Congress",
    "DecayWeight",
    "DetailLink",
    "bill_summary",
]
# The user approved the original workbook surface.  Audit-only fields belong
# in legislation_routing_audit.json, never as appended All Bills columns.
ARCHIVE_HEADERS = INTERACTION_HEADERS
TARGET_MODEL_SHEETS = (
    "Sponsor Interactions",
    "Original Co-Sponsor Interaction",
    "Co-Sponsor Interactions",
    "Success Sponsor",
    "Success Original Co-Sponsor",
)


def required_nonbill_numeric_policy(
    manual_evidence_sha256: str,
) -> dict[str, Any]:
    """Return the only accepted treatment of audited non-standard columns."""
    sha = str(manual_evidence_sha256 or "").casefold()
    if not HEX_SHA_RE.fullmatch(sha):
        raise WorkbookBuildError("manual evidence SHA-256 is invalid")
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


class WorkbookBuildError(RuntimeError):
    """An input, isolation, policy, or artifact-tool invariant failed."""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Build accepted legislation archive/model inputs under a sealed "
            "shadow run. Production paths and alternate XLSX engines are refused."
        )
    )
    result.add_argument("--run-dir", type=Path)
    result.add_argument("--input-root", type=Path)
    result.add_argument("--baseline-manifest", type=Path)
    result.add_argument(
        "--smoke-capability-out",
        type=Path,
        help=(
            "Run a real frozen-workbook artifact-tool smoke test and write the "
            "capability receipt under audits/legfix5."
        ),
    )
    return result


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(
    path: Path, *, relative_to: Path | None = None
) -> dict[str, Any]:
    shown = path.relative_to(relative_to) if relative_to else path.resolve()
    return {
        "path": str(shown).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkbookBuildError(f"invalid JSON at {path}: {exc}") from exc


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise WorkbookBuildError(f"cannot read NDJSON at {path}: {exc}") from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkbookBuildError(
                f"invalid NDJSON at {path}:{number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise WorkbookBuildError(f"non-object NDJSON row at {path}:{number}")
        rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def normalize_token(value: Any) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def historical_scoring_authorization(
    row: dict[str, Any],
) -> tuple[bool, str | None]:
    """Recompute the fourth term of the producer's scoring-eligibility rule.

    ``build_accepted_legislation_generation`` computes ``scoring_eligible``
    from FOUR terms; this verifier used to recompute only three, dropping
    ``historical_scoring_authorized``.  Against a produced ledger that failed
    closed, but against a SUPPLIED ledger it failed open: a row marked
    ``historical_membership_only`` with ``historical_relevance_ratified``
    false could still claim ``scoring_eligible: true``, because the
    ratification fields were never read at all.

    Returns ``(authorized, error)``.  ``error`` is non-None when the historical
    provenance fields are shaped in a way the producer can never emit, which is
    itself a forgery signal.
    """

    historical_only = row.get("historical_membership_only")
    ratified = row.get("historical_relevance_ratified")
    if not isinstance(historical_only, bool):
        return False, (
            "historical_membership_only must be a boolean, found "
            f"{historical_only!r}"
        )
    if historical_only:
        if not isinstance(ratified, bool):
            return False, (
                "historical rows must carry a boolean "
                f"historical_relevance_ratified, found {ratified!r}"
            )
        return ratified, None
    if ratified is not None:
        return False, (
            "non-historical rows must leave historical_relevance_ratified "
            f"null, found {ratified!r}"
        )
    return True, None


def canonical_category(value: Any) -> str:
    text = " ".join(str(value or "").split())
    category = CATEGORY_ALIASES.get(text.casefold())
    if not category:
        category = CATEGORY_ALIASES.get(normalize_token(text))
    if not category:
        raise WorkbookBuildError(f"unrecognized scoring category: {value!r}")
    return category


def recursive_sha_values(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold().endswith("sha256"):
                text = str(item or "").casefold()
                if HEX_SHA_RE.fullmatch(text):
                    found.add(text)
            found.update(recursive_sha_values(item))
    elif isinstance(value, list):
        for item in value:
            found.update(recursive_sha_values(item))
    return found


def validate_shadow_context(
    run_dir: Path | None, input_root: Path | None
) -> tuple[Path, Path]:
    if os.environ.get("LEGISLATION_SHADOW_MODE") != "1":
        raise WorkbookBuildError("LEGISLATION_SHADOW_MODE=1 is required")
    root = (
        run_dir
        or (
            Path(os.environ["LEGISLATION_SHADOW_RUN_DIR"])
            if os.environ.get("LEGISLATION_SHADOW_RUN_DIR")
            else None
        )
        or Path(MB_ROOT)
    ).resolve()
    declared = os.environ.get("LEGISLATION_SHADOW_RUN_DIR")
    moneyball = os.environ.get("MONEYBALL_ROOT")
    if not declared or Path(declared).resolve() != root:
        raise WorkbookBuildError(
            "LEGISLATION_SHADOW_RUN_DIR must exactly equal the run directory"
        )
    if not moneyball or Path(moneyball).resolve() != root:
        raise WorkbookBuildError("MONEYBALL_ROOT must exactly equal the run directory")
    allowed_root = SHADOW_ROOT
    test_root = os.environ.get("LEGISLATION_SHADOW_TEST_ROOT")
    if test_root:
        candidate = Path(test_root).resolve()
        if not _is_relative_to(candidate, Path(tempfile.gettempdir()).resolve()):
            raise WorkbookBuildError(
                "LEGISLATION_SHADOW_TEST_ROOT must be under the system temp root"
            )
        allowed_root = candidate
    if not _is_relative_to(root, allowed_root) or root == allowed_root:
        raise WorkbookBuildError(
            f"run directory must be a child of {allowed_root}: {root}"
        )
    if not root.is_dir():
        raise WorkbookBuildError(f"shadow run directory does not exist: {root}")
    inputs = (
        input_root
        or (
            Path(os.environ["LEGISLATION_SHADOW_INPUT_ROOT"])
            if os.environ.get("LEGISLATION_SHADOW_INPUT_ROOT")
            else root / "inputs"
        )
    ).resolve()
    if not _is_relative_to(inputs, root) or inputs == root:
        raise WorkbookBuildError("input root must be a child of the shadow run")
    if inputs != (root / "inputs").resolve():
        raise WorkbookBuildError("input root must be exactly <run>/inputs")
    if not inputs.is_dir():
        raise WorkbookBuildError(f"shadow input root does not exist: {inputs}")
    return root, inputs


def baseline_copy(
    manifest_path: Path,
    manifest: dict[str, Any],
    basename: str,
) -> Path:
    matches = [
        item
        for item in manifest.get("files", [])
        if isinstance(item, dict)
        and Path(str(item.get("copy") or "")).name.casefold()
        == basename.casefold()
    ]
    if len(matches) != 1:
        raise WorkbookBuildError(
            f"baseline has {len(matches)} copies named {basename!r}"
        )
    entry = matches[0]
    path = Path(str(entry["copy"]))
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve()
    frozen_files = (manifest_path.parent / "files").resolve()
    if not _is_relative_to(path, frozen_files):
        raise WorkbookBuildError(
            f"baseline copy escapes immutable files directory: {path}"
        )
    if not path.is_file():
        raise WorkbookBuildError(f"frozen baseline copy is missing: {path}")
    expected = str(entry.get("sha256") or "").casefold()
    if not HEX_SHA_RE.fullmatch(expected) or sha256_file(path) != expected:
        raise WorkbookBuildError(f"frozen baseline hash mismatch: {path}")
    if entry.get("size") is not None and path.stat().st_size != int(entry["size"]):
        raise WorkbookBuildError(f"frozen baseline size mismatch: {path}")
    return path


def verify_frozen_baseline(
    value: Path | None, *, full: bool = True
) -> dict[str, Any]:
    path = (value or BASELINE_MANIFEST).resolve()
    manifest = read_json(path)
    files = manifest.get("files")
    if not (
        isinstance(manifest, dict)
        and manifest.get("schema") == "legislation-production-baseline-v1"
        and manifest.get("immutable_existing_copy_policy") is True
        and manifest.get("missing_named_artifacts") == []
        and isinstance(files, list)
        and manifest.get("file_count") == len(files)
    ):
        raise WorkbookBuildError("frozen production baseline manifest is incomplete")
    selection = manifest.get("selection") or {}
    archive_name = str(selection.get("archive_selected_by_mtime") or "")
    if not archive_name:
        raise WorkbookBuildError("baseline archive selection is absent")
    model_name = "Moneyball Main Version - Blended.xlsx"
    if full:
        for entry in files:
            if not isinstance(entry, dict):
                raise WorkbookBuildError("non-object frozen baseline entry")
            baseline_copy(path, manifest, Path(str(entry.get("copy") or "")).name)
    archive = baseline_copy(path, manifest, archive_name)
    model = baseline_copy(path, manifest, model_name)
    return {
        "manifest_path": path,
        "manifest": manifest,
        "manifest_receipt": file_receipt(path),
        "verified_copy_count": len(files) if full else 2,
        "archive": archive,
        "archive_receipt": file_receipt(archive),
        "model": model,
        "model_receipt": file_receipt(model),
    }


def find_materialized_by_hash(
    directory: Path,
    expected_sha256: str,
    *,
    expected_size: int | None = None,
    label: str,
) -> Path:
    sha = str(expected_sha256 or "").casefold()
    if not HEX_SHA_RE.fullmatch(sha):
        raise WorkbookBuildError(f"{label} lacks a valid SHA-256")
    matches: list[Path] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        if expected_size is not None and path.stat().st_size != int(expected_size):
            continue
        if sha256_file(path) == sha:
            matches.append(path.resolve())
    if len(matches) != 1:
        raise WorkbookBuildError(
            f"{label} resolved to {len(matches)} manifest-adjacent staged files"
        )
    return matches[0]


def declared_receipt(
    manifest: dict[str, Any], name: str
) -> dict[str, Any]:
    direct = manifest.get(name)
    if isinstance(direct, dict):
        return direct
    outputs = manifest.get("outputs")
    if isinstance(outputs, dict):
        for key in (name, f"{name}.json"):
            item = outputs.get(key)
            if isinstance(item, dict):
                return item
    raise WorkbookBuildError(
        f"accepted generation lacks declared {name} receipt"
    )


def validate_role_policy_approval(
    directory: Path,
    manifest: dict[str, Any],
    *,
    generation_id: str,
    selected_policy: str,
) -> dict[str, Any]:
    item = declared_receipt(manifest, "role_weight_policy_approval")
    path = find_materialized_by_hash(
        directory,
        str(item.get("sha256") or ""),
        expected_size=(
            int(item["size"]) if item.get("size") is not None else None
        ),
        label="role-weight policy approval",
    )
    value = read_json(path)
    if not (
        isinstance(value, dict)
        and value.get("schema") == ROLE_APPROVAL_SCHEMA
        and value.get("status") == "APPROVED"
        and value.get("human_approved") is True
        and value.get("approved_by_user") is True
        and value.get("selected_role_weight_policy") == selected_policy
        and selected_policy == "workbook_legacy_4_2_1"
        and value.get("selected_method_profile")
        == ROLE_METHOD_PROFILE_ID
        and value.get("selected_policy") == ROLE_METHOD_PROFILE_ID
        and value.get("consumer_methods") == ROLE_METHOD_PROFILE
        and value.get("applies_to") == list(ROLE_METHOD_PROFILE)
        and str(value.get("accepted_ledger_sha256") or "").casefold()
        == generation_id
    ):
        raise WorkbookBuildError(
            "role-weight policy approval is absent, not human-approved, or "
            "not hash-bound to the accepted generation"
        )
    return {"path": path, "value": value, "receipt": file_receipt(path)}


def validate_manual_bill_evidence(
    directory: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    item = declared_receipt(manifest, "manual_bill_evidence")
    path = find_materialized_by_hash(
        directory,
        str(item.get("sha256") or ""),
        expected_size=(
            int(item["size"]) if item.get("size") is not None else None
        ),
        label="manual sign-on-letter evidence",
    )
    if path.suffix.casefold() != ".csv":
        raise WorkbookBuildError(
            "manual sign-on-letter evidence must be a staged CSV"
        )
    return {"path": path, "receipt": file_receipt(path)}


def validate_numeric_recompute_plan(
    directory: Path,
    manifest: dict[str, Any],
    *,
    generation_id: str,
    selected_policy: str,
    manual_evidence_sha256: str,
) -> dict[str, Any]:
    item = declared_receipt(manifest, "numeric_recompute_plan")
    path = find_materialized_by_hash(
        directory,
        str(item.get("sha256") or ""),
        expected_size=(
            int(item["size"]) if item.get("size") is not None else None
        ),
        label="numeric legislation workbook recompute plan",
    )
    value = read_json(path)
    efficiency = (
        value.get("legislative_efficiency_definition")
        if isinstance(value, dict)
        else None
    )
    if not (
        isinstance(value, dict)
        and value.get("schema") == NUMERIC_PLAN_SCHEMA
        and value.get("status") == "PASS"
        and value.get("generation_id") == generation_id
        and value.get("selected_role_weight_policy") == selected_policy
        and value.get("role_weight_policy_reconciled") is True
        and value.get("workbook_role_weights_verified") is True
        and value.get("measure_weight_policy") == measure_weight_policy()
        and isinstance(value.get("writes"), list)
        and value.get("unexplained_cell_count") == 0
        and isinstance(efficiency, dict)
        and efficiency.get("status") == "APPROVED"
        and efficiency.get("approved_by_user") is True
    ):
        raise WorkbookBuildError(
            "numeric recompute plan is absent, stale, incomplete, or lacks "
            "explicit user approval for its efficiency definition"
        )
    legacy_columns = int(efficiency.get("legacy_efficiency_column_count") or 17)
    resolved_categories = int(efficiency.get("resolved_category_count") or 0)
    layout_resolution = str(efficiency.get("layout_resolution") or "").strip()
    if legacy_columns == 17 and (
        resolved_categories != 18 or not layout_resolution
    ):
        raise WorkbookBuildError(
            "legacy AG:AW supplies only 17 efficiency columns; the approved "
            "numeric plan must explicitly resolve and name an 18-category layout"
        )
    if efficiency.get("consistent_across_18_categories") is not True:
        raise WorkbookBuildError(
            "efficiency definition has not been approved as consistent across "
            "all 18 categories"
        )
    nonbill = value.get("nonbill_numeric_policy")
    if nonbill != required_nonbill_numeric_policy(manual_evidence_sha256):
        raise WorkbookBuildError(
            "numeric plan does not aggressively recompute the audited CFPB "
            "sign-on letters, keep CV/CX zero, and recompute AV/DX while "
            "preserving their one-decimal stored-value semantics"
        )
    input_hashes: dict[str, str] = {}
    inputs = value.get("inputs")
    if not isinstance(inputs, dict):
        raise WorkbookBuildError("numeric plan inputs are absent")
    for name, receipt_value in inputs.items():
        if not (
            isinstance(receipt_value, dict)
            and re.fullmatch(
                r"[0-9a-f]{64}",
                str(receipt_value.get("sha256") or "").casefold(),
            )
        ):
            raise WorkbookBuildError(
                f"numeric plan input receipt is invalid: {name}"
            )
        input_hashes[str(name)] = str(
            receipt_value["sha256"]
        ).casefold()
    if (
        input_hashes.get("accepted_relationship_ledger") != generation_id
        or input_hashes.get("manual_bill_evidence_csv")
        != manual_evidence_sha256
    ):
        raise WorkbookBuildError(
            "numeric plan input hashes do not match the accepted generation"
        )
    policy_receipt = value.get("computation_policy_approval")
    policy_hash = (
        str(policy_receipt.get("sha256") or "").casefold()
        if isinstance(policy_receipt, dict)
        else ""
    )
    if not re.fullmatch(r"[0-9a-f]{64}", policy_hash):
        raise WorkbookBuildError(
            "numeric plan computation-policy receipt is invalid"
        )
    basis = hashlib.sha256(
        canonical_json(
            {
                "generation_id": generation_id,
                "input_sha256": input_hashes,
                "policy_sha256": policy_hash,
                "measure_weight_policy": value["measure_weight_policy"],
                "writes": value["writes"],
            }
        ).encode("utf-8")
    ).hexdigest()
    if value.get("computation_basis_sha256") != basis:
        raise WorkbookBuildError(
            "numeric plan computation-basis hash mismatch"
        )
    return {"path": path, "value": value, "receipt": file_receipt(path)}


def validate_accepted_generation(directory: Path) -> dict[str, Any]:
    manifest_path = (directory / "manifest.json").resolve()
    manifest = read_json(manifest_path)
    policies = manifest.get("role_weight_policy_scenarios")
    selected = manifest.get("selected_role_weight_policy")
    decay = (manifest.get("decay_policy") or {}).get("weights")
    expected_decay = {str(key): value for key, value in EXPECTED_DECAY.items()}
    if not (
        manifest.get("schema") == ACCEPTED_MANIFEST_SCHEMA
        and manifest.get("status") == "complete"
        and manifest.get("generation_complete") is True
        and manifest.get("upstream_acceptance_passed") is True
        and manifest.get("staged_only") is True
        and manifest.get("production_artifacts_written") is False
        and manifest.get("promotion_ready") is True
        and manifest.get("role_weight_policy_reconciled") is True
        and manifest.get("selected_method_profile")
        == ROLE_METHOD_PROFILE_ID
        and manifest.get("consumer_role_methods") == ROLE_METHOD_PROFILE
        and isinstance(policies, dict)
        and selected in policies
        and decay == expected_decay
        and manifest.get("measure_weight_policy") == measure_weight_policy()
    ):
        raise WorkbookBuildError(
            "accepted generation is not complete, promotion-approved, and "
            "role-policy reconciled under 1/.66/.33/.16 decay"
        )
    legacy = policies.get(LEGACY_ARCHIVE_POLICY)
    if legacy != ARCHIVE_ROLE_WEIGHT:
        raise WorkbookBuildError(
            "accepted generation does not preserve archive legacy 3/2/1 weights"
        )
    ledger_hash = str(manifest.get("canonical_ledger_sha256") or "").casefold()
    ledger = find_materialized_by_hash(
        directory,
        ledger_hash,
        label="accepted canonical relationship ledger",
    )
    rows = read_ndjson(ledger)
    if manifest.get("canonical_ledger_row_count") != len(rows):
        raise WorkbookBuildError("accepted canonical ledger row count mismatch")
    seen: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if row.get("schema") != ACCEPTED_SCHEMA:
            raise WorkbookBuildError(f"accepted row {index} has wrong schema")
        measure_key = str(row.get("measure_key") or "")
        match = MEASURE_RE.fullmatch(measure_key)
        if not match:
            raise WorkbookBuildError(f"accepted row {index} has invalid measure key")
        congress = int(match.group(1))
        if row.get("congress") != congress:
            raise WorkbookBuildError(f"accepted row {index} Congress mismatch")
        if row.get("decay_weight") != EXPECTED_DECAY[congress]:
            raise WorkbookBuildError(f"accepted row {index} decay mismatch")
        if "measure_weight" not in row or "effective_weight" not in row:
            raise WorkbookBuildError(
                f"accepted row {index} lacks the manifest-bound measure weight"
            )
        role = row.get("role")
        if role not in ARCHIVE_ROLE_WEIGHT:
            raise WorkbookBuildError(f"accepted row {index} role is invalid")
        relationship = row.get("relationship_status")
        membership = row.get("membership_status")
        if relationship not in {"current", "withdrawn"}:
            raise WorkbookBuildError(
                f"accepted row {index} relationship status is invalid"
            )
        if membership not in {"included", "excluded"}:
            raise WorkbookBuildError(
                f"accepted row {index} membership status is invalid"
            )
        bioguide = str(row.get("member_bioguide_id") or "")
        if not BIOGUIDE_RE.fullmatch(bioguide):
            raise WorkbookBuildError(f"accepted row {index} Bioguide is invalid")
        category = canonical_category(
            row.get("category_label") or row.get("category")
        )
        natural = (measure_key, category, bioguide, relationship)
        if natural in seen:
            raise WorkbookBuildError(
                f"accepted canonical relationship duplicate: {natural}"
            )
        seen.add(natural)
        historical_authorized, historical_error = (
            historical_scoring_authorization(row)
        )
        if historical_error:
            raise WorkbookBuildError(
                f"accepted row {index} {historical_error}"
            )
        expected_eligible = (
            membership == "included"
            and relationship == "current"
            and row.get("scoring_category") is True
            and historical_authorized
        )
        if row.get("scoring_eligible") is not expected_eligible:
            raise WorkbookBuildError(
                f"accepted row {index} scoring eligibility is inconsistent"
            )
    approval = validate_role_policy_approval(
        directory,
        manifest,
        generation_id=ledger_hash,
        selected_policy=str(selected),
    )
    manual_evidence = validate_manual_bill_evidence(directory, manifest)
    numeric_plan = validate_numeric_recompute_plan(
        directory,
        manifest,
        generation_id=ledger_hash,
        selected_policy=str(selected),
        manual_evidence_sha256=manual_evidence["receipt"]["sha256"],
    )
    return {
        "manifest_path": manifest_path,
        "manifest": manifest,
        "manifest_receipt": file_receipt(manifest_path),
        "ledger": ledger,
        "ledger_receipt": file_receipt(ledger),
        "generation_id": ledger_hash,
        "rows": rows,
        "role_weight_policy_approval": approval,
        "manual_bill_evidence": manual_evidence,
        "numeric_recompute_plan": numeric_plan,
    }


def validate_direction_generation(
    directory: Path, generation_id: str
) -> dict[str, Any]:
    manifest_path = (directory / "manifest.json").resolve()
    manifest = read_json(manifest_path)
    if not (
        manifest.get("schema") == DIRECTION_MANIFEST_SCHEMA
        and manifest.get("status") == "PASS"
        and manifest.get("acceptance_passed") is True
        and manifest.get("production_write_prohibited") is True
    ):
        raise WorkbookBuildError("direction ledger is not a staged PASS generation")
    if generation_id not in recursive_sha_values(manifest.get("inputs")):
        raise WorkbookBuildError(
            "direction ledger is not hash-bound to the accepted generation"
        )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise WorkbookBuildError("direction manifest has no output receipts")
    receipt = outputs.get("scoring_directions.ndjson")
    if not isinstance(receipt, dict):
        receipt = outputs.get("scoring_directions")
    if not isinstance(receipt, dict):
        raise WorkbookBuildError("direction manifest lacks scoring directions")
    ledger = find_materialized_by_hash(
        directory,
        str(receipt.get("sha256") or ""),
        expected_size=(
            int(receipt["size"]) if receipt.get("size") is not None else None
        ),
        label="scoring direction ledger",
    )
    rows = read_ndjson(ledger)
    directions: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows, start=1):
        if row.get("schema") != DIRECTION_SCHEMA:
            raise WorkbookBuildError(f"direction row {index} has wrong schema")
        key = (
            str(row.get("canonical_key") or ""),
            canonical_category(row.get("category")),
        )
        try:
            sign = int(row.get("direction_sign"))
        except (TypeError, ValueError) as exc:
            raise WorkbookBuildError(
                f"direction row {index} has nonnumeric direction"
            ) from exc
        if sign not in {-1, 0, 1}:
            raise WorkbookBuildError(f"direction row {index} has invalid direction")
        if key in directions:
            raise WorkbookBuildError(f"duplicate scoring direction edge: {key}")
        directions[key] = sign
    return {
        "manifest_path": manifest_path,
        "manifest": manifest,
        "manifest_receipt": file_receipt(manifest_path),
        "ledger": ledger,
        "ledger_receipt": file_receipt(ledger),
        "rows": rows,
        "directions": directions,
    }


def validate_step_ledger(run_dir: Path, generation_id: str) -> dict[str, Any]:
    path = run_dir / "intermediate" / "shadow_step_receipts.json"
    value = read_json(path)
    completed = value.get("completed_steps")
    names = [
        row.get("name")
        for row in completed
        if isinstance(row, dict)
    ] if isinstance(completed, list) else []
    if not (
        value.get("schema") == STEP_LEDGER_SCHEMA
        and value.get("status") == "RUNNING"
        and value.get("staged_only") is True
        and value.get("production_written") is False
        and value.get("generation_id") == generation_id
        and isinstance(completed, list)
        and STEP_NAME not in names
    ):
        raise WorkbookBuildError(
            "shadow step ledger is absent, stale, complete, or already owns this step"
        )
    return {"path": path, "value": value, "receipt": file_receipt(path)}


def display_number(row: dict[str, Any]) -> str:
    existing = " ".join(str(row.get("display_number") or "").split())
    if existing:
        return existing
    match = MEASURE_RE.fullmatch(str(row["measure_key"]))
    assert match is not None
    prefix = {
        "hr": "H.R.",
        "s": "S.",
        "hres": "H.Res.",
        "sres": "S.Res.",
        "hjres": "H.J.Res.",
        "sjres": "S.J.Res.",
        "hconres": "H.Con.Res.",
        "sconres": "S.Con.Res.",
        "hamdt": "H.Amdt.",
        "samdt": "S.Amdt.",
    }[match.group(2)]
    return f"{prefix}{match.group(3)}"


def first_value(row: dict[str, Any], *paths: Sequence[str]) -> Any:
    for parts in paths:
        value: Any = row
        for key in parts:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value not in (None, "", []):
            return value
    return None


def committee_text(value: Any) -> str:
    if isinstance(value, list):
        values = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("name") or item.get("committee") or item
            text = " ".join(str(item or "").split())
            if text:
                values.append(text)
        return "; ".join(values)
    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {item}" for key, item in sorted(value.items())
        )
    return " ".join(str(value or "").split())


def relationship_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        canonical_category(row.get("category_label") or row.get("category")),
        str(row.get("member_name") or "").casefold(),
        str(row.get("measure_key") or ""),
        {"sponsor": 0, "original_cosponsor": 1, "cosponsor": 2}[row["role"]],
        0 if row["relationship_status"] == "current" else 1,
    )


def build_materialization(
    accepted_rows: list[dict[str, Any]],
    directions: dict[tuple[str, str], int],
    *,
    generation_id: str,
    selected_role_policy: str,
) -> dict[str, Any]:
    sponsors: dict[str, str] = {}
    sponsor_sets: dict[str, set[str]] = defaultdict(set)
    for row in accepted_rows:
        if row["role"] == "sponsor" and row["relationship_status"] == "current":
            sponsor_sets[row["measure_key"]].add(str(row["member_name"]))
    for measure, names in sorted(sponsor_sets.items()):
        if len(names) != 1:
            raise WorkbookBuildError(
                f"measure {measure} has {len(names)} current sponsors"
            )
        sponsors[measure] = next(iter(names))

    archive_matrix: list[list[Any]] = [ARCHIVE_HEADERS]
    interactions: dict[str, list[list[Any]]] = {
        sheet: [INTERACTION_HEADERS] for sheet in TARGET_MODEL_SHEETS
    }
    archive_leg: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    routing_rows: list[dict[str, Any]] = []
    missing_direction_edges: set[tuple[str, str]] = set()
    statuses: Counter[str] = Counter()

    for row in sorted(accepted_rows, key=relationship_sort_key):
        category = canonical_category(
            row.get("category_label") or row.get("category")
        )
        edge = (row["measure_key"], category)
        relationship_eligible = bool(row["scoring_eligible"])
        sign = directions.get(edge)
        if relationship_eligible and sign is None:
            missing_direction_edges.add(edge)
        sign_value = int(sign) if sign is not None else 0
        role_weight = ARCHIVE_ROLE_WEIGHT[row["role"]]
        decay = float(row["decay_weight"])
        measure_factor = measure_weight(row["measure_key"])
        if float(row["measure_weight"]) != measure_factor:
            raise WorkbookBuildError(
                f"{edge} accepted measure weight does not match measure type"
            )
        effective_weight = round(decay * measure_factor, 12)
        if float(row["effective_weight"]) != effective_weight:
            raise WorkbookBuildError(
                f"{edge} accepted effective weight is inconsistent"
            )
        if row["membership_status"] == "excluded":
            disposition = "excluded_membership"
            zero_reason = "membership_excluded"
        elif row["relationship_status"] == "withdrawn":
            disposition = "withdrawn_relationship"
            zero_reason = "relationship_withdrawn"
        elif row.get("scoring_category") is not True:
            disposition = "non_scoring_category"
            zero_reason = "category_non_scoring"
        elif sign is None:
            disposition = "missing_direction"
            zero_reason = "missing_direction"
        elif sign_value == 0:
            disposition = "neutral_direction"
            zero_reason = "direction_neutral"
        else:
            disposition = "selected"
            zero_reason = None
        contribution = (
            round(sign_value * role_weight * effective_weight, 12)
            if disposition == "selected"
            else 0.0
        )
        scoring_eligible = disposition == "selected"
        activity_eligible = disposition in {"selected", "neutral_direction"}
        scoring_weight = effective_weight if scoring_eligible else 0.0
        activity_weight = effective_weight if activity_eligible else 0.0
        tracker_status = " ".join(str(row.get("tracker_status") or "").split())
        if relationship_eligible and tracker_status not in ADVANCE_WEIGHT:
            raise WorkbookBuildError(
                f"{edge} lacks a recognized official tracker_status: "
                f"{tracker_status!r}"
            )
        sponsor = sponsors.get(row["measure_key"], "")
        if relationship_eligible and not sponsor:
            raise WorkbookBuildError(
                f"scoring relationship has no unique current sponsor: {edge}"
            )
        title = (
            row.get("official_title")
            or row.get("measure_title")
            or ""
        )
        summary = row.get("official_summary") or title
        detail_link = first_value(
            row,
            ("provenance", "membership", "official_source_url"),
            ("provenance", "membership", "congress_gov_url"),
        ) or ""
        base = [
            category,
            row["role"].replace("_", "-"),
            row["member_name"],
            display_number(row),
            title,
            sponsor,
            row.get("reported_cosponsor_count") or 0,
            committee_text(row.get("committees")),
            tracker_status,
            row["congress"],
            activity_weight,
            detail_link,
            summary,
        ]
        statuses[disposition] += 1
        routing_rows.append(
            {
                "schema": ROUTING_SCHEMA,
                "measure_key": row["measure_key"],
                "congress": row["congress"],
                "display_number": display_number(row),
                "category": category,
                "member": row["member_name"],
                "member_bioguide_id": row["member_bioguide_id"],
                "role": row["role"],
                "relationship_status": row["relationship_status"],
                "membership_status": row["membership_status"],
                "tracker_status": tracker_status,
                "direction": sign_value if sign is not None else None,
                "archive_role_weight": (
                    role_weight if scoring_eligible else 0.0
                ),
                "congress_decay_weight": decay,
                "measure_weight": measure_factor,
                "effective_weight": effective_weight,
                "decay_weight": scoring_weight,
                "activity_decay_weight": activity_weight,
                "scoring_decay_weight": scoring_weight,
                "contribution": contribution,
                "scoring_contribution": contribution,
                "activity_eligible": activity_eligible,
                "included_in_activity_workbooks": activity_eligible,
                "scoring_eligible": scoring_eligible,
                "disposition": disposition,
                "zero_weight_reason": zero_reason,
            }
        )
        if not activity_eligible:
            continue
        archive_matrix.append(base)
        member = str(row["member_name"])
        if scoring_eligible:
            archive_leg[member][category] += contribution
        sheet = {
            "sponsor": "Sponsor Interactions",
            "original_cosponsor": "Original Co-Sponsor Interaction",
            "cosponsor": "Co-Sponsor Interactions",
        }[row["role"]]
        interactions[sheet].append(base)
        if ADVANCE_WEIGHT[tracker_status] > 0:
            if row["role"] == "sponsor":
                interactions["Success Sponsor"].append(base)
            elif row["role"] == "original_cosponsor":
                interactions["Success Original Co-Sponsor"].append(base)

    if missing_direction_edges:
        examples = ", ".join(
            f"{measure}|{category}"
            for measure, category in sorted(missing_direction_edges)[:20]
        )
        raise WorkbookBuildError(
            f"{len(missing_direction_edges)} scoring edges lack directions: "
            f"{examples}"
        )
    archive_leg_value = {
        member: {
            category: round(value, 12)
            for category, value in sorted(categories.items())
            if value
        }
        for member, categories in sorted(archive_leg.items())
        if any(categories.values())
    }
    routing = {
        "schema": ROUTING_SCHEMA,
        "measure_weight_policy": measure_weight_policy(),
        "status": "PASS",
        "staged_only": True,
        "production_written": False,
        "generation_id": generation_id,
        "selected_role_weight_policy": selected_role_policy,
        "archive_role_weight_policy": {
            "name": LEGACY_ARCHIVE_POLICY,
            "weights": ARCHIVE_ROLE_WEIGHT,
        },
        "activity_policy": {
            "contract": "accepted-current-relevant-activity-v1",
            "included_dispositions": ["selected", "neutral_direction"],
            "neutral_direction_retained_as_activity": True,
            "neutral_direction_scoring_contribution": 0.0,
            "excluded_dispositions_are_audit_visible_only": [
                "excluded_membership",
                "withdrawn_relationship",
                "non_scoring_category",
                "missing_direction",
            ],
            "canonical_all_bills_column_count": len(ARCHIVE_HEADERS),
        },
        "advance_weight": ADVANCE_WEIGHT,
        "summary": {
            "accepted_relationship_rows": len(accepted_rows),
            "archive_rows": len(archive_matrix) - 1,
            "activity_rows": sum(
                1
                for item in routing_rows
                if item["included_in_activity_workbooks"]
            ),
            "scoring_rows": sum(
                1 for item in routing_rows if item["scoring_eligible"]
            ),
            "archive_leg_members": len(archive_leg_value),
            "dispositions": dict(sorted(statuses.items())),
            "interaction_rows": {
                sheet: len(rows) - 1
                for sheet, rows in interactions.items()
            },
        },
        "rows": routing_rows,
    }
    return {
        "archive_rows": archive_matrix,
        "model_sheets": interactions,
        "archive_leg": archive_leg_value,
        "routing_audit": routing,
    }


def artifact_runtime() -> tuple[Path, Path, int]:
    node_raw = os.environ.get("ARTIFACT_TOOL_NODE")
    modules_raw = os.environ.get("ARTIFACT_TOOL_NODE_MODULES")
    if not node_raw or not modules_raw:
        raise WorkbookBuildError(
            "ARTIFACT_TOOL_NODE and ARTIFACT_TOOL_NODE_MODULES are required"
        )
    node = Path(node_raw).resolve()
    modules = Path(modules_raw).resolve()
    if not node.is_file():
        raise WorkbookBuildError(f"artifact-tool Node executable is missing: {node}")
    if not modules.is_dir():
        raise WorkbookBuildError(
            f"artifact-tool node_modules directory is missing: {modules}"
        )
    try:
        heap = int(os.environ.get("ARTIFACT_TOOL_NODE_HEAP_MB", "12288"))
    except ValueError as exc:
        raise WorkbookBuildError("ARTIFACT_TOOL_NODE_HEAP_MB is not an integer") from exc
    if not 4096 <= heap <= 16384:
        raise WorkbookBuildError(
            "ARTIFACT_TOOL_NODE_HEAP_MB must be between 4096 and 16384"
        )
    return node, modules, heap


def run_artifact_helper(
    payload_path: Path,
    report_path: Path,
    *,
    cwd: Path,
) -> dict[str, Any]:
    node, modules, heap = artifact_runtime()
    if not JS_HELPER.is_file():
        raise WorkbookBuildError(f"artifact-tool helper is missing: {JS_HELPER}")
    environment = dict(os.environ)
    environment["ARTIFACT_TOOL_NODE_MODULES"] = str(modules)
    completed = subprocess.run(
        [
            str(node),
            f"--max-old-space-size={heap}",
            str(JS_HELPER),
            str(payload_path),
            str(report_path),
        ],
        cwd=cwd,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    (cwd / "artifact_tool.stdout.txt").write_text(
        completed.stdout, encoding="utf-8"
    )
    (cwd / "artifact_tool.stderr.txt").write_text(
        completed.stderr, encoding="utf-8"
    )
    if completed.returncode != 0:
        tail = completed.stderr[-4000:] or completed.stdout[-4000:]
        raise WorkbookBuildError(
            f"@oai/artifact-tool helper failed with exit "
            f"{completed.returncode}: {tail}"
        )
    report = read_json(report_path)
    if not (
        report.get("schema") == "accepted-legislation-artifact-tool-report-v1"
        and report.get("status") == "PASS"
        and report.get("engine") == "@oai/artifact-tool"
        and report.get("production_written") is False
        and report.get("required_sheets_present") is True
        and report.get("targeted_formula_error_count") == 0
    ):
        raise WorkbookBuildError("artifact-tool report is not a staged PASS")
    return report


def install_file(source: Path, destination: Path) -> None:
    if destination.exists():
        if destination.is_file() and sha256_file(destination) == sha256_file(source):
            return
        raise WorkbookBuildError(
            f"refusing to overwrite a different staged artifact: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def build_runtime(args: argparse.Namespace) -> dict[str, Any]:
    run_dir, input_root = validate_shadow_context(args.run_dir, args.input_root)
    baseline = verify_frozen_baseline(args.baseline_manifest, full=True)
    accepted = validate_accepted_generation(input_root / "accepted_generation")
    direction = validate_direction_generation(
        input_root / "direction", accepted["generation_id"]
    )
    step = validate_step_ledger(run_dir, accepted["generation_id"])
    materialized = build_materialization(
        accepted["rows"],
        direction["directions"],
        generation_id=accepted["generation_id"],
        selected_role_policy=accepted["manifest"][
            "selected_role_weight_policy"
        ],
    )
    intermediate = run_dir / "intermediate"
    intermediate.mkdir(exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(prefix="accepted_workbook_", dir=intermediate)
    )
    try:
        archive_leg_path = temp_dir / "archive_leg.json"
        routing_path = temp_dir / "legislation_routing_audit.json"
        write_json(archive_leg_path, materialized["archive_leg"])
        write_json(routing_path, materialized["routing_audit"])
        payload_path = temp_dir / "artifact_tool_payload.json"
        report_path = temp_dir / "artifact_tool_report.json"
        payload = {
            "schema": PREPARED_SCHEMA,
            "generation_id": accepted["generation_id"],
            "production_written": False,
            "baseline": {
                "model": str(baseline["model"]),
                "archive": str(baseline["archive"]),
            },
            "outputs": {
                "model": str(
                    temp_dir / "Moneyball Main Version - Blended.xlsx"
                ),
                "archive": str(
                    temp_dir / "congress_bills_archive_shadow.xlsx"
                ),
                "preview_dir": str(temp_dir / "previews"),
            },
            "archive": {
                "sheet": "All Bills",
                "rows": materialized["archive_rows"],
            },
            "model_sheets": materialized["model_sheets"],
        }
        write_json(payload_path, payload)
        artifact_report = run_artifact_helper(
            payload_path, report_path, cwd=temp_dir
        )
        targets = {
            "model": run_dir / "Moneyball Main Version - Blended.xlsx",
            "archive": run_dir / "congress_bills_archive_shadow.xlsx",
            "archive_leg": run_dir / "archive_leg.json",
            "routing_audit": run_dir / "legislation_routing_audit.json",
        }
        sources = {
            "model": Path(payload["outputs"]["model"]),
            "archive": Path(payload["outputs"]["archive"]),
            "archive_leg": archive_leg_path,
            "routing_audit": routing_path,
        }
        for name, destination in targets.items():
            source = sources[name]
            if not source.is_file():
                raise WorkbookBuildError(
                    f"materialized {name} output is missing: {source}"
                )
            install_file(source, destination)
        inspection_dir = intermediate / "accepted_workbook_inspection"
        if inspection_dir.exists():
            raise WorkbookBuildError(
                f"inspection output already exists: {inspection_dir}"
            )
        inspection_dir.mkdir()
        install_file(report_path, inspection_dir / "artifact_tool_report.json")
        for preview in sorted((temp_dir / "previews").glob("*.png")):
            install_file(preview, inspection_dir / preview.name)
        receipt_path = (
            intermediate / "accepted_legislation_workbook.receipt.json"
        )
        receipt = {
            "schema": SCHEMA,
            "status": "PASS",
            "staged_only": True,
            "production_written": False,
            "working_model_is_intermediate": True,
            "generation_id": accepted["generation_id"],
            "selected_role_weight_policy": accepted["manifest"][
                "selected_role_weight_policy"
            ],
            "selected_method_profile": accepted["manifest"][
                "selected_method_profile"
            ],
            "consumer_role_methods": accepted["manifest"][
                "consumer_role_methods"
            ],
            "role_weight_policy_reconciled": True,
            "archive_role_weight_policy": {
                "name": LEGACY_ARCHIVE_POLICY,
                "weights": ARCHIVE_ROLE_WEIGHT,
            },
            "advance_weight": ADVANCE_WEIGHT,
            "inputs": {
                "accepted_manifest": accepted["manifest_receipt"],
                "accepted_ledger": accepted["ledger_receipt"],
                "direction_manifest": direction["manifest_receipt"],
                "direction_ledger": direction["ledger_receipt"],
                "manual_bill_evidence": accepted[
                    "manual_bill_evidence"
                ]["receipt"],
                "step_ledger": step["receipt"],
                "frozen_baseline_manifest": baseline["manifest_receipt"],
                "frozen_model": baseline["model_receipt"],
                "frozen_archive": baseline["archive_receipt"],
                "verified_baseline_copy_count": baseline[
                    "verified_copy_count"
                ],
            },
            "role_weight_policy_approval": {
                **accepted["role_weight_policy_approval"]["receipt"],
                "path": accepted["role_weight_policy_approval"][
                    "path"
                ].relative_to(run_dir).as_posix(),
            },
            "numeric_recompute_plan": {
                **accepted["numeric_recompute_plan"]["receipt"],
                "path": accepted["numeric_recompute_plan"][
                    "path"
                ].relative_to(run_dir).as_posix(),
            },
            "legislative_efficiency_definition": accepted[
                "numeric_recompute_plan"
            ]["value"]["legislative_efficiency_definition"],
            "outputs": {
                "Moneyball Main Version - Blended.xlsx": file_receipt(
                    targets["model"], relative_to=run_dir
                ),
                "congress_bills_archive_shadow.xlsx": file_receipt(
                    targets["archive"], relative_to=run_dir
                ),
                "archive_leg.json": file_receipt(
                    targets["archive_leg"], relative_to=run_dir
                ),
                "legislation_routing_audit.json": file_receipt(
                    targets["routing_audit"], relative_to=run_dir
                ),
            },
            "inspection": {
                "artifact_tool_report": file_receipt(
                    inspection_dir / "artifact_tool_report.json",
                    relative_to=run_dir,
                ),
                "previews": [
                    file_receipt(path, relative_to=run_dir)
                    for path in sorted(inspection_dir.glob("*.png"))
                ],
                "engine": artifact_report["engine"],
                "targeted_formula_error_count": artifact_report.get(
                    "targeted_formula_error_count"
                ),
            },
            "counts": materialized["routing_audit"]["summary"],
            "producer": file_receipt(Path(__file__).resolve()),
            "helper": file_receipt(JS_HELPER),
        }
        write_json(receipt_path, receipt)
        return receipt
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def validate_capability_out(path: Path) -> Path:
    out = path.resolve()
    if not _is_relative_to(out, AUDIT_ROOT) or out == AUDIT_ROOT:
        raise WorkbookBuildError(
            "--smoke-capability-out must be under audits/legfix5"
        )
    return out


def run_capability_smoke(
    out_dir: Path, baseline_manifest: Path | None
) -> dict[str, Any]:
    out = validate_capability_out(out_dir)
    baseline = verify_frozen_baseline(baseline_manifest, full=True)
    report_path = out / "inspection.json"
    model_out = out / "smoke_output_workbook.xlsx"
    archive_out = out / "smoke_archive_workbook.xlsx"
    reusable = (
        out.is_dir()
        and report_path.is_file()
        and model_out.is_file()
        and archive_out.is_file()
    )
    if reusable:
        report = read_json(report_path)
        if not (
            report.get("schema")
            == "accepted-legislation-artifact-tool-report-v1"
            and report.get("status") == "PASS"
            and report.get("engine") == "@oai/artifact-tool"
            and report.get("production_written") is False
            and report.get("required_sheets_present") is True
            and report.get("targeted_formula_error_count") == 0
        ):
            raise WorkbookBuildError(
                "existing capability smoke artifacts are incomplete or invalid"
            )
    else:
        out.mkdir(parents=True, exist_ok=True)
        if any(out.iterdir()):
            raise WorkbookBuildError(
                f"partial smoke output directory is not reusable: {out}"
            )
        payload_path = out / "artifact_tool_payload.json"
        payload = {
            "schema": PREPARED_SCHEMA,
            "generation_id": "0" * 64,
            "production_written": False,
            "baseline": {
                "model": str(baseline["model"]),
                "archive": str(baseline["archive"]),
            },
            "outputs": {
                "model": str(model_out),
                "archive": str(archive_out),
                "preview_dir": str(out / "previews"),
            },
            "archive": {
                "sheet": "All Bills",
                "rows": [ARCHIVE_HEADERS],
            },
            "model_sheets": {
                sheet: [INTERACTION_HEADERS]
                for sheet in TARGET_MODEL_SHEETS
            },
        }
        write_json(payload_path, payload)
        report = run_artifact_helper(payload_path, report_path, cwd=out)
    receipt = {
        "schema": CAPABILITY_SCHEMA,
        "status": "PASS",
        "approved_engine": "@oai/artifact-tool",
        "staged_only": True,
        "production_written": False,
        "producer_sha256": sha256_file(Path(__file__).resolve()),
        "helper_sha256": sha256_file(JS_HELPER),
        "baseline_input_sha256": baseline["model_receipt"]["sha256"],
        "frozen_baseline_manifest": baseline["manifest_receipt"],
        "smoke_test": {
            "passed": True,
            "required_sheets_present": report.get(
                "required_sheets_present"
            ),
            "targeted_formula_error_count": report.get(
                "targeted_formula_error_count"
            ),
        },
        "runtime": {
            "artifact_tool_node": str(artifact_runtime()[0]),
            "artifact_tool_node_modules": str(artifact_runtime()[1]),
        },
        "output_workbook": file_receipt(model_out),
        "inspection": file_receipt(report_path),
        "archive_output": file_receipt(archive_out),
        "previews": [
            file_receipt(path)
            for path in sorted((out / "previews").glob("*.png"))
        ],
    }
    write_json(out.parent / "accepted_workbook_producer_capability.json", receipt)
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.smoke_capability_out:
            result = run_capability_smoke(
                args.smoke_capability_out, args.baseline_manifest
            )
        else:
            result = build_runtime(args)
    except (
        WorkbookBuildError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"ACCEPTED LEGISLATION WORKBOOK BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

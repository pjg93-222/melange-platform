# -*- coding: utf-8 -*-
"""Assemble a fully approved legislation generation in a staged audit directory.

This is a packaging and verification boundary, not a computation producer.  It
does not infer approvals, mutate the accepted ledger, author workbooks, or
write production paths.  Every input is validated before a temporary output
directory is created, and ``manifest.json`` is written only after every
declared artifact has been copied and hash-verified.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping, Sequence

from legislation import build_legislation_workbook_numeric_plan as NP

try:
    from legislation_measure_key import (
        CANONICAL_KEY_RE,
        measure_weight,
        measure_weight_policy,
    )
except ImportError:  # pragma: no cover - package import path
    from fec.legislation_measure_key import (
        CANONICAL_KEY_RE,
        measure_weight,
        measure_weight_policy,
    )


MANIFEST_SCHEMA = "accepted-legislation-generation-manifest-v1"
DIRECTION_MANIFEST_SCHEMA = "legislation-direction-ledger-manifest-v1"
LEDGER_SCHEMA = "accepted-legislation-relationship-v1"
DIRECTION_SCHEMA = "legislation-direction-decision-v1"
PLAN_SCHEMA = "legislation-workbook-numeric-write-plan-v1"
POLICY_SCHEMA = "legislation-workbook-computation-policy-approval-v1"
LAYOUT_SCHEMA = "legislation-workbook-audited-member-layout-v1"

CODE_ROOT = Path(__file__).resolve().parents[2]
AUDIT_ROOT = (CODE_ROOT / "audits" / "legfix5").resolve()
HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
BIOGUIDE_RE = re.compile(r"^[A-Z]\d{6}$")
# One shared definition; the Congress window is the single named constant
# ``legislation_measure_key.LAST_SUPPORTED_CONGRESS``.
MEASURE_RE = CANONICAL_KEY_RE
# EXPECTED_DECAY is a user-owned value map, not a derived constant: seating the
# 120th Congress needs an explicit entry here as well as the one-line bump in
# legislation_measure_key.LAST_SUPPORTED_CONGRESS.
EXPECTED_DECAY = {116: 0.16, 117: 0.33, 118: 0.66, 119: 1.0}
NON_AUTHORITATIVE_PATH_TOKENS = (
    "/billstatus_bulk_enrichment/review_sidecars/",
    "/billstatus_bulk_enrichment/structured_relevance_sidecars/",
    "/partial_review_prefill/",
    "/partial_review_queue/",
)
NON_AUTHORITATIVE_TRUE_FLAGS = (
    "review_prefill_only",
    "proposal_only",
    "partial_discovery_only",
)
NON_AUTHORITATIVE_FALSE_FLAGS = (
    "downstream_consumption_allowed",
    "decision_import_allowed",
    "scoring_input_allowed",
    "membership_disposition_authority",
    "ratification_authority",
    "scoring_authority",
    "usable_as_membership_review_ledger",
)
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
ARCHIVE_POLICY = "archive_legacy_3_2_1"
ARCHIVE_WEIGHTS = {
    "sponsor": 3.0,
    "original_cosponsor": 2.0,
    "cosponsor": 1.0,
}
OUTPUT_NAMES = {
    "canonical_ledger": "accepted_legislation_relationships.ndjson",
    "direction_ledger": "scoring_directions.ndjson",
    "role_weight_policy_approval": "role_weight_policy_approval.json",
    "computation_policy_approval": "computation_policy_approval.json",
    "manual_bill_evidence": "manual_bill_evidence.csv",
    "audited_layout": "audited_member_layout.json",
    "numeric_recompute_plan": "legislation_workbook_recompute_plan.json",
    "source_accepted_manifest": "source_accepted_generation_manifest.json",
    "source_direction_manifest": "source_direction_manifest.json",
}


class AssemblyError(RuntimeError):
    """The supplied generation cannot be promoted into an approved package."""


def non_authoritative_markers(value: Mapping[str, Any]) -> list[str]:
    markers = [
        name
        for name in NON_AUTHORITATIVE_TRUE_FLAGS
        if value.get(name) is True
    ]
    markers.extend(
        name
        for name in NON_AUTHORITATIVE_FALSE_FLAGS
        if value.get(name) is False
    )
    if str(value.get("ratification_state") or "").strip().upper() == (
        "NOT_RATIFIED"
    ):
        markers.append("ratification_state=NOT_RATIFIED")
    return sorted(markers)


def contains_non_authoritative_path(value: Any) -> bool:
    if isinstance(value, str):
        normalized = "/" + value.replace("\\", "/").casefold().lstrip("/")
        return any(
            token in normalized for token in NON_AUTHORITATIVE_PATH_TOKENS
        )
    if isinstance(value, Mapping):
        return any(contains_non_authoritative_path(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return any(contains_non_authoritative_path(item) for item in value)
    return False


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": resolved.name,
        "size": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def source_receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


READ_SHA256: dict[Path, str] = {}


def read_object(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        READ_SHA256[path.resolve()] = hashlib.sha256(raw).hexdigest()
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssemblyError(f"invalid JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AssemblyError(f"JSON root is not an object: {path}")
    return value


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise AssemblyError(f"cannot read NDJSON {path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssemblyError(
                f"invalid NDJSON {path}:{number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise AssemblyError(f"non-object NDJSON row {path}:{number}")
        rows.append(value)
    return rows


def recursive_sha_values(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                isinstance(item, str)
                and "sha256" in str(key).casefold()
                and HEX_RE.fullmatch(item)
            ):
                result.add(item.casefold())
            result.update(recursive_sha_values(item))
    elif isinstance(value, list):
        for item in value:
            result.update(recursive_sha_values(item))
    return result


def require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise AssemblyError(f"required {label} is missing: {resolved}")
    return resolved


def historical_scoring_authorization(
    row: Mapping[str, Any],
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


def normalized_category(value: Any) -> str:
    try:
        return NP.category(value)
    except NP.NumericPlanError as exc:
        raise AssemblyError(str(exc)) from exc


def validate_accepted(
    manifest_path: Path, ledger_path: Path
) -> dict[str, Any]:
    manifest = read_object(manifest_path)
    authority_markers = non_authoritative_markers(manifest)
    if authority_markers:
        raise AssemblyError(
            "accepted generation carries proposal-only authority markers: "
            + ", ".join(authority_markers)
        )
    if contains_non_authoritative_path(manifest.get("input_paths")):
        raise AssemblyError(
            "accepted generation is directly bound to a non-authoritative "
            "review/relevance/prefill/queue sidecar"
        )
    if not (
        manifest.get("schema") == MANIFEST_SCHEMA
        and str(manifest.get("status") or "").casefold()
        in {"complete", "pass", "passed"}
        and manifest.get("generation_complete") is True
        and manifest.get("upstream_acceptance_passed") is True
        and manifest.get("staged_only") is True
        and manifest.get("production_artifacts_written") is False
        and int(manifest.get("membership_unresolved_count") or 0) == 0
        and int(manifest.get("relationship_unresolved_measure_count") or 0)
        == 0
    ):
        raise AssemblyError(
            "accepted generation is not a complete staged upstream PASS"
        )
    expected_decay = {str(key): value for key, value in EXPECTED_DECAY.items()}
    if (manifest.get("decay_policy") or {}).get("weights") != expected_decay:
        raise AssemblyError("accepted generation does not use 1/.66/.33/.16")
    if manifest.get("measure_weight_policy") != measure_weight_policy():
        raise AssemblyError(
            "accepted generation lacks the original-method measure-weight policy"
        )
    generation_id = sha256_file(ledger_path)
    if (
        str(manifest.get("canonical_ledger_sha256") or "").casefold()
        != generation_id
    ):
        raise AssemblyError("accepted canonical ledger hash mismatch")
    declared_name = str(manifest.get("canonical_ledger_file") or "")
    if declared_name and Path(declared_name).name != ledger_path.name:
        raise AssemblyError("explicit accepted ledger differs from manifest")
    rows = read_ndjson(ledger_path)
    if manifest.get("canonical_ledger_row_count") != len(rows):
        raise AssemblyError("accepted canonical ledger row count mismatch")
    if (
        manifest.get("canonical_ledger_schema")
        and manifest.get("canonical_ledger_schema") != LEDGER_SCHEMA
    ):
        raise AssemblyError("accepted canonical ledger schema mismatch")
    seen: set[tuple[str, str, str, str]] = set()
    scoring_edges: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if row.get("schema") != LEDGER_SCHEMA:
            raise AssemblyError(f"accepted row {index} schema mismatch")
        row_markers = non_authoritative_markers(row)
        if row_markers:
            raise AssemblyError(
                f"accepted row {index} carries proposal-only authority "
                "markers: " + ", ".join(row_markers)
            )
        measure = str(row.get("measure_key") or "")
        match = MEASURE_RE.fullmatch(measure)
        if not match:
            raise AssemblyError(f"accepted row {index} measure key invalid")
        congress = int(match.group(1))
        if (
            row.get("congress") != congress
            or row.get("decay_weight") != EXPECTED_DECAY[congress]
        ):
            raise AssemblyError(
                f"accepted row {index} Congress/decay mismatch"
            )
        expected_measure_weight = measure_weight(measure)
        expected_effective_weight = round(
            EXPECTED_DECAY[congress] * expected_measure_weight,
            12,
        )
        if (
            row.get("measure_weight") != expected_measure_weight
            or row.get("effective_weight") != expected_effective_weight
        ):
            raise AssemblyError(
                f"accepted row {index} measure-weight policy mismatch"
            )
        role = row.get("role")
        membership = row.get("membership_status")
        relationship = row.get("relationship_status")
        bioguide = str(row.get("member_bioguide_id") or "")
        category = normalized_category(
            row.get("category_label") or row.get("category")
        )
        if (
            role not in ARCHIVE_WEIGHTS
            or membership not in {"included", "excluded"}
            or relationship not in {"current", "withdrawn"}
            or not BIOGUIDE_RE.fullmatch(bioguide)
        ):
            raise AssemblyError(f"accepted row {index} relationship invalid")
        natural = (measure, category, bioguide, relationship)
        if natural in seen:
            raise AssemblyError(f"accepted row {index} duplicates {natural}")
        seen.add(natural)
        historical_authorized, historical_error = (
            historical_scoring_authorization(row)
        )
        if historical_error:
            raise AssemblyError(
                f"accepted row {index} {historical_error}"
            )
        eligible = bool(
            membership == "included"
            and relationship == "current"
            and row.get("scoring_category") is True
            and historical_authorized
        )
        if row.get("scoring_eligible") is not eligible:
            raise AssemblyError(
                f"accepted row {index} scoring eligibility inconsistent"
            )
        if eligible:
            scoring_edges.add((measure, category))
    scenarios = manifest.get("role_weight_policy_scenarios")
    if not (
        isinstance(scenarios, dict)
        and scenarios.get(ARCHIVE_POLICY) == ARCHIVE_WEIGHTS
    ):
        raise AssemblyError("accepted generation lost archive legacy 3/2/1")
    return {
        "manifest": manifest,
        "rows": rows,
        "generation_id": generation_id,
        "scoring_edges": scoring_edges,
        "role_scenarios": scenarios,
        "source_coverage_hashes": recursive_sha_values(
            {
                "input_hashes": manifest.get("input_hashes"),
                "coverage_bindings": manifest.get("coverage_bindings"),
                "coverage": manifest.get("coverage"),
            }
        ),
    }


def declared_direction_receipt(
    manifest: Mapping[str, Any],
) -> Mapping[str, Any]:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise AssemblyError("direction manifest has no outputs")
    item = outputs.get("scoring_directions.ndjson")
    if not isinstance(item, dict):
        item = outputs.get("scoring_directions")
    if not isinstance(item, dict):
        raise AssemblyError("direction manifest lacks scoring directions")
    return item


def validate_direction(
    manifest_path: Path,
    ledger_path: Path,
    *,
    generation_id: str,
    required_edges: set[tuple[str, str]],
) -> dict[str, Any]:
    manifest = read_object(manifest_path)
    if not (
        manifest.get("schema") == DIRECTION_MANIFEST_SCHEMA
        and manifest.get("status") == "PASS"
        and manifest.get("acceptance_passed") is True
        and manifest.get("production_write_prohibited") is True
    ):
        raise AssemblyError("direction generation is not a staged PASS")
    if generation_id not in recursive_sha_values(manifest.get("inputs")):
        raise AssemblyError(
            "direction generation is not bound to the accepted ledger"
        )
    declared = declared_direction_receipt(manifest)
    actual_hash = sha256_file(ledger_path)
    if str(declared.get("sha256") or "").casefold() != actual_hash:
        raise AssemblyError("direction ledger hash differs from its manifest")
    if (
        declared.get("size") is not None
        and int(declared["size"]) != ledger_path.stat().st_size
    ):
        raise AssemblyError("direction ledger size differs from its manifest")
    directions: dict[tuple[str, str], int] = {}
    rows = read_ndjson(ledger_path)
    for index, row in enumerate(rows, start=1):
        if row.get("schema") != DIRECTION_SCHEMA:
            raise AssemblyError(f"direction row {index} schema mismatch")
        key = (
            str(row.get("canonical_key") or ""),
            normalized_category(row.get("category")),
        )
        try:
            sign = int(row.get("direction_sign"))
        except (TypeError, ValueError) as exc:
            raise AssemblyError(
                f"direction row {index} has invalid direction"
            ) from exc
        if sign not in {-1, 0, 1} or key in directions:
            raise AssemblyError(f"direction row {index} invalid/duplicate")
        directions[key] = sign
    missing = sorted(required_edges - set(directions))
    if missing:
        raise AssemblyError(
            f"direction ledger misses {len(missing)} accepted scoring edges"
        )
    return {
        "manifest": manifest,
        "rows": rows,
        "sha256": actual_hash,
        "directions": directions,
    }


def validate_role_approval(
    path: Path,
    *,
    generation_id: str,
    scenarios: Mapping[str, Any],
) -> dict[str, Any]:
    value = read_object(path)
    selected = str(value.get("selected_role_weight_policy") or "")
    if not (
        value.get("schema") == ROLE_APPROVAL_SCHEMA
        and value.get("status") == "APPROVED"
        and value.get("human_approved") is True
        and value.get("approved_by_user") is True
        and str(value.get("accepted_ledger_sha256") or "").casefold()
        == generation_id
        and value.get("selected_method_profile")
        == ROLE_METHOD_PROFILE_ID
        and value.get("selected_policy") == ROLE_METHOD_PROFILE_ID
        and value.get("consumer_methods") == ROLE_METHOD_PROFILE
        and value.get("applies_to") == list(ROLE_METHOD_PROFILE)
        and selected in scenarios
        and selected in NP.ROLE_WEIGHTS
        and selected == "workbook_legacy_4_2_1"
        and scenarios[selected] == NP.ROLE_WEIGHTS[selected]
    ):
        raise AssemblyError(
            "role-weight approval is absent, inferred, stale, or inconsistent"
        )
    return {"value": value, "selected": selected, "sha256": sha256_file(path)}


def validate_layout(path: Path) -> dict[str, Any]:
    value = read_object(path)
    source_hash = str(value.get("source_workbook_sha256") or "").casefold()
    if not (
        value.get("schema") == LAYOUT_SCHEMA
        and value.get("status") == "PASS"
        and value.get("engine") == "@oai/artifact-tool"
        and value.get("xlsx_opened_by_python") is False
        and value.get("production_written") is False
        and value.get("audit_complete") is True
        and HEX_RE.fullmatch(source_hash)
        and value.get("raw_block_ranges") == NP.BLOCK_RANGES
        and value.get("sublayer_category_columns")
        == NP.CATEGORY_SUBLAYER_COLUMNS
    ):
        raise AssemblyError("audited member/layout artifact is not a PASS")
    try:
        press_alignment = NP.press_alignment_facts(
            value.get("press_literal_row_alignment")
        )
    except NP.NumericPlanError as exc:
        raise AssemblyError(str(exc)) from exc
    try:
        members, _ = NP.validate_members(value)
    except NP.NumericPlanError as exc:
        raise AssemblyError(str(exc)) from exc
    nonleg = value.get("sublayer_nonlegislative_values")
    weights = value.get("sublayer_legislative_weights")
    if not isinstance(nonleg, dict) or not isinstance(weights, dict):
        raise AssemblyError("audited layout lacks extracted sublayer inputs")
    for category in NP.CATEGORIES:
        values = nonleg.get(category)
        category_weights = weights.get(category)
        if not isinstance(values, list) or len(values) != 533:
            raise AssemblyError(
                f"audited nonlegislative vector invalid: {category}"
            )
        if not isinstance(category_weights, list) or len(category_weights) != 5:
            raise AssemblyError(
                f"audited legislative weights invalid: {category}"
            )
        for item in [*values, *category_weights]:
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
            ):
                raise AssemblyError(
                    f"audited sublayer input is nonnumeric: {category}"
                )
    slots = value.get("efficiency_slots")
    if not isinstance(slots, list) or len(slots) != 17:
        raise AssemblyError("audited layout lacks the explicit 17 slots")
    normalized: list[dict[str, Any]] = []
    for column, slot in zip(NP.EFFICIENCY_COLUMNS, slots):
        if not isinstance(slot, dict) or slot.get("column") != column:
            raise AssemblyError(f"audited efficiency slot {column} invalid")
        status = slot.get("status")
        categories = slot.get("categories")
        candidates = slot.get("category_candidates")
        if status is None and isinstance(categories, list) and categories:
            status = "RESOLVED_IN_AUDITED_LAYOUT"
            candidates = list(categories)
        if not (
            status
            in {
                "UNRESOLVED",
                "IDENTIFIED_FROM_WORKBOOK",
                "RESOLVED_IN_AUDITED_LAYOUT",
            }
            and isinstance(candidates, list)
            and all(item in NP.CATEGORIES for item in candidates)
            and (
                status != "IDENTIFIED_FROM_WORKBOOK"
                or len(candidates) == 1
            )
        ):
            raise AssemblyError(
                f"audited efficiency slot {column} facts invalid"
            )
        normalized.append(
            {
                "column": column,
                "status": status,
                "category_candidates": list(candidates),
                "evidence": slot.get("evidence") or {},
            }
        )
    return {
        "value": value,
        "members": members,
        "slots": normalized,
        "press_literal_row_alignment": value.get(
            "press_literal_row_alignment"
        ),
        "press_alignment_facts": press_alignment,
        "sha256": sha256_file(path),
        "source_workbook_sha256": source_hash,
    }


def numeric_matrix(value: Any, rows: int, columns: int, label: str) -> None:
    if not isinstance(value, list) or len(value) != rows:
        raise AssemblyError(f"{label} row count mismatch")
    for row in value:
        if not isinstance(row, list) or len(row) != columns:
            raise AssemblyError(f"{label} column count mismatch")
        for item in row:
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
            ):
                raise AssemblyError(f"{label} contains a nonnumeric cell")


def column_number(label: str) -> int:
    value = 0
    for character in label:
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def range_shape(cell_range: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", cell_range)
    if not match:
        raise AssemblyError(f"invalid numeric-plan range: {cell_range}")
    first_column, first_row, last_column, last_row = match.groups()
    return (
        int(last_row) - int(first_row) + 1,
        column_number(last_column) - column_number(first_column) + 1,
    )


def required_write_keys() -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for sheet in (
        "Legislative Score - Raw",
        "Legislative Score - Normalized",
    ):
        result.update((sheet, value) for value in NP.BLOCK_RANGES.values())
    for sheet in ("Sub Layer - Raw", "Sub Layer - Normalized"):
        result.update(
            (sheet, f"{column}4:{column}536")
            for column in NP.CATEGORY_SUBLAYER_COLUMNS.values()
        )
        result.add((sheet, "AG4:AW536"))
    return result


def plan_input_hashes(
    plan: Mapping[str, Any],
    *,
    expected: Mapping[str, str],
) -> dict[str, str]:
    inputs = plan.get("inputs")
    if not isinstance(inputs, dict):
        raise AssemblyError("numeric plan has no input receipts")
    result: dict[str, str] = {}
    for label in (
        "accepted_relationship_ledger",
        "scoring_direction_ledger",
        "manual_bill_evidence_csv",
        "audited_member_layout",
        "frozen_baseline_manifest",
    ):
        item = inputs.get(label)
        sha = (
            str(item.get("sha256") or "").casefold()
            if isinstance(item, dict)
            else ""
        )
        if not HEX_RE.fullmatch(sha):
            raise AssemblyError(f"numeric plan input receipt invalid: {label}")
        result[label] = sha
    for label, sha in expected.items():
        if result.get(label) != sha:
            raise AssemblyError(f"numeric plan input hash mismatch: {label}")
    return result


def validate_efficiency_layout(
    plan: Mapping[str, Any],
    slots: Sequence[Mapping[str, Any]],
    cu_tax_treatment: Mapping[str, Any],
) -> None:
    layout = plan.get("efficiency_layout")
    if not (
        isinstance(layout, dict)
        and layout.get("physical_column_count") == 17
        and layout.get("category_count") == 18
        and layout.get("legacy_materialized_category_count")
        == sum(len(slot["categories"]) for slot in slots)
        and layout.get("slots") == list(slots)
        and layout.get("cu_tax_efficiency_treatment")
        == dict(cu_tax_treatment)
        and layout.get("raw_range") == "Sub Layer - Raw!AG4:AW536"
        and layout.get("normalized_range")
        == "Sub Layer - Normalized!AG4:AW536"
    ):
        raise AssemblyError(
            "numeric plan lacks the approved machine-readable 17/18 layout"
        )
    mappings = layout.get("category_mappings")
    if not (
        isinstance(mappings, dict)
        and set(mappings) == set(NP.CATEGORIES)
        and len(mappings) == 18
    ):
        raise AssemblyError("numeric plan efficiency mappings are incomplete")
    slot_by_category = {
        category: slot
        for slot in slots
        for category in slot["categories"]
    }
    for category in NP.CATEGORIES:
        item = mappings.get(category)
        slot = slot_by_category.get(category)
        if (
            category == NP.CU_TAX_CATEGORY
            and cu_tax_treatment.get("selected_treatment")
            == NP.CU_TAX_PRESERVE_OMISSION
        ):
            if item != {
                "sheet": "Legislative Score - Raw",
                "range": NP.CU_TAX_RAW_GENERIC_RANGE,
                "normalized_sheet": "Legislative Score - Normalized",
                "normalized_range": NP.CU_TAX_NORMALIZED_GENERIC_RANGE,
                "column": "FB",
                "aggregation": "identity",
                "aggregate_with": [],
                "source_metric": "generic_success_rate",
                "materialization": "outside_legacy_efficiency_surface",
            }:
                raise AssemblyError(
                    "numeric plan CU Tax efficiency mapping is invalid"
                )
            continue
        if slot is None:
            raise AssemblyError(
                f"numeric plan efficiency mapping has no slot: {category}"
            )
        peers = [
            peer for peer in slot["categories"] if peer != category
        ]
        if not (
            isinstance(item, dict)
            and item.get("column") == slot["column"]
            and item.get("range")
            == f"{slot['column']}4:{slot['column']}536"
            and item.get("aggregation") == slot["aggregation"]
            and item.get("aggregate_with") == peers
        ):
            raise AssemblyError(
                f"numeric plan efficiency mapping invalid: {category}"
            )


def validate_plan(
    path: Path,
    *,
    generation_id: str,
    selected_policy: str,
    accepted_hash: str,
    direction_hash: str,
    manual_hash: str,
    layout: Mapping[str, Any],
    computation_policy_path: Path,
) -> dict[str, Any]:
    value = read_object(path)
    efficiency = value.get("legislative_efficiency_definition")
    if not (
        value.get("schema") == PLAN_SCHEMA
        and value.get("status") == "PASS"
        and value.get("generation_id") == generation_id
        and value.get("selected_role_weight_policy") == selected_policy
        and value.get("role_weight_policy_reconciled") is True
        and value.get("workbook_role_weights_verified") is True
        and value.get("measure_weight_policy") == measure_weight_policy()
        and value.get("all_18_categories_recomputed") is True
        and value.get("categories") == list(NP.CATEGORIES)
        and value.get("unexplained_cell_count") == 0
        and value.get("staged_only") is True
        and value.get("production_written") is False
        and value.get("xlsx_opened_or_authored_by_python") is False
        and isinstance(efficiency, dict)
        and efficiency.get("status") == "APPROVED"
        and efficiency.get("approved_by_user") is True
        and efficiency.get("consistent_across_18_categories") is True
        and efficiency.get("resolved_category_count") == 18
        and efficiency.get("legacy_efficiency_column_count") == 17
        and str(efficiency.get("layout_resolution") or "").strip()
    ):
        raise AssemblyError("numeric recompute plan is not a complete PASS")
    hashes = plan_input_hashes(
        value,
        expected={
            "accepted_relationship_ledger": accepted_hash,
            "scoring_direction_ledger": direction_hash,
            "manual_bill_evidence_csv": manual_hash,
            "audited_member_layout": layout["sha256"],
        },
    )
    policy_hash = sha256_file(computation_policy_path)
    approval_receipt = value.get("computation_policy_approval")
    if not (
        isinstance(approval_receipt, dict)
        and str(approval_receipt.get("sha256") or "").casefold()
        == policy_hash
    ):
        raise AssemblyError(
            "numeric plan is not bound to the computation approval"
        )
    frozen = value.get("frozen_workbook_binding")
    if not (
        isinstance(frozen, dict)
        and isinstance(frozen.get("manifest"), dict)
        and frozen["manifest"].get("sha256")
        == hashes["frozen_baseline_manifest"]
        and isinstance(frozen.get("workbook"), dict)
        and frozen["workbook"].get("sha256")
        == layout["source_workbook_sha256"]
    ):
        raise AssemblyError("numeric plan frozen workbook binding mismatch")
    if value.get("nonbill_numeric_policy") != NP.required_nonbill_numeric_policy(
        manual_hash
    ):
        raise AssemblyError("numeric plan nonbill policy is stale or wrong")
    policy_value = read_object(computation_policy_path)
    try:
        press_alignment = NP.normalize_press_member_alignment_policy(
            (
                policy_value.get("layout_policy") or {}
            ).get("press_member_alignment"),
            layout_sha256=layout["sha256"],
            audited_alignment=layout["press_literal_row_alignment"],
        )
    except NP.NumericPlanError as exc:
        raise AssemblyError(str(exc)) from exc
    if value.get("press_member_alignment") != press_alignment:
        raise AssemblyError(
            "numeric plan press-member alignment is stale or wrong"
        )
    try:
        cfpb_routing = NP.normalize_cfpb_policy(
            policy_value.get("cfpb_policy")
        )
    except NP.NumericPlanError as exc:
        raise AssemblyError(str(exc)) from exc
    if value.get("cfpb_accepted_bill_routing") != cfpb_routing:
        raise AssemblyError(
            "numeric plan CFPB accepted-bill routing is stale or wrong"
        )
    accepted_audit = (value.get("audit") or {}).get("accepted") or {}
    special_columns = (value.get("audit") or {}).get(
        "special_columns"
    ) or {}
    if not (
        (
            cfpb_routing["sponsor_original_route"]["selected_route"]
            is not None
            or accepted_audit.get(
                "accepted_cfpb_sponsor_original_bill_events"
            )
            == 0
        )
        and special_columns.get("CU_source")
        == "manual_LTR sponsor rows only"
        and special_columns.get("CW_source")
        == "manual_LTR original-cosponsor rows only"
        and special_columns.get("CV_all_zero") is True
        and special_columns.get("CX_all_zero") is True
    ):
        raise AssemblyError(
            "numeric plan CFPB route/legacy-column evidence is incomplete"
        )
    if (value.get("decay_policy") or {}).get("weights") != {
        str(key): weight for key, weight in EXPECTED_DECAY.items()
    }:
        raise AssemblyError("numeric plan decay policy mismatch")
    try:
        cu_tax_treatment = NP.normalize_cu_tax_efficiency_treatment(
            (value.get("efficiency_layout") or {}).get(
                "cu_tax_efficiency_treatment"
            ),
            layout["slots"],
        )
        resolved_slots = NP.normalize_resolved_efficiency_slots(
            (value.get("efficiency_layout") or {}).get("slots"),
            layout["slots"],
            cu_tax_treatment,
        )
    except NP.NumericPlanError as exc:
        raise AssemblyError(str(exc)) from exc
    validate_efficiency_layout(value, resolved_slots, cu_tax_treatment)
    writes = value.get("writes")
    if not isinstance(writes, list):
        raise AssemblyError("numeric plan has no writes")
    keys: list[tuple[str, str]] = []
    for index, write in enumerate(writes, start=1):
        if not isinstance(write, dict):
            raise AssemblyError(f"numeric write {index} is invalid")
        sheet = str(write.get("sheet") or "")
        cell_range = str(write.get("range") or "").upper()
        rows, columns = range_shape(cell_range)
        numeric_matrix(
            write.get("values"), rows, columns, f"numeric write {index}"
        )
        keys.append((sheet, cell_range))
    if len(keys) != len(set(keys)) or set(keys) != required_write_keys():
        raise AssemblyError("numeric plan range coverage is not exact")
    basis = hashlib.sha256(
        canonical_bytes(
            {
                "generation_id": generation_id,
                "input_sha256": hashes,
                "policy_sha256": policy_hash,
                "measure_weight_policy": value["measure_weight_policy"],
                "writes": writes,
            }
        )
    ).hexdigest()
    if value.get("computation_basis_sha256") != basis:
        raise AssemblyError("numeric plan computation-basis hash mismatch")
    return {
        "value": value,
        "input_hashes": hashes,
        "sha256": sha256_file(path),
        "resolved_efficiency_slots": resolved_slots,
        "cu_tax_efficiency_treatment": cu_tax_treatment,
        "press_member_alignment": press_alignment,
    }


def validate_computation_policy(
    path: Path,
    *,
    plan: Mapping[str, Any],
    generation_id: str,
    selected_policy: str,
    input_hashes: Mapping[str, str],
    layout: Mapping[str, Any],
) -> dict[str, Any]:
    value = read_object(path)
    if not (
        value.get("schema") == POLICY_SCHEMA
        and value.get("status") == "APPROVED"
        and value.get("approved_by_user") is True
        and value.get("generation_id") == generation_id
        and value.get("input_sha256") == dict(input_hashes)
        and value.get("selected_role_weight_policy") == selected_policy
        and value.get("role_weight_policy_reconciled") is True
        and value.get("legislative_efficiency_definition")
        == plan["legislative_efficiency_definition"]
    ):
        raise AssemblyError(
            "computation approval is absent, inferred, stale, or inconsistent"
        )
    role = value.get("role_policy")
    if not (
        isinstance(role, dict)
        and role.get("weights") == NP.ROLE_WEIGHTS[selected_policy]
        and role.get("raw_activity_encoding")
        == "signed_sum_congress_decay"
        and role.get("average_encoding")
        == "signed_mean_congress_decay_per_relationship"
        and role.get("workbook_row2_applies_role_weights") is True
    ):
        raise AssemblyError("computation role policy is inconsistent")
    efficiency = value.get("legislative_efficiency_definition")
    required_efficiency = {
        "status": "APPROVED",
        "approved_by_user": True,
        "consistent_across_18_categories": True,
        "definition_id": "uniform-decay-weighted-advanced-rate-v1",
        "numerator": "decay-weighted accepted advanced relationships",
        "denominator": "decay-weighted accepted scoring opportunities",
        "empty_role_handling": "explicit zero",
        "success_action_rule": "tracker_status in approved advanced set",
        "weighting_basis": "canonical Congress decay",
        "opportunity_roles": ["sponsor", "original_cosponsor"],
        "generic_rate": "pooled_sponsor_and_original",
        "scale": "percentage_points",
        "precision_decimals": 1,
        "ltr_success_handling":
            "activity_only_excluded_from_efficiency",
        "legacy_efficiency_column_count": 17,
        "resolved_category_count": 18,
    }
    if not isinstance(efficiency, dict):
        raise AssemblyError("computation efficiency policy is absent")
    for key, expected in required_efficiency.items():
        if efficiency.get(key) != expected:
            raise AssemblyError(
                f"computation efficiency policy is inconsistent: {key}"
            )
    if sorted(efficiency.get("success_tracker_statuses") or []) != sorted(
        NP.ADVANCED_STATUSES
    ) or not str(efficiency.get("layout_resolution") or "").strip():
        raise AssemblyError(
            "computation efficiency statuses/layout are unresolved"
        )
    layout_policy = value.get("layout_policy")
    try:
        cu_tax_treatment = NP.normalize_cu_tax_efficiency_treatment(
            (
                layout_policy.get("cu_tax_efficiency_treatment")
                if isinstance(layout_policy, dict)
                else None
            ),
            layout["slots"],
        )
    except NP.NumericPlanError as exc:
        raise AssemblyError(str(exc)) from exc
    if not (
        isinstance(layout_policy, dict)
        and layout_policy.get("status") == "APPROVED"
        and layout_policy.get("approved_by_user") is True
        and layout_policy.get("layout_sha256") == layout["sha256"]
        and layout_policy.get("category_columns")
        == NP.CATEGORY_SUBLAYER_COLUMNS
        and layout_policy.get("efficiency_slots")
        == (plan.get("efficiency_layout") or {}).get("slots")
        and cu_tax_treatment
        == (plan.get("efficiency_layout") or {}).get(
            "cu_tax_efficiency_treatment"
        )
        and (
            layout_policy.get("audited_efficiency_slots") is None
            or layout_policy.get("audited_efficiency_slots")
            == layout["slots"]
        )
        and layout_policy.get("normalization_method")
        == "positive_column_max_zero_if_no_positive"
    ):
        raise AssemblyError("computation layout policy is inconsistent")
    try:
        press_alignment = NP.normalize_press_member_alignment_policy(
            layout_policy.get("press_member_alignment"),
            layout_sha256=layout["sha256"],
            audited_alignment=layout["press_literal_row_alignment"],
        )
    except NP.NumericPlanError as exc:
        raise AssemblyError(str(exc)) from exc
    if plan.get("press_member_alignment") != press_alignment:
        raise AssemblyError(
            "computation press-member alignment differs from the numeric plan"
        )
    try:
        cfpb_routing = NP.normalize_cfpb_policy(value.get("cfpb_policy"))
    except NP.NumericPlanError as exc:
        raise AssemblyError(str(exc)) from exc
    if plan.get("cfpb_accepted_bill_routing") != cfpb_routing:
        raise AssemblyError(
            "computation CFPB policy differs from the numeric plan"
        )
    return {"value": value, "sha256": sha256_file(path)}


def validate_out(path: Path, input_paths: Sequence[Path]) -> Path:
    resolved = path.resolve()
    allowed = [
        AUDIT_ROOT,
        (CODE_ROOT / "tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]
    try:
        permitted = any(resolved.is_relative_to(root) for root in allowed)
    except AttributeError:
        permitted = any(
            str(resolved).casefold().startswith(
                str(root).casefold() + os.sep
            )
            for root in allowed
        )
    if not permitted or resolved in allowed:
        raise AssemblyError(
            "output must be a child of audits/legfix5, repo tmp, or OS temp"
        )
    if resolved.exists():
        raise AssemblyError("approved-generation output must not already exist")
    for source in input_paths:
        try:
            contains = source.resolve().is_relative_to(resolved)
        except AttributeError:
            contains = str(source.resolve()).casefold().startswith(
                str(resolved).casefold() + os.sep
            )
        if contains:
            raise AssemblyError("output cannot contain an input artifact")
    return resolved


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(
        json.dumps(
            value, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        + b"\n"
    )
    os.replace(temporary, path)


def assemble(
    *,
    accepted_manifest_path: Path,
    accepted_ledger_path: Path,
    direction_manifest_path: Path,
    direction_ledger_path: Path,
    manual_evidence_path: Path,
    role_approval_path: Path,
    computation_approval_path: Path,
    audited_layout_path: Path,
    numeric_plan_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    READ_SHA256.clear()
    sources = {
        "source_accepted_manifest": require_file(
            accepted_manifest_path, "accepted generation manifest"
        ),
        "canonical_ledger": require_file(
            accepted_ledger_path, "accepted canonical ledger"
        ),
        "source_direction_manifest": require_file(
            direction_manifest_path, "direction manifest"
        ),
        "direction_ledger": require_file(
            direction_ledger_path, "direction ledger"
        ),
        "manual_bill_evidence": require_file(
            manual_evidence_path, "manual bill evidence"
        ),
        "role_weight_policy_approval": require_file(
            role_approval_path, "role-weight approval"
        ),
        "computation_policy_approval": require_file(
            computation_approval_path, "computation approval"
        ),
        "audited_layout": require_file(
            audited_layout_path, "audited layout"
        ),
        "numeric_recompute_plan": require_file(
            numeric_plan_path, "numeric recompute plan"
        ),
    }
    output = validate_out(out_dir, list(sources.values()))
    accepted = validate_accepted(
        sources["source_accepted_manifest"], sources["canonical_ledger"]
    )
    direction = validate_direction(
        sources["source_direction_manifest"],
        sources["direction_ledger"],
        generation_id=accepted["generation_id"],
        required_edges=accepted["scoring_edges"],
    )
    role = validate_role_approval(
        sources["role_weight_policy_approval"],
        generation_id=accepted["generation_id"],
        scenarios=accepted["role_scenarios"],
    )
    if sources["manual_bill_evidence"].suffix.casefold() != ".csv":
        raise AssemblyError("manual bill evidence must be CSV")
    manual_hash = sha256_file(sources["manual_bill_evidence"])
    layout = validate_layout(sources["audited_layout"])
    plan = validate_plan(
        sources["numeric_recompute_plan"],
        generation_id=accepted["generation_id"],
        selected_policy=role["selected"],
        accepted_hash=accepted["generation_id"],
        direction_hash=direction["sha256"],
        manual_hash=manual_hash,
        layout=layout,
        computation_policy_path=sources["computation_policy_approval"],
    )
    computation = validate_computation_policy(
        sources["computation_policy_approval"],
        plan=plan["value"],
        generation_id=accepted["generation_id"],
        selected_policy=role["selected"],
        input_hashes=plan["input_hashes"],
        layout=layout,
    )

    validated_sha = {
        "source_accepted_manifest":
            READ_SHA256[sources["source_accepted_manifest"].resolve()],
        "canonical_ledger": accepted["generation_id"],
        "source_direction_manifest":
            READ_SHA256[sources["source_direction_manifest"].resolve()],
        "direction_ledger": direction["sha256"],
        "manual_bill_evidence": manual_hash,
        "role_weight_policy_approval": role["sha256"],
        "computation_policy_approval": computation["sha256"],
        "audited_layout": layout["sha256"],
        "numeric_recompute_plan": plan["sha256"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.assembling.", dir=str(output.parent)
        )
    )
    try:
        copied: dict[str, Path] = {}
        for label, source in sources.items():
            destination = temporary / OUTPUT_NAMES[label]
            shutil.copyfile(source, destination)
            if (
                destination.stat().st_size != source.stat().st_size
                or sha256_file(destination) != validated_sha[label]
            ):
                raise AssemblyError(f"staged copy verification failed: {label}")
            copied[label] = destination
        outputs = {
            label: receipt(path)
            for label, path in copied.items()
        }
        manifest = copy.deepcopy(accepted["manifest"])
        manifest.update(
            {
                "schema": MANIFEST_SCHEMA,
                "status": "complete",
                "generation_complete": True,
                "upstream_acceptance_passed": True,
                "staged_only": True,
                "production_artifacts_written": False,
                "promotion_ready": True,
                "promotion_blockers": [],
                "selected_role_weight_policy": role["selected"],
                "selected_method_profile": ROLE_METHOD_PROFILE_ID,
                "consumer_role_methods": ROLE_METHOD_PROFILE,
                "role_weight_policy_reconciled": True,
                "canonical_ledger_file": OUTPUT_NAMES["canonical_ledger"],
                "canonical_ledger_sha256": accepted["generation_id"],
                "canonical_ledger_row_count": len(accepted["rows"]),
                "canonical_ledger_schema": LEDGER_SCHEMA,
                "role_weight_policy_approval":
                    outputs["role_weight_policy_approval"],
                "computation_policy_approval":
                    outputs["computation_policy_approval"],
                "manual_bill_evidence":
                    outputs["manual_bill_evidence"],
                "audited_layout": outputs["audited_layout"],
                "numeric_recompute_plan":
                    outputs["numeric_recompute_plan"],
                "press_member_alignment":
                    plan["press_member_alignment"],
                "direction_ledger": outputs["direction_ledger"],
                "source_accepted_generation_manifest":
                    outputs["source_accepted_manifest"],
                "source_direction_manifest":
                    outputs["source_direction_manifest"],
                "approval_assembly": {
                    "schema":
                        "approved-legislation-generation-assembly-v1",
                    "status": "PASS",
                    "generation_id": accepted["generation_id"],
                    "selected_role_weight_policy": role["selected"],
                    "all_inputs_hash_verified": True,
                    "accepted_ledger_preserved_byte_for_byte": True,
                    "all_declared_artifacts_manifest_adjacent": True,
                    "production_written": False,
                    "press_member_alignment":
                        plan["press_member_alignment"],
                    "source_inputs": {
                        label: source_receipt(path)
                        for label, path in sources.items()
                    },
                    "source_coverage_hashes": sorted(
                        accepted["source_coverage_hashes"]
                    ),
                },
                "outputs": outputs,
            }
        )
        atomic_json(temporary / "manifest.json", manifest)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Assemble a staged approved legislation generation."
    )
    result.add_argument("--accepted-manifest", type=Path, required=True)
    result.add_argument("--accepted-ledger", type=Path, required=True)
    result.add_argument("--direction-manifest", type=Path, required=True)
    result.add_argument("--direction-ledger", type=Path, required=True)
    result.add_argument("--manual-evidence", type=Path, required=True)
    result.add_argument("--role-policy-approval", type=Path, required=True)
    result.add_argument(
        "--computation-policy-approval", type=Path, required=True
    )
    result.add_argument("--audited-layout", type=Path, required=True)
    result.add_argument("--numeric-plan", type=Path, required=True)
    result.add_argument("--out", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = assemble(
            accepted_manifest_path=args.accepted_manifest,
            accepted_ledger_path=args.accepted_ledger,
            direction_manifest_path=args.direction_manifest,
            direction_ledger_path=args.direction_ledger,
            manual_evidence_path=args.manual_evidence,
            role_approval_path=args.role_policy_approval,
            computation_approval_path=args.computation_policy_approval,
            audited_layout_path=args.audited_layout,
            numeric_plan_path=args.numeric_plan,
            out_dir=args.out,
        )
    except (
        AssemblyError, OSError, ValueError, json.JSONDecodeError,
        TypeError, AttributeError, KeyError,
    ) as exc:
        print(f"APPROVED LEGISLATION GENERATION BLOCKED: {exc}")
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

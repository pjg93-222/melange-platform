# -*- coding: utf-8 -*-
"""Build a numeric-only, generation-bound legislative workbook write plan.

This producer never opens or authors an XLSX.  It consumes:

* the accepted relationship ledger;
* the explicit measure/category direction ledger;
* a hash-bound ``manual_bill_evidence.csv`` (LTR rows only);
* an audited artifact-tool member/layout extraction of the frozen workbook; and
* a human-approved computation-policy receipt bound to every input hash.

Until role, efficiency, CFPB-routing, normalization, and the legacy 17-slot
efficiency layout are explicit, the only permissible result is a BLOCKED audit.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping, Sequence

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


SCHEMA = "legislation-workbook-numeric-write-plan-v1"
LAYOUT_SCHEMA = "legislation-workbook-audited-member-layout-v1"
POLICY_SCHEMA = "legislation-workbook-computation-policy-approval-v1"
CU_TAX_TREATMENT_SCHEMA = "cu-tax-efficiency-treatment-v1"
BASELINE_SCHEMA = "legislation-production-baseline-v1"
ACCEPTED_SCHEMA = "accepted-legislation-relationship-v1"
DIRECTION_SCHEMA = "legislation-direction-decision-v1"
DEFAULT_OUT = "legislation_workbook_recompute_plan.json"

EXPECTED_DECAY = {116: 0.16, 117: 0.33, 118: 0.66, 119: 1.0}
ROLE_WEIGHTS = {
    "archive_legacy_3_2_1": {
        "sponsor": 3.0,
        "original_cosponsor": 2.0,
        "cosponsor": 1.0,
    },
    "workbook_legacy_4_2_1": {
        "sponsor": 4.0,
        "original_cosponsor": 2.0,
        "cosponsor": 1.0,
    },
    "stance_legacy_1_1_1": {
        "sponsor": 1.0,
        "original_cosponsor": 1.0,
        "cosponsor": 1.0,
    },
}
ROLE_ORDER = ("sponsor", "original_cosponsor", "cosponsor")
ADVANCED_STATUSES = frozenset(
    {
        "Passed House",
        "Passed Senate",
        "Agreed to in Senate",
        "Resolving Differences",
        "Failed to pass over veto",
        "Became Law",
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
CATEGORY_ALIASES = {
    re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_"): value
    for value in CATEGORIES
}
CATEGORY_ALIASES.update(
    {
        "credit_union_capital_requirements": "CU Capital Requirements",
        "ncua_budget_share_insurance": "NCUA Budget & Insurance",
        "technology_emerging_issues": "Technology & Emerging",
        "data_privacy_cybersecurity": "Data Privacy & Cyber",
        "charter_modernization_governance": "Charter Modernization",
        "defense_credit_union_issues": "Defense CU Issues",
        "credit_card_consumer_fee": "Credit Card Mandates",
        "telephone_consumer_protection_act": "TCPA",
        "mortgage_lending_housing": "Mortgage & Housing",
        "credit_union_tax_status": "CU Tax Status",
    }
)

BLOCK_RANGES = {
    category: value
    for category, value in zip(
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
CATEGORY_SUBLAYER_COLUMNS = {
    "Examination & Supervision": "C",
    "CU Capital Requirements": "D",
    "NCUA Budget & Insurance": "E",
    "Interchange": "F",
    "Payments": "G",
    "Technology & Emerging": "H",
    "Fraud Prevention": "I",
    "Data Privacy & Cyber": "J",
    "Charter Modernization": "K",
    "Defense CU Issues": "L",
    "Field of Membership": "M",
    "CU Tax Status": "N",
    "CDFI Fund": "O",
    "CFPB": "Q",
    "TCPA": "R",
    "Credit Card Mandates": "S",
    "Member Business Lending": "T",
    "Mortgage & Housing": "U",
}
EFFICIENCY_COLUMNS = tuple(
    chr(ord("A") + index) if index < 26 else "A" + chr(ord("A") + index - 26)
    for index in range(32, 49)
)
CU_TAX_CATEGORY = "CU Tax Status"
CU_TAX_RAW_GENERIC_RANGE = "FB3:FB535"
CU_TAX_NORMALIZED_GENERIC_RANGE = "FB3:FB535"
CU_TAX_PRESERVE_OMISSION = "preserve_legacy_efficiency_omission"
CU_TAX_MERGE_NAMED_SLOT = "merge_into_named_legacy_slot"
# The expression above deliberately yields AG:AW without spreadsheet packages.
assert EFFICIENCY_COLUMNS == (
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

RAW_OFFSETS = {
    "sponsor_activity": 0,
    "sponsor_average": 1,
    "original_activity": 2,
    "original_average": 3,
    "cosponsor_activity": 4,
    "generic_success_rate": 5,
    "sponsor_success_rate": 6,
    "original_success_rate": 7,
}
HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
BIOGUIDE_RE = re.compile(r"^[A-Z]\d{6}$")
MEASURE_RE = CANONICAL_KEY_RE
CFPB_ROUTE_SCHEMA = "cfpb-accepted-bill-sponsor-original-route-v1"
CFPB_ROUTE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*_v[1-9]\d*$")
PRESS_ALIGNMENT_POLICY_SCHEMA = "press-member-alignment-policy-v1"
PRESS_ALIGNMENT_PRESERVE_LITERAL = (
    "preserve_literal_formula_row_reference"
)
PRESS_ALIGNMENT_REPAIR_BY_BIOGUIDE = "repair_by_member_bioguide_join"
PRESS_ALIGNMENT_UNRESOLVED_STATUS = (
    "REQUIRES_EXPLICIT_POLICY_DECISION"
)
PRESS_ALIGNMENT_REPAIRED_STATUS = (
    "RESOLVED_BY_MEMBER_BIOGUIDE_JOIN"
)
PRESS_ALIGNMENT_REPAIR_SEMANTICS = "member_bioguide_join"
PRESS_ALIGNMENT_REPAIR_JOIN_KEY = "bioguide_id"
# Empty until an exact non-CU/CW destination is implemented by this writer.
# A policy receipt can describe a future route, but naming it never enables it.
CFPB_IMPLEMENTED_BILL_ROUTES: dict[str, str] = {}

class NumericPlanError(RuntimeError):
    """An input, policy, layout, or computation contract is unresolved."""


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


def file_receipt(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    shown = (
        resolved.relative_to(relative_to.resolve()).as_posix()
        if relative_to is not None
        else str(resolved)
    )
    return {
        "path": shown,
        "size": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def required_nonbill_numeric_policy(
    manual_evidence_sha256: str,
) -> dict[str, Any]:
    """Mirror the accepted-workbook validator's exact numeric policy."""
    sha = str(manual_evidence_sha256 or "").casefold()
    if not HEX_RE.fullmatch(sha):
        raise NumericPlanError("manual evidence SHA-256 is invalid")
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


def normalize_cfpb_policy(value: Any) -> dict[str, Any]:
    """Validate immutable legacy columns and an explicit bill-route choice."""
    if not isinstance(value, dict):
        raise NumericPlanError("CFPB CU:CY routing policy is missing")
    if not (
        value.get("CU") == "LTR_sponsor_only"
        and value.get("CV") == "explicit_zero"
        and value.get("CW") == "LTR_original_cosponsor_only"
        and value.get("CX") == "explicit_zero"
        and value.get("accepted_bill_cosponsor") == "include_in_CY"
    ):
        raise NumericPlanError(
            "CFPB fixed routing must preserve CU/CW as LTR-only, "
            "CV/CX as zero, and accepted cosponsors in CY"
        )
    route = value.get("accepted_bill_sponsor_original")
    if not isinstance(route, dict) or route.get("schema") != CFPB_ROUTE_SCHEMA:
        raise NumericPlanError(
            "CFPB accepted-bill Sponsor/Original route contract is missing"
        )
    status = route.get("status")
    selected = route.get("selected_route")
    approved = route.get("approved_by_user")
    implementation_id = route.get("implementation_id")
    if selected is None:
        if not (
            status == "UNSELECTED"
            and approved is False
            and implementation_id is None
        ):
            raise NumericPlanError(
                "unselected CFPB accepted-bill route must remain explicitly "
                "unapproved and unimplemented"
            )
    else:
        if not (
            isinstance(selected, str)
            and CFPB_ROUTE_ID_RE.fullmatch(selected)
            and status == "SELECTED"
            and approved is True
            and isinstance(implementation_id, str)
            and implementation_id
        ):
            raise NumericPlanError(
                "selected CFPB accepted-bill route is malformed or unapproved"
            )
        expected = CFPB_IMPLEMENTED_BILL_ROUTES.get(selected)
        if expected is None:
            raise NumericPlanError(
                f"selected CFPB accepted-bill route is not implemented: "
                f"{selected}"
            )
        if implementation_id != expected:
            raise NumericPlanError(
                "selected CFPB accepted-bill route implementation ID is stale"
            )
    return {
        "fixed_legacy_columns": {
            "CU": "LTR_sponsor_only",
            "CV": "explicit_zero",
            "CW": "LTR_original_cosponsor_only",
            "CX": "explicit_zero",
            "CY": "accepted_bill_cosponsor",
        },
        "sponsor_original_route": {
            "schema": CFPB_ROUTE_SCHEMA,
            "status": status,
            "approved_by_user": approved,
            "selected_route": selected,
            "implementation_id": implementation_id,
        },
    }


def press_alignment_facts(value: Any) -> dict[str, Any]:
    """Normalize the audited literal-row/member-join facts fail-closed."""
    if not isinstance(value, dict):
        raise NumericPlanError(
            "audited press literal-row alignment facts are missing"
        )
    status = value.get("status")
    member_join_applied = value.get("member_join_applied")
    semantics = value.get("reconstruction_semantics")
    mismatch_count = value.get("mismatch_count")
    mismatches = value.get("mismatches")
    join_key = value.get("member_join_key")
    if not (
        isinstance(status, str)
        and status
        and isinstance(member_join_applied, bool)
        and isinstance(semantics, str)
        and semantics
        and isinstance(mismatch_count, int)
        and not isinstance(mismatch_count, bool)
        and mismatch_count >= 0
        and isinstance(mismatches, list)
        and len(mismatches) == mismatch_count
        and all(isinstance(item, dict) for item in mismatches)
        and (join_key is None or isinstance(join_key, str))
    ):
        raise NumericPlanError(
            "audited press literal-row alignment facts are malformed"
        )
    return {
        "status": status,
        "member_join_applied": member_join_applied,
        "member_join_key": join_key,
        "reconstruction_semantics": semantics,
        "mismatch_count": mismatch_count,
        "mismatches_sha256": hashlib.sha256(
            canonical_bytes(mismatches)
        ).hexdigest(),
    }


def normalize_press_member_alignment_policy(
    value: Any,
    *,
    layout_sha256: str,
    audited_alignment: Any,
) -> dict[str, Any]:
    """Require an approved policy that matches the exact audited layout."""
    layout_sha = str(layout_sha256 or "").casefold()
    facts = press_alignment_facts(audited_alignment)
    if not (
        isinstance(value, dict)
        and value.get("schema") == PRESS_ALIGNMENT_POLICY_SCHEMA
        and value.get("status") == "APPROVED"
        and value.get("approved_by_user") is True
        and value.get("rank_effect_acknowledged") is True
        and HEX_RE.fullmatch(layout_sha)
        and str(value.get("layout_sha256") or "").casefold()
        == layout_sha
        and value.get("audited_facts") == facts
    ):
        raise NumericPlanError(
            "press-member alignment policy is missing, unapproved, stale, "
            "or inconsistent with the audited layout facts"
        )
    selected = value.get("selected_treatment")
    if selected == PRESS_ALIGNMENT_PRESERVE_LITERAL:
        if not (
            facts["status"] == PRESS_ALIGNMENT_UNRESOLVED_STATUS
            and facts["member_join_applied"] is False
            and facts["member_join_key"] is None
            and facts["reconstruction_semantics"]
            == PRESS_ALIGNMENT_PRESERVE_LITERAL
        ):
            raise NumericPlanError(
                "preserve-literal press alignment does not match the "
                "audited layout reconstruction"
            )
    elif selected == PRESS_ALIGNMENT_REPAIR_BY_BIOGUIDE:
        if not (
            facts["status"] == PRESS_ALIGNMENT_REPAIRED_STATUS
            and facts["member_join_applied"] is True
            and facts["member_join_key"]
            == PRESS_ALIGNMENT_REPAIR_JOIN_KEY
            and facts["reconstruction_semantics"]
            == PRESS_ALIGNMENT_REPAIR_SEMANTICS
        ):
            raise NumericPlanError(
                "press alignment repair requires an audited layout that "
                "actually applied the member/Bioguide join"
            )
    else:
        raise NumericPlanError(
            "press-member alignment treatment is missing or unrecognized"
        )
    return {
        "schema": PRESS_ALIGNMENT_POLICY_SCHEMA,
        "status": "APPROVED",
        "approved_by_user": True,
        "selected_treatment": selected,
        "layout_sha256": layout_sha,
        "audited_facts": facts,
        "rank_effect_acknowledged": True,
    }


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


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NumericPlanError(f"invalid JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise NumericPlanError(f"JSON root must be an object: {path}")
    return value


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise NumericPlanError(f"cannot read NDJSON {path}: {exc}") from exc
    rows = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise NumericPlanError(
                f"invalid NDJSON {path}:{number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise NumericPlanError(f"non-object NDJSON row {path}:{number}")
        rows.append(value)
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError as exc:
        raise NumericPlanError(f"cannot read CSV {path}: {exc}") from exc


def normalized_token(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9]+", "_", " ".join(str(value or "").split()).casefold()
    ).strip("_")


def category(value: Any) -> str:
    token = normalized_token(value)
    result = CATEGORY_ALIASES.get(token)
    if not result:
        raise NumericPlanError(f"unrecognized legislation category: {value!r}")
    return result


def finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NumericPlanError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise NumericPlanError(f"{label} must be finite")
    return result


def validate_baseline(path: Path, workbook_sha256: str) -> dict[str, Any]:
    value = read_object(path)
    if value.get("schema") != BASELINE_SCHEMA:
        raise NumericPlanError("frozen baseline manifest schema mismatch")
    matches = [
        row
        for row in value.get("files") or []
        if isinstance(row, dict)
        and Path(str(row.get("copy") or row.get("source") or "")).name
        == "Moneyball Main Version - Blended.xlsx"
    ]
    if len(matches) != 1:
        raise NumericPlanError("frozen model workbook receipt is ambiguous")
    row = matches[0]
    if str(row.get("sha256") or "").casefold() != workbook_sha256.casefold():
        raise NumericPlanError(
            "layout source workbook is not the frozen model workbook"
        )
    return {
        "manifest": file_receipt(path),
        "workbook": {
            "basename": "Moneyball Main Version - Blended.xlsx",
            "size": row.get("size"),
            "sha256": workbook_sha256.casefold(),
        },
    }


def validate_members(layout: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = layout.get("members")
    if not isinstance(rows, list) or len(rows) != 533:
        raise NumericPlanError("audited layout must contain exactly 533 members")
    members: list[dict[str, Any]] = []
    by_bioguide: dict[str, int] = {}
    names: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise NumericPlanError("layout member row is not an object")
        name = " ".join(str(raw.get("name") or "").split())
        bioguide = str(raw.get("bioguide_id") or "")
        raw_row = raw.get("raw_row")
        sublayer_row = raw.get("sublayer_row")
        if (
            not name
            or name in names
            or not BIOGUIDE_RE.fullmatch(bioguide)
            or bioguide in by_bioguide
            or raw_row != index + 3
            or sublayer_row != index + 4
        ):
            raise NumericPlanError(
                f"invalid/noncanonical layout member at offset {index}"
            )
        names.add(name)
        by_bioguide[bioguide] = index
        members.append(
            {
                "name": name,
                "bioguide_id": bioguide,
                "raw_row": raw_row,
                "sublayer_row": sublayer_row,
            }
        )
    return members, by_bioguide


def validate_layout(
    path: Path, baseline_manifest: Path
) -> dict[str, Any]:
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
    ):
        raise NumericPlanError("member/layout extraction is not an audited PASS")
    baseline = validate_baseline(baseline_manifest, source_hash)
    members, by_bioguide = validate_members(value)
    if value.get("raw_block_ranges") != BLOCK_RANGES:
        raise NumericPlanError("audited raw block ranges differ from canonical 18")
    if value.get("sublayer_category_columns") != CATEGORY_SUBLAYER_COLUMNS:
        raise NumericPlanError("audited sublayer category layout is noncanonical")
    press_alignment = press_alignment_facts(
        value.get("press_literal_row_alignment")
    )

    nonleg = value.get("sublayer_nonlegislative_values")
    weights = value.get("sublayer_legislative_weights")
    if not isinstance(nonleg, dict) or not isinstance(weights, dict):
        raise NumericPlanError("layout lacks extracted sublayer inputs")
    normalized_nonleg: dict[str, list[float]] = {}
    normalized_weights: dict[str, list[float]] = {}
    for cat in CATEGORIES:
        values = nonleg.get(cat)
        role_weights = weights.get(cat)
        if not isinstance(values, list) or len(values) != 533:
            raise NumericPlanError(f"sublayer nonlegislative vector invalid: {cat}")
        if not isinstance(role_weights, list) or len(role_weights) != 5:
            raise NumericPlanError(f"sublayer legislative weights invalid: {cat}")
        normalized_nonleg[cat] = [
            finite_number(item, label=f"{cat} nonlegislative value")
            for item in values
        ]
        normalized_weights[cat] = [
            finite_number(item, label=f"{cat} sublayer weight")
            for item in role_weights
        ]

    slots = value.get("efficiency_slots")
    if not isinstance(slots, list) or len(slots) != 17:
        raise NumericPlanError("audited efficiency layout must define AG:AW")
    normalized_slots: list[dict[str, Any]] = []
    for column, raw in zip(EFFICIENCY_COLUMNS, slots):
        if not isinstance(raw, dict) or raw.get("column") != column:
            raise NumericPlanError(f"efficiency slot {column} is missing/reordered")
        status = raw.get("status")
        cats = raw.get("categories")
        candidates = raw.get("category_candidates")
        if status is None and isinstance(cats, list) and cats:
            status = "RESOLVED_IN_AUDITED_LAYOUT"
            candidates = list(cats)
        if not (
            status
            in {
                "UNRESOLVED",
                "IDENTIFIED_FROM_WORKBOOK",
                "RESOLVED_IN_AUDITED_LAYOUT",
            }
            and isinstance(candidates, list)
            and all(item in CATEGORIES for item in candidates)
        ):
            raise NumericPlanError(
                f"audited efficiency slot {column} facts are invalid"
            )
        if status == "IDENTIFIED_FROM_WORKBOOK" and len(candidates) != 1:
            raise NumericPlanError(
                f"audited efficiency slot {column} identification is ambiguous"
            )
        normalized_slots.append(
            {
                "column": column,
                "status": status,
                "category_candidates": list(candidates),
                "evidence": raw.get("evidence") or {},
            }
        )
    return {
        "value": value,
        "receipt": file_receipt(path),
        "baseline": baseline,
        "members": members,
        "by_bioguide": by_bioguide,
        "sublayer_nonlegislative_values": normalized_nonleg,
        "sublayer_legislative_weights": normalized_weights,
        "efficiency_slots": normalized_slots,
        "press_literal_row_alignment": value.get(
            "press_literal_row_alignment"
        ),
        "press_alignment_facts": press_alignment,
        "source_workbook_sha256": source_hash,
    }


def normalize_cu_tax_efficiency_treatment(
    value: Any,
    audited_slots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate an explicit CU Tax disposition without inferring a slot."""
    if not (
        isinstance(value, dict)
        and value.get("schema") == CU_TAX_TREATMENT_SCHEMA
        and value.get("status") == "APPROVED"
        and value.get("approved_by_user") is True
        and value.get("omitted_category") == CU_TAX_CATEGORY
        and value.get("rank_effect_acknowledged") is True
    ):
        raise NumericPlanError(
            "CU Tax efficiency treatment is absent, unapproved, or unresolved"
        )
    selected = value.get("selected_treatment")
    if selected == CU_TAX_PRESERVE_OMISSION:
        if not (
            value.get("legacy_physical_column_count") == 17
            and value.get("resolved_category_count") == 18
            and value.get("source_metric") == "generic_success_rate"
            and value.get("raw_source_range")
            == f"Legislative Score - Raw!{CU_TAX_RAW_GENERIC_RANGE}"
            and value.get("normalized_source_range")
            == (
                "Legislative Score - Normalized!"
                f"{CU_TAX_NORMALIZED_GENERIC_RANGE}"
            )
            and value.get("merge_target_category") is None
            and value.get("merge_target_column") is None
        ):
            raise NumericPlanError(
                "CU Tax legacy-omission treatment is incomplete"
            )
        return {
            "schema": CU_TAX_TREATMENT_SCHEMA,
            "status": "APPROVED",
            "approved_by_user": True,
            "selected_treatment": CU_TAX_PRESERVE_OMISSION,
            "omitted_category": CU_TAX_CATEGORY,
            "legacy_physical_column_count": 17,
            "resolved_category_count": 18,
            "source_metric": "generic_success_rate",
            "raw_source_range":
                f"Legislative Score - Raw!{CU_TAX_RAW_GENERIC_RANGE}",
            "normalized_source_range": (
                "Legislative Score - Normalized!"
                f"{CU_TAX_NORMALIZED_GENERIC_RANGE}"
            ),
            "merge_target_category": None,
            "merge_target_column": None,
            "rank_effect_acknowledged": True,
        }
    if selected == CU_TAX_MERGE_NAMED_SLOT:
        target_category = value.get("merge_target_category")
        target_column = value.get("merge_target_column")
        matching = [
            slot
            for slot in audited_slots
            if slot.get("column") == target_column
            and slot.get("category_candidates") == [target_category]
        ]
        if not (
            isinstance(target_category, str)
            and target_category in set(CATEGORIES) - {CU_TAX_CATEGORY}
            and target_column in EFFICIENCY_COLUMNS
            and len(matching) == 1
            and value.get("aggregation") == "mean"
            and value.get("workbook_fact_change_acknowledged") is True
            and value.get("legacy_physical_column_count") == 17
            and value.get("resolved_category_count") == 18
        ):
            raise NumericPlanError(
                "CU Tax named-slot merge treatment is incomplete or "
                "not bound to an audited singleton"
            )
        return {
            "schema": CU_TAX_TREATMENT_SCHEMA,
            "status": "APPROVED",
            "approved_by_user": True,
            "selected_treatment": CU_TAX_MERGE_NAMED_SLOT,
            "omitted_category": CU_TAX_CATEGORY,
            "legacy_physical_column_count": 17,
            "resolved_category_count": 18,
            "merge_target_category": target_category,
            "merge_target_column": target_column,
            "aggregation": "mean",
            "workbook_fact_change_acknowledged": True,
            "rank_effect_acknowledged": True,
        }
    raise NumericPlanError("CU Tax efficiency treatment selection is unknown")


def normalize_resolved_efficiency_slots(
    rows: Any,
    audited_slots: Sequence[Mapping[str, Any]],
    cu_tax_treatment: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Validate 17 audited slots under an explicit CU Tax disposition."""
    treatment = normalize_cu_tax_efficiency_treatment(
        cu_tax_treatment, audited_slots
    )
    if not isinstance(rows, list) or len(rows) != 17:
        raise NumericPlanError(
            "approved efficiency layout must define all 17 physical slots"
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for column, raw, audited in zip(EFFICIENCY_COLUMNS, rows, audited_slots):
        if not isinstance(raw, dict) or raw.get("column") != column:
            raise NumericPlanError(
                f"approved efficiency slot {column} is missing/reordered"
            )
        cats = raw.get("categories")
        aggregation = raw.get("aggregation")
        if not (
            isinstance(cats, list)
            and cats
            and all(item in CATEGORIES for item in cats)
            and aggregation in {"identity", "mean"}
            and (len(cats) == 1) == (aggregation == "identity")
            and not (set(cats) & seen)
        ):
            raise NumericPlanError(
                "approved efficiency layout must map each of the 18 "
                f"categories exactly once; invalid slot {column}"
            )
        candidates = list(audited.get("category_candidates") or [])
        if audited.get("status") in {
            "IDENTIFIED_FROM_WORKBOOK",
            "RESOLVED_IN_AUDITED_LAYOUT",
        }:
            allowed = cats == candidates
            if treatment["selected_treatment"] == CU_TAX_MERGE_NAMED_SLOT:
                allowed = (
                    column == treatment["merge_target_column"]
                    and cats
                    == [
                        treatment["merge_target_category"],
                        CU_TAX_CATEGORY,
                    ]
                    and aggregation == "mean"
                ) or allowed
            if not allowed:
                raise NumericPlanError(
                    f"approved efficiency slot {column} contradicts "
                    "workbook facts or the explicit CU Tax treatment"
                )
        seen.update(cats)
        result.append(
            {
                "column": column,
                "categories": list(cats),
                "aggregation": aggregation,
            }
        )
    expected_seen = set(CATEGORIES)
    if treatment["selected_treatment"] == CU_TAX_PRESERVE_OMISSION:
        expected_seen.remove(CU_TAX_CATEGORY)
    if seen != expected_seen:
        raise NumericPlanError(
            "approved 17-slot layout does not match the explicit CU Tax "
            "treatment"
        )
    expected_merges = (
        1
        if treatment["selected_treatment"] == CU_TAX_MERGE_NAMED_SLOT
        else 0
    )
    if sum(len(slot["categories"]) > 1 for slot in result) != expected_merges:
        raise NumericPlanError(
            "approved legacy efficiency slots do not implement the explicit "
            "CU Tax treatment"
        )
    return result


def validate_policy(
    path: Path,
    *,
    generation_id: str,
    input_hashes: Mapping[str, str],
    layout: Mapping[str, Any],
) -> dict[str, Any]:
    value = read_object(path)
    selected = str(value.get("selected_role_weight_policy") or "")
    role = value.get("role_policy") or {}
    efficiency = value.get("legislative_efficiency_definition") or {}
    layout_policy = value.get("layout_policy") or {}
    cfpb = value.get("cfpb_policy") or {}
    if not (
        value.get("schema") == POLICY_SCHEMA
        and value.get("status") == "APPROVED"
        and value.get("approved_by_user") is True
        and value.get("generation_id") == generation_id
        and value.get("input_sha256") == dict(input_hashes)
        and selected in ROLE_WEIGHTS
        and value.get("role_weight_policy_reconciled") is True
    ):
        raise NumericPlanError(
            "computation policy is absent, unapproved, stale, or not input-bound"
        )
    if not (
        role.get("weights") == ROLE_WEIGHTS[selected]
        and role.get("raw_activity_encoding") == "signed_sum_congress_decay"
        and role.get("average_encoding")
        == "signed_mean_congress_decay_per_relationship"
        and role.get("workbook_row2_applies_role_weights") is True
    ):
        raise NumericPlanError("role/raw-column computation policy is unresolved")
    if selected != "workbook_legacy_4_2_1":
        raise NumericPlanError(
            "workbook numeric plan must select its audited 4/2/1 consumer "
            "method inside the approved consumer-specific profile"
        )
    expected = ROLE_WEIGHTS[selected]
    for cat in CATEGORIES:
        weights = layout["sublayer_legislative_weights"][cat]
        scale = weights[4] / expected["cosponsor"]
        if not (
            scale > 0
            and math.isclose(weights[0], expected["sponsor"] * scale)
            and math.isclose(
                weights[2],
                expected["original_cosponsor"] * scale,
            )
            and math.isclose(weights[1], 0.0, abs_tol=1e-12)
            and math.isclose(weights[3], 0.0, abs_tol=1e-12)
        ):
            raise NumericPlanError(
                f"audited workbook row-2 weights do not preserve 4/2/1 "
                f"for {cat}: {weights!r}"
            )
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
        "ltr_success_handling": "activity_only_excluded_from_efficiency",
        "legacy_efficiency_column_count": 17,
        "resolved_category_count": 18,
    }
    for key, expected in required_efficiency.items():
        if efficiency.get(key) != expected:
            raise NumericPlanError(
                f"uniform efficiency policy field is unresolved: {key}"
            )
    if sorted(efficiency.get("success_tracker_statuses") or []) != sorted(
        ADVANCED_STATUSES
    ):
        raise NumericPlanError("approved success tracker statuses are incomplete")
    if not str(efficiency.get("layout_resolution") or "").strip():
        raise NumericPlanError("18-category efficiency layout resolution is blank")
    approved_slots = normalize_resolved_efficiency_slots(
        layout_policy.get("efficiency_slots"),
        layout["efficiency_slots"],
        layout_policy.get("cu_tax_efficiency_treatment"),
    )
    cu_tax_treatment = normalize_cu_tax_efficiency_treatment(
        layout_policy.get("cu_tax_efficiency_treatment"),
        layout["efficiency_slots"],
    )
    audited_binding = layout_policy.get("audited_efficiency_slots")
    if not (
        layout_policy.get("status") == "APPROVED"
        and layout_policy.get("approved_by_user") is True
        and layout_policy.get("layout_sha256")
        == layout["receipt"]["sha256"]
        and layout_policy.get("category_columns")
        == CATEGORY_SUBLAYER_COLUMNS
        and (
            audited_binding is None
            or audited_binding == layout["efficiency_slots"]
        )
        and layout_policy.get("normalization_method")
        == "positive_column_max_zero_if_no_positive"
    ):
        raise NumericPlanError("member/sublayer layout policy is unresolved")
    press_alignment = normalize_press_member_alignment_policy(
        layout_policy.get("press_member_alignment"),
        layout_sha256=layout["receipt"]["sha256"],
        audited_alignment=layout["press_literal_row_alignment"],
    )
    cfpb_routing = normalize_cfpb_policy(cfpb)
    return {
        "value": value,
        "receipt": file_receipt(path),
        "selected_role_weight_policy": selected,
        "efficiency": efficiency,
        "efficiency_slots": approved_slots,
        "cu_tax_efficiency_treatment": cu_tax_treatment,
        "press_member_alignment": press_alignment,
        "cfpb_routing": cfpb_routing,
        "workbook_role_weights_verified": True,
    }


def direction_map(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], int]:
    result: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows, start=1):
        if row.get("schema") != DIRECTION_SCHEMA:
            raise NumericPlanError(f"direction row {index} schema mismatch")
        key = (
            str(row.get("canonical_key") or ""),
            category(row.get("category")),
        )
        try:
            sign = int(
                row.get("direction_sign")
                if row.get("direction_sign") is not None
                else row.get("direction")
            )
        except (TypeError, ValueError) as exc:
            raise NumericPlanError(
                f"direction row {index} sign is nonnumeric"
            ) from exc
        if sign not in {-1, 0, 1} or key in result:
            raise NumericPlanError(f"direction row {index} is invalid/duplicate")
        result[key] = sign
    return result


def accepted_events(
    rows: Iterable[dict[str, Any]],
    *,
    generation_id: str,
    directions: Mapping[tuple[str, str], int],
    by_bioguide: Mapping[str, int],
    members: Sequence[Mapping[str, Any]],
    cfpb_routing: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events = []
    counts: defaultdict[str, int] = defaultdict(int)
    counts["accepted_cfpb_sponsor_original_bill_events"] = 0
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if row.get("schema") != ACCEPTED_SCHEMA:
            raise NumericPlanError(f"accepted row {index} schema mismatch")
        measure = str(row.get("measure_key") or "")
        match = MEASURE_RE.fullmatch(measure)
        if not match:
            raise NumericPlanError(f"accepted row {index} measure key invalid")
        congress = int(match.group(1))
        if row.get("congress") != congress:
            raise NumericPlanError(f"accepted row {index} Congress mismatch")
        if row.get("decay_weight") != EXPECTED_DECAY[congress]:
            raise NumericPlanError(f"accepted row {index} decay mismatch")
        if "measure_weight" not in row or "effective_weight" not in row:
            raise NumericPlanError(
                f"accepted row {index} lacks the bound measure-weight policy"
            )
        measure_factor = measure_weight(measure)
        if float(row["measure_weight"]) != measure_factor:
            raise NumericPlanError(
                f"accepted row {index} measure weight mismatch"
            )
        effective_weight = round(
            EXPECTED_DECAY[congress] * measure_factor,
            12,
        )
        if float(row["effective_weight"]) != effective_weight:
            raise NumericPlanError(
                f"accepted row {index} effective weight mismatch"
            )
        role = row.get("role")
        if role not in ROLE_ORDER:
            raise NumericPlanError(f"accepted row {index} role invalid")
        cat = category(row.get("category_label") or row.get("category"))
        bioguide = str(row.get("member_bioguide_id") or "")
        if bioguide not in by_bioguide:
            raise NumericPlanError(
                f"accepted row {index} member absent from audited layout"
            )
        if (
            " ".join(str(row.get("member_name") or "").split())
            != members[by_bioguide[bioguide]]["name"]
        ):
            raise NumericPlanError(
                f"accepted row {index} name/Bioguide layout mismatch"
            )
        natural = (measure, cat, bioguide)
        if natural in seen:
            raise NumericPlanError(
                f"accepted ledger has duplicate strongest-role edge: {natural}"
            )
        seen.add(natural)
        eligible = bool(
            row.get("membership_status") == "included"
            and row.get("relationship_status") == "current"
            and row.get("scoring_category") is True
            and row.get("scoring_eligible") is True
        )
        if not eligible:
            counts["non_scoring_or_inactive"] += 1
            continue
        edge = (measure, cat)
        if edge not in directions:
            raise NumericPlanError(f"accepted scoring edge lacks direction: {edge}")
        sign = directions[edge]
        if sign == 0:
            counts["neutral_direction"] += 1
            continue
        if cat == "CFPB" and role in {"sponsor", "original_cosponsor"}:
            counts["accepted_cfpb_sponsor_original_bill_events"] += 1
            selected_route = cfpb_routing[
                "sponsor_original_route"
            ]["selected_route"]
            if selected_route is None:
                raise NumericPlanError(
                    "accepted CFPB bill Sponsor/Original rows require an "
                    "explicit selected activity route"
                )
            # Independent fail-closed guard: adding a registry entry must not
            # silently drop the event before its physical writer exists.
            raise NumericPlanError(
                "selected CFPB bill Sponsor/Original route is registered but "
                "not materialized by the event builder"
            )
        events.append(
            {
                "source": "accepted_bill",
                "member_index": by_bioguide[bioguide],
                "measure": measure,
                "congress": congress,
                "category": cat,
                "role": role,
                "sign": sign,
                "congress_decay": EXPECTED_DECAY[congress],
                "measure_weight": measure_factor,
                "decay": effective_weight,
                "tracker_status": str(row.get("tracker_status") or ""),
            }
        )
        counts["accepted_bill_events"] += 1
    return events, dict(counts)


def ltr_events(
    rows: Iterable[dict[str, str]],
    *,
    members: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_name = {str(row["name"]): index for index, row in enumerate(members)}
    events = []
    stale_decay = []
    non_ltr = 0
    for index, row in enumerate(rows, start=2):
        bill = str(row.get("Bill") or "").strip()
        if not bill.upper().startswith("LTR-"):
            non_ltr += 1
            continue
        name = " ".join(str(row.get("Member") or "").split())
        if name not in by_name:
            raise NumericPlanError(
                f"LTR row {index} member absent from audited layout: {name}"
            )
        try:
            congress = int(row.get("Congress") or "")
        except ValueError as exc:
            raise NumericPlanError(f"LTR row {index} Congress invalid") from exc
        if congress not in EXPECTED_DECAY:
            raise NumericPlanError(f"LTR row {index} Congress out of scope")
        role_text = normalized_token(row.get("Role"))
        role = {
            "sponsor": "sponsor",
            "original_cosponsor": "original_cosponsor",
        }.get(role_text)
        if role is None:
            raise NumericPlanError(f"LTR row {index} role unsupported")
        try:
            sign = int(str(row.get("Direction") or "").strip())
        except ValueError as exc:
            raise NumericPlanError(f"LTR row {index} direction invalid") from exc
        if sign not in {-1, 0, 1}:
            raise NumericPlanError(f"LTR row {index} direction invalid")
        observed = str(row.get("DecayWeight") or "").strip()
        canonical = EXPECTED_DECAY[congress]
        try:
            observed_number = float(observed)
        except ValueError:
            observed_number = None
        if observed_number != canonical:
            stale_decay.append(
                {
                    "csv_row": index,
                    "bill": bill,
                    "observed": observed,
                    "applied": canonical,
                }
            )
        if sign == 0:
            continue
        events.append(
            {
                "source": "manual_LTR",
                "member_index": by_name[name],
                "measure": bill,
                "congress": congress,
                "category": category(row.get("Category")),
                "role": role,
                "sign": sign,
                "decay": canonical,
                "tracker_status": None,
            }
        )
    return events, {
        "LTR_event_count": len(events),
        "non_LTR_rows_retained_outside_plan": non_ltr,
        "stored_decay_mismatch_count": len(stale_decay),
        "stored_decay_mismatches": stale_decay,
        "canonical_decay_applied": {
            str(key): value for key, value in EXPECTED_DECAY.items()
        },
    }


def positive_max_normalize(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    if not matrix:
        return []
    columns = len(matrix[0])
    maxima = [
        max([0.0] + [float(row[column]) for row in matrix])
        for column in range(columns)
    ]
    return [
        [
            0.0 if maxima[column] == 0 else float(value) / maxima[column]
            for column, value in enumerate(row)
        ]
        for row in matrix
    ]


def rate(
    events: Sequence[Mapping[str, Any]],
    roles: set[str],
    precision: int,
) -> float:
    eligible = [
        item
        for item in events
        if item["source"] == "accepted_bill" and item["role"] in roles
    ]
    denominator = sum(float(item["decay"]) for item in eligible)
    numerator = sum(
        float(item["decay"])
        for item in eligible
        if item["tracker_status"] in ADVANCED_STATUSES
    )
    return round(100.0 * numerator / denominator, precision) if denominator else 0.0


def build_matrices(
    *,
    members: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    layout: Mapping[str, Any],
    efficiency: Mapping[str, Any],
) -> tuple[dict[str, list[list[float]]], dict[str, Any]]:
    grouped: defaultdict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[(int(event["member_index"]), str(event["category"]))].append(event)
    raw: dict[str, list[list[float]]] = {}
    normalized: dict[str, list[list[float]]] = {}
    precision = int(efficiency["precision_decimals"])
    for cat in CATEGORIES:
        matrix = []
        for member_index in range(len(members)):
            current = grouped[(member_index, cat)]
            role_events = {
                role: [item for item in current if item["role"] == role]
                for role in ROLE_ORDER
            }
            activity = {
                role: sum(
                    float(item["sign"]) * float(item["decay"])
                    for item in role_events[role]
                )
                for role in ROLE_ORDER
            }
            averages = {
                role: (
                    activity[role] / len(role_events[role])
                    if role_events[role]
                    else 0.0
                )
                for role in ROLE_ORDER
            }
            sponsor = rate(current, {"sponsor"}, precision)
            original = rate(current, {"original_cosponsor"}, precision)
            generic = rate(
                current, {"sponsor", "original_cosponsor"}, precision
            )
            row = [
                activity["sponsor"],
                averages["sponsor"],
                activity["original_cosponsor"],
                averages["original_cosponsor"],
                activity["cosponsor"],
                generic,
                sponsor,
                original,
            ]
            if cat == "CFPB":
                # CU/CW are LTR-only; CV/CX are deliberately dead zero.
                row[0] = sum(
                    float(item["sign"]) * float(item["decay"])
                    for item in role_events["sponsor"]
                    if item["source"] == "manual_LTR"
                )
                row[1] = 0.0
                row[2] = sum(
                    float(item["sign"]) * float(item["decay"])
                    for item in role_events["original_cosponsor"]
                    if item["source"] == "manual_LTR"
                )
                row[3] = 0.0
            matrix.append([round(float(value), 12) for value in row])
        raw[cat] = matrix
        normalized[cat] = positive_max_normalize(matrix)

    sublayer_raw: dict[str, list[list[float]]] = {}
    sublayer_norm: dict[str, list[list[float]]] = {}
    for cat in CATEGORIES:
        weights = layout["sublayer_legislative_weights"][cat]
        nonleg = layout["sublayer_nonlegislative_values"][cat]
        values = [
            [
                round(
                    nonleg[index]
                    + sum(
                        normalized[cat][index][offset] * weights[offset]
                        for offset in range(5)
                    ),
                    12,
                )
            ]
            for index in range(len(members))
        ]
        sublayer_raw[cat] = values
        sublayer_norm[cat] = positive_max_normalize(values)

    efficiency_raw_rows = [[] for _ in members]
    for slot in layout["efficiency_slots"]:
        cats = slot["categories"]
        for index in range(len(members)):
            values = [normalized[cat][index][5] for cat in cats]
            value = (
                values[0]
                if slot["aggregation"] == "identity"
                else sum(values) / len(values)
            )
            efficiency_raw_rows[index].append(round(value, 12))
    efficiency_norm_rows = positive_max_normalize(efficiency_raw_rows)
    return (
        {
            "raw": raw,
            "normalized": normalized,
            "sublayer_raw": sublayer_raw,
            "sublayer_normalized": sublayer_norm,
            "efficiency_raw": efficiency_raw_rows,
            "efficiency_normalized": efficiency_norm_rows,
        },
        {
            "CV_all_zero": all(row[1] == 0 for row in raw["CFPB"]),
            "CX_all_zero": all(row[3] == 0 for row in raw["CFPB"]),
            "CU_source": "manual_LTR sponsor rows only",
            "CW_source": "manual_LTR original-cosponsor rows only",
            "AV_recomputed_uniformly": True,
            "DX_recomputed_uniformly": True,
        },
    )


def writes_from_matrices(
    matrices: Mapping[str, Any],
    layout: Mapping[str, Any],
) -> list[dict[str, Any]]:
    writes: list[dict[str, Any]] = []
    for sheet, key in (
        ("Legislative Score - Raw", "raw"),
        ("Legislative Score - Normalized", "normalized"),
    ):
        for cat in CATEGORIES:
            writes.append(
                {
                    "sheet": sheet,
                    "range": BLOCK_RANGES[cat],
                    "values": matrices[key][cat],
                    "category": cat,
                }
            )
    for sheet, key in (
        ("Sub Layer - Raw", "sublayer_raw"),
        ("Sub Layer - Normalized", "sublayer_normalized"),
    ):
        for cat in CATEGORIES:
            column = CATEGORY_SUBLAYER_COLUMNS[cat]
            writes.append(
                {
                    "sheet": sheet,
                    "range": f"{column}4:{column}536",
                    "values": matrices[key][cat],
                    "category": cat,
                }
            )
        writes.append(
            {
                "sheet": sheet,
                "range": "AG4:AW536",
                "values": matrices[
                    "efficiency_raw"
                    if sheet == "Sub Layer - Raw"
                    else "efficiency_normalized"
                ],
                "layout": layout["efficiency_slots"],
            }
        )
    return writes


def explicit_efficiency_layout(
    slots: Sequence[Mapping[str, Any]],
    cu_tax_treatment: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose every category's destination, including approved aggregation."""
    category_mappings: dict[str, dict[str, Any]] = {}
    for slot in slots:
        column = str(slot["column"])
        cats = list(slot["categories"])
        for cat in cats:
            if cat in category_mappings:
                raise NumericPlanError(
                    f"efficiency category mapped more than once: {cat}"
                )
            category_mappings[cat] = {
                "sheet": "Sub Layer - Raw",
                "range": f"{column}4:{column}536",
                "column": column,
                "aggregation": slot["aggregation"],
                "aggregate_with": [
                    other for other in cats if other != cat
                ],
                "source_metric": "normalized_generic_success_rate",
            }
    if (
        cu_tax_treatment["selected_treatment"]
        == CU_TAX_PRESERVE_OMISSION
    ):
        category_mappings[CU_TAX_CATEGORY] = {
            "sheet": "Legislative Score - Raw",
            "range": CU_TAX_RAW_GENERIC_RANGE,
            "normalized_sheet": "Legislative Score - Normalized",
            "normalized_range": CU_TAX_NORMALIZED_GENERIC_RANGE,
            "column": "FB",
            "aggregation": "identity",
            "aggregate_with": [],
            "source_metric": "generic_success_rate",
            "materialization": "outside_legacy_efficiency_surface",
        }
    if set(category_mappings) != set(CATEGORIES):
        raise NumericPlanError(
            "machine-readable efficiency layout is not complete"
        )
    return {
        "raw_range": "Sub Layer - Raw!AG4:AW536",
        "normalized_range": "Sub Layer - Normalized!AG4:AW536",
        "physical_column_count": 17,
        "category_count": 18,
        "legacy_materialized_category_count": sum(
            len(slot["categories"]) for slot in slots
        ),
        "cu_tax_efficiency_treatment": dict(cu_tax_treatment),
        "slots": [dict(slot) for slot in slots],
        "category_mappings": {
            cat: category_mappings[cat] for cat in CATEGORIES
        },
    }


def build_plan(
    *,
    accepted_ledger: Path,
    directions_ledger: Path,
    manual_evidence: Path,
    layout_path: Path,
    policy_path: Path,
    baseline_manifest: Path,
) -> dict[str, Any]:
    input_paths = {
        "accepted_relationship_ledger": accepted_ledger.resolve(),
        "scoring_direction_ledger": directions_ledger.resolve(),
        "manual_bill_evidence_csv": manual_evidence.resolve(),
        "audited_member_layout": layout_path.resolve(),
        "frozen_baseline_manifest": baseline_manifest.resolve(),
    }
    for label, path in input_paths.items():
        if not path.is_file():
            raise NumericPlanError(f"required input is missing: {label}")
    generation_id = sha256_file(input_paths["accepted_relationship_ledger"])
    input_hashes = {
        key: sha256_file(path) for key, path in input_paths.items()
    }
    layout = validate_layout(layout_path, baseline_manifest)
    policy = validate_policy(
        policy_path,
        generation_id=generation_id,
        input_hashes=input_hashes,
        layout=layout,
    )
    layout = dict(layout)
    layout["efficiency_slots"] = policy["efficiency_slots"]
    directions_rows = read_ndjson(directions_ledger)
    directions = direction_map(directions_rows)
    accepted_rows = read_ndjson(accepted_ledger)
    bill_events, accepted_audit = accepted_events(
        accepted_rows,
        generation_id=generation_id,
        directions=directions,
        by_bioguide=layout["by_bioguide"],
        members=layout["members"],
        cfpb_routing=policy["cfpb_routing"],
    )
    letters, manual_audit = ltr_events(
        read_csv(manual_evidence),
        members=layout["members"],
    )
    matrices, special = build_matrices(
        members=layout["members"],
        events=[*bill_events, *letters],
        layout=layout,
        efficiency=policy["efficiency"],
    )
    writes = writes_from_matrices(matrices, layout)
    efficiency_layout = explicit_efficiency_layout(
        layout["efficiency_slots"],
        policy["cu_tax_efficiency_treatment"],
    )
    plan = {
        "schema": SCHEMA,
        "status": "PASS",
        "generation_id": generation_id,
        "staged_only": True,
        "production_written": False,
        "xlsx_opened_or_authored_by_python": False,
        "selected_role_weight_policy": policy[
            "selected_role_weight_policy"
        ],
        "role_weight_policy_reconciled": True,
        "workbook_role_weights_verified": policy[
            "workbook_role_weights_verified"
        ],
        "decay_policy": {
            "weights": {
                str(key): value for key, value in EXPECTED_DECAY.items()
            },
            "applied_to": ["accepted_bill_rows", "manual_LTR_rows"],
        },
        "measure_weight_policy": measure_weight_policy(),
        "all_18_categories_recomputed": True,
        "categories": list(CATEGORIES),
        "unexplained_cell_count": 0,
        "legislative_efficiency_definition": policy["efficiency"],
        "efficiency_layout": efficiency_layout,
        "cfpb_accepted_bill_routing": policy["cfpb_routing"],
        "press_member_alignment": policy["press_member_alignment"],
        "nonbill_numeric_policy": required_nonbill_numeric_policy(
            input_hashes["manual_bill_evidence_csv"]
        ),
        "inputs": {
            key: file_receipt(path)
            for key, path in sorted(input_paths.items())
        },
        "computation_policy_approval": policy["receipt"],
        "frozen_workbook_binding": layout["baseline"],
        "member_layout": {
            "member_count": len(layout["members"]),
            "first_raw_row": 3,
            "last_raw_row": 535,
            "first_sublayer_row": 4,
            "last_sublayer_row": 536,
            "layout_sha256": layout["receipt"]["sha256"],
        },
        "audit": {
            "accepted": accepted_audit,
            "manual_evidence": manual_audit,
            "special_columns": special,
            "direction_edge_count": len(directions),
            "write_count": len(writes),
            "written_numeric_cell_count": sum(
                len(row)
                for write in writes
                for row in write["values"]
            ),
        },
        "writes": writes,
    }
    plan["computation_basis_sha256"] = hashlib.sha256(
        canonical_bytes(
            {
                "generation_id": generation_id,
                "input_sha256": input_hashes,
                "policy_sha256": policy["receipt"]["sha256"],
                "measure_weight_policy": plan["measure_weight_policy"],
                "writes": writes,
            }
        )
    ).hexdigest()
    return plan


def validate_out(path: Path) -> Path:
    resolved = path.resolve()
    allowed = [
        (Path(__file__).resolve().parents[2] / "audits" / "legfix5").resolve(),
        (Path(__file__).resolve().parents[2] / "tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]
    try:
        permitted = any(resolved.is_relative_to(root) for root in allowed)
    except AttributeError:
        permitted = any(
            str(resolved).casefold().startswith(str(root).casefold() + os.sep)
            for root in allowed
        )
    if not permitted:
        raise NumericPlanError(
            "output must remain under audits/legfix5, repo tmp, or OS temp"
        )
    return resolved


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Build a staged numeric legislative workbook write plan."
    )
    result.add_argument("--accepted-ledger", type=Path, required=True)
    result.add_argument("--directions-ledger", type=Path, required=True)
    result.add_argument("--manual-evidence", type=Path, required=True)
    result.add_argument("--layout", type=Path, required=True)
    result.add_argument("--policy-approval", type=Path, required=True)
    result.add_argument("--baseline-manifest", type=Path, required=True)
    result.add_argument("--out", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        out = validate_out(args.out)
        plan = build_plan(
            accepted_ledger=args.accepted_ledger,
            directions_ledger=args.directions_ledger,
            manual_evidence=args.manual_evidence,
            layout_path=args.layout,
            policy_path=args.policy_approval,
            baseline_manifest=args.baseline_manifest,
        )
        atomic_json(out, plan)
    except (NumericPlanError, OSError, ValueError) as exc:
        try:
            out = validate_out(args.out)
            atomic_json(
                out,
                {
                    "schema": SCHEMA,
                    "status": "BLOCKED",
                    "staged_only": True,
                    "production_written": False,
                    "writes": [],
                    "blockers": [str(exc)],
                },
            )
        except (NumericPlanError, OSError):
            pass
        print(f"legislation workbook numeric plan: BLOCKED: {exc}")
        return 2
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

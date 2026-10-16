#!/usr/bin/env python3
"""Build a fail-closed, hash-bound legislation integration plan.

This executable is intentionally incapable of promotion.  It reads normalized
acceptance-gate receipts, physically rehashes them and every input they cite,
re-derives every load-bearing assertion from the specific input artifact the
receipt names for it, checks cross-gate generation bindings, and writes only an
audit plan and its receipt under the workspace ``audits`` directory.

A receipt is never believed on its word.  Each gate must name a required set of
input roles; each numeric assertion must be recomputed from the artifact bound
to its role.  Any assertion that cannot be recomputed is recorded as
``declared_not_derived`` and the plan cannot reach
``READY_FOR_EXPLICIT_INTEGRATION_REVIEW`` while one exists.

Exit codes:
    0  all gates passed; the plan is ready for explicit human review
    2  command/configuration error
    3  a deterministic blocked plan was written
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping

try:
    from lane_file_io import open_ndjson
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import open_ndjson


REQUEST_SCHEMA = "acu-legislation-staged-integration-request-v1"
GATE_RECEIPT_SCHEMA = "acu-legislation-acceptance-gate-receipt-v1"
PLAN_SCHEMA = "acu-legislation-staged-integration-plan-v1"
PLAN_RECEIPT_SCHEMA = "acu-legislation-staged-integration-plan-receipt-v1"
EXPECTED_CANDIDATE_COUNT = 18_557
EXPECTED_MEMBERSHIP_COUNT = 64_004
EXPECTED_QUERY_COUNT = 62
EXPECTED_CATEGORY_COUNT = 19
EXPECTED_CATEGORY_AUDIT_COUNT = EXPECTED_CANDIDATE_COUNT * EXPECTED_CATEGORY_COUNT
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SAFETY_FALSE_FIELDS = (
    "automatic_promotion_allowed",
    "production_write_performed",
    "mainframe_write_performed",
    "sync_or_promotion_performed",
)

GATE_ASSERTIONS: dict[str, dict[str, Any]] = {
    "coverage_reconciliation": {
        "congress_advanced_primary": True,
        "quorum_primary": False,
        "quorum_floor_only": True,
        "registry_query_count": EXPECTED_QUERY_COUNT,
        "completed_query_count": EXPECTED_QUERY_COUNT,
        "candidate_count": EXPECTED_CANDIDATE_COUNT,
        "membership_count": EXPECTED_MEMBERSHIP_COUNT,
        "reconciliation_exact": True,
        "identifier_probe_revalidation_complete": True,
        "historical_probe_revalidation_complete": True,
        "unresolved_coverage_failure_count": 0,
    },
    "evidence_completeness": {
        "candidate_evidence_count": EXPECTED_CANDIDATE_COUNT,
        "membership_evidence_count": EXPECTED_MEMBERSHIP_COUNT,
        "official_title_complete": True,
        "official_summary_or_explicit_absence_complete": True,
        "official_full_text_complete": True,
        "all_official_text_versions_accounted": True,
        "category_count": EXPECTED_CATEGORY_COUNT,
        "candidate_category_audit_count": EXPECTED_CATEGORY_AUDIT_COUNT,
        "stage_actions_complete": True,
        "archive_body_rehash_complete": True,
        "semantic_parser_parity_complete": True,
        "amendment_evidence_closed": True,
        "missing_required_evidence_count": 0,
    },
    "culling_validity_reversibility": {
        "candidate_partition_count": EXPECTED_CANDIDATE_COUNT,
        "membership_partition_count": EXPECTED_MEMBERSHIP_COUNT,
        "reviewed_membership_count": EXPECTED_MEMBERSHIP_COUNT,
        "membership_partition_exact": True,
        "all_candidates_preserved": True,
        "candidate_deletion_performed": False,
        "unresolved_membership_count": 0,
        "flagged_membership_count": 0,
        "flagged_candidate_count": 0,
        "absence_is_neutral": True,
        "junk_ledger_append_only": True,
        "junk_is_reversible": True,
        "restoration_ledger_audited": True,
        "every_global_junk_candidate_fully_excluded": True,
    },
    "role_attribution": {
        "candidate_role_attribution_complete": True,
        "sponsor_attribution_complete": True,
        "original_cosponsor_attribution_complete": True,
        "later_cosponsor_attribution_complete": True,
        "original_cosponsor_marker_semantics_preserved": True,
        "withdrawal_rejoin_history_preserved": True,
        "role_provenance_complete": True,
        "unresolved_role_gap_count": 0,
    },
    "recomputation": {
        "accepted_generation_hash_bound": True,
        "shadow_recomputation_complete": True,
        "shadow_inputs_outputs_hash_bound": True,
        "rank_drift_reviewed": True,
        "presync_audit_passed": True,
        "production_write_performed": False,
        "mainframe_write_performed": False,
        "sync_or_promotion_performed": False,
    },
    "regression_currentness_hash_binding": {
        "full_regression_passed": True,
        "regression_current": True,
        "implementation_inventory_hash_bound": True,
        "test_inventory_hash_bound": True,
        "semantic_timeout_rollovers_current": True,
        "same_accepted_generation_as_recomputation": True,
        "recomputation_receipt_current": True,
        "regression_failure_count": 0,
        "production_write_performed": False,
        "mainframe_write_performed": False,
        "sync_or_promotion_performed": False,
    },
}

DISCOVERY_BOUND_GATES = {
    "coverage_reconciliation",
    "evidence_completeness",
    "culling_validity_reversibility",
    "role_attribution",
}

# ---------------------------------------------------------------------------
# Derived-assertion contract.
#
# A gate receipt used to be believed on its word: it declared a number, the
# number was compared against a hard-coded constant, and the cited inputs were
# only rehashed.  Nothing tied the number to the artifact.  A hand-authored
# receipt claiming ``unresolved_membership_count: 0`` therefore passed while the
# real ledger held every unresolved row.
#
# Every gate now has to (a) name the specific input artifact each load-bearing
# assertion derives from and (b) survive re-derivation of that assertion from
# that artifact by this executable.  A value that cannot be re-derived is
# recorded as ``declared_not_derived`` and blocks the plan.
# ---------------------------------------------------------------------------

GATE_REQUIRED_INPUT_ROLES: dict[str, tuple[str, ...]] = {
    "coverage_reconciliation": (
        "query_registry",
        "candidate_ledger",
        "membership_ledger",
    ),
    "evidence_completeness": (
        "candidate_evidence_ledger",
        "membership_evidence_ledger",
        "category_registry",
        "category_audit_ledger",
    ),
    "culling_validity_reversibility": (
        "candidate_partition_ledger",
        "membership_partition_ledger",
        "junk_ledger",
    ),
    "role_attribution": ("role_attribution_ledger",),
    "recomputation": ("shadow_recompute_receipt",),
    "regression_currentness_hash_binding": ("final_regression_receipt",),
}

# assertion name -> (input role, derivation method, method parameters)
GATE_DERIVATIONS: dict[str, dict[str, tuple[str, str, dict[str, Any]]]] = {
    "coverage_reconciliation": {
        "registry_query_count": ("query_registry", "ndjson_record_count", {}),
        "completed_query_count": (
            "query_registry",
            "ndjson_field_in_count",
            {"field": "collection_status", "values": ["complete"]},
        ),
        "unresolved_coverage_failure_count": (
            "query_registry",
            "ndjson_field_not_in_count",
            {"field": "collection_status", "values": ["complete"]},
        ),
        "candidate_count": (
            "candidate_ledger",
            "ndjson_distinct_field_count",
            {"field": "canonical_key"},
        ),
        "membership_count": ("membership_ledger", "ndjson_record_count", {}),
    },
    "evidence_completeness": {
        "candidate_evidence_count": (
            "candidate_evidence_ledger",
            "ndjson_distinct_field_count",
            {"field": "canonical_key"},
        ),
        "missing_required_evidence_count": (
            "candidate_evidence_ledger",
            "ndjson_field_not_in_count",
            {"field": "required_evidence_status", "values": ["complete"]},
        ),
        "membership_evidence_count": (
            "membership_evidence_ledger",
            "ndjson_record_count",
            {},
        ),
        "category_count": (
            "category_registry",
            "ndjson_distinct_field_count",
            {"field": "category_id"},
        ),
        "candidate_category_audit_count": (
            "category_audit_ledger",
            "ndjson_record_count",
            {},
        ),
    },
    "culling_validity_reversibility": {
        "candidate_partition_count": (
            "candidate_partition_ledger",
            "ndjson_distinct_field_count",
            {"field": "canonical_key"},
        ),
        "flagged_candidate_count": (
            "candidate_partition_ledger",
            "ndjson_field_in_count",
            {"field": "flagged", "values": [True]},
        ),
        "membership_partition_count": (
            "membership_partition_ledger",
            "ndjson_record_count",
            {},
        ),
        "reviewed_membership_count": (
            "membership_partition_ledger",
            "ndjson_field_in_count",
            {"field": "review_disposition", "values": ["include", "exclude"]},
        ),
        "unresolved_membership_count": (
            "membership_partition_ledger",
            "ndjson_field_not_in_count",
            {"field": "review_disposition", "values": ["include", "exclude"]},
        ),
        "flagged_membership_count": (
            "membership_partition_ledger",
            "ndjson_field_in_count",
            {"field": "flagged", "values": [True]},
        ),
    },
    "role_attribution": {
        "unresolved_role_gap_count": (
            "role_attribution_ledger",
            "ndjson_field_not_in_count",
            {"field": "role_attribution_status", "values": ["resolved"]},
        ),
    },
    "recomputation": {
        "shadow_recomputation_complete": (
            "shadow_recompute_receipt",
            "json_pointer_equals",
            {"pointer": "/acceptance_passed", "value": True},
        ),
        "rank_drift_reviewed": (
            "shadow_recompute_receipt",
            "json_pointer_equals",
            {"pointer": "/rank_drift_reviewed", "value": True},
        ),
    },
    "regression_currentness_hash_binding": {
        "regression_failure_count": (
            "final_regression_receipt",
            "json_pointer_value",
            {"pointer": "/actual_implementation_regression_count"},
        ),
        "full_regression_passed": (
            "final_regression_receipt",
            "json_pointer_equals",
            {"pointer": "/status", "value": "PASS_CURRENT"},
        ),
    },
}


def _is_numeric_assertion(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_bearing_assertions(gate_id: str) -> tuple[str, ...]:
    """Assertions that may never be believed on the receipt's word alone.

    Every numeric assertion is load bearing (counts are the values a forged
    receipt would lie about), plus any assertion this module knows how to
    re-derive.
    """

    names = {
        name
        for name, expected in GATE_ASSERTIONS[gate_id].items()
        if _is_numeric_assertion(expected)
    }
    names.update(GATE_DERIVATIONS.get(gate_id, {}))
    return tuple(sorted(names))


def _validate_derivation_tables() -> None:
    """Fail at import if the tables ever drift out of alignment.

    This is what keeps the contract honest as gates are added: a new numeric
    assertion without a derivation is a build error, not a silent trust hole.
    """

    for gate_id, assertions in GATE_ASSERTIONS.items():
        derivations = GATE_DERIVATIONS.get(gate_id, {})
        required_roles = set(GATE_REQUIRED_INPUT_ROLES.get(gate_id, ()))
        if not required_roles:
            raise GateContractError(
                f"{gate_id}: no required input roles are declared"
            )
        for name, expected in assertions.items():
            if _is_numeric_assertion(expected) and name not in derivations:
                raise GateContractError(
                    f"{gate_id}: numeric assertion {name} has no derivation"
                )
        for name, (role, method, params) in derivations.items():
            if name not in assertions:
                raise GateContractError(
                    f"{gate_id}: derivation for unknown assertion {name}"
                )
            if role not in required_roles:
                raise GateContractError(
                    f"{gate_id}: derivation of {name} cites undeclared "
                    f"input role {role}"
                )
            if method not in DERIVATION_METHODS:
                raise GateContractError(
                    f"{gate_id}: derivation of {name} uses unknown method "
                    f"{method}"
                )
            del params


class GateError(ValueError):
    """A deterministic integration-gate validation failure."""


class GateContractError(RuntimeError):
    """The gate's own constant tables are internally inconsistent."""


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
        raise GateError(f"expected a JSON object: {path}")
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


def _under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _verify_file_receipt(
    value: Any,
    *,
    workspace_root: Path,
    label: str,
) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return None, [f"{label}: file receipt is not an object"]
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None, [f"{label}: path is absent"]
    path = Path(raw_path)
    if not path.is_absolute():
        path = workspace_root / path
    path = path.resolve()
    if not _under(path, workspace_root):
        errors.append(f"{label}: path escapes workspace root: {path}")
    if not path.is_file():
        errors.append(f"{label}: file does not exist: {path}")
        return path, errors
    expected_bytes = value.get("bytes")
    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int):
        errors.append(f"{label}: bytes is not an integer")
    elif expected_bytes != path.stat().st_size:
        errors.append(f"{label}: byte count is stale")
    expected_sha = str(value.get("sha256") or "").casefold()
    if not _valid_sha(expected_sha):
        errors.append(f"{label}: SHA-256 is invalid")
    elif expected_sha != sha256_file(path):
        errors.append(f"{label}: SHA-256 is stale")
    return path, errors


class DerivationFailure(Exception):
    """The cited artifact could not yield the assertion's value."""


def _iter_ndjson(path: Path):
    # ``open_ndjson`` reads as utf-8-sig so a PowerShell-written file with a
    # leading UTF-8 BOM parses instead of failing on line 1.
    with open_ndjson(path) as stream:
        for number, line in enumerate(stream, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise DerivationFailure(
                    f"{path.name}: line {number} is not JSON: {exc}"
                ) from exc
            if not isinstance(record, dict):
                raise DerivationFailure(
                    f"{path.name}: line {number} is not a JSON object"
                )
            yield record


def _derive_ndjson_record_count(path: Path, _params: Mapping[str, Any]) -> int:
    return sum(1 for _record in _iter_ndjson(path))


def _derive_ndjson_field_in_count(
    path: Path, params: Mapping[str, Any]
) -> int:
    field = str(params["field"])
    values = list(params["values"])
    return sum(
        1
        for record in _iter_ndjson(path)
        if any(record.get(field) == value for value in values)
    )


def _derive_ndjson_field_not_in_count(
    path: Path, params: Mapping[str, Any]
) -> int:
    field = str(params["field"])
    values = list(params["values"])
    return sum(
        1
        for record in _iter_ndjson(path)
        if not any(record.get(field) == value for value in values)
    )


def _derive_ndjson_distinct_field_count(
    path: Path, params: Mapping[str, Any]
) -> int:
    field = str(params["field"])
    seen: set[str] = set()
    for record in _iter_ndjson(path):
        value = record.get(field)
        if value is None or value == "":
            raise DerivationFailure(
                f"{path.name}: record is missing {field}"
            )
        seen.add(json.dumps(value, sort_keys=True))
    return len(seen)


def _json_pointer(path: Path, pointer: str) -> Any:
    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DerivationFailure(f"{path.name}: unreadable JSON: {exc}") from exc
    for token in pointer.split("/"):
        if not token:
            continue
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(document, Mapping):
            if token not in document:
                raise DerivationFailure(
                    f"{path.name}: pointer {pointer} is absent"
                )
            document = document[token]
        elif isinstance(document, list):
            try:
                document = document[int(token)]
            except (ValueError, IndexError) as exc:
                raise DerivationFailure(
                    f"{path.name}: pointer {pointer} is absent"
                ) from exc
        else:
            raise DerivationFailure(
                f"{path.name}: pointer {pointer} is absent"
            )
    return document


def _derive_json_pointer_value(path: Path, params: Mapping[str, Any]) -> Any:
    return _json_pointer(path, str(params["pointer"]))


def _derive_json_pointer_equals(path: Path, params: Mapping[str, Any]) -> bool:
    return _json_pointer(path, str(params["pointer"])) == params["value"]


DERIVATION_METHODS = {
    "ndjson_record_count": _derive_ndjson_record_count,
    "ndjson_field_in_count": _derive_ndjson_field_in_count,
    "ndjson_field_not_in_count": _derive_ndjson_field_not_in_count,
    "ndjson_distinct_field_count": _derive_ndjson_distinct_field_count,
    "json_pointer_value": _derive_json_pointer_value,
    "json_pointer_equals": _derive_json_pointer_equals,
}

# Enforced at import: no gate may ship a numeric assertion nobody re-derives.
_validate_derivation_tables()


def _derive_assertions(
    gate_id: str,
    assertions: Any,
    role_paths: Mapping[str, Path],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Re-derive every load-bearing assertion from its named input artifact.

    Returns ``(derived, declared_not_derived, errors)``.  A value that could
    not be recomputed lands in ``declared_not_derived``; the caller must treat
    that as fatal, not as a soft warning.
    """

    declared = assertions if isinstance(assertions, Mapping) else {}
    derivations = GATE_DERIVATIONS.get(gate_id, {})
    derived: dict[str, Any] = {}
    not_derived: list[str] = []
    errors: list[str] = []
    for name in load_bearing_assertions(gate_id):
        recipe = derivations.get(name)
        if recipe is None:
            not_derived.append(name)
            errors.append(
                f"{gate_id}: assertion {name} is load bearing but has no "
                "declared derivation"
            )
            continue
        role, method, params = recipe
        path = role_paths.get(role)
        if path is None:
            not_derived.append(name)
            errors.append(
                f"{gate_id}: assertion {name} cannot be derived; input role "
                f"{role} was not supplied"
            )
            continue
        try:
            value = DERIVATION_METHODS[method](path, params)
        except (DerivationFailure, OSError, KeyError, TypeError, ValueError) as exc:
            not_derived.append(name)
            errors.append(
                f"{gate_id}: assertion {name} could not be derived from "
                f"{role}: {exc}"
            )
            continue
        derived[name] = {
            "input_role": role,
            "input_path": str(path),
            "method": method,
            "derived_value": value,
            "declared_value": declared.get(name),
        }
        if declared.get(name) != value:
            errors.append(
                f"{gate_id}: assertion {name} declared "
                f"{declared.get(name)!r} but derives to {value!r} from "
                f"input role {role}"
            )
    return derived, sorted(not_derived), errors


def _exact_assertions(
    gate_id: str,
    assertions: Any,
) -> list[str]:
    if not isinstance(assertions, Mapping):
        return [f"{gate_id}: assertions is not an object"]
    errors: list[str] = []
    for name, expected in GATE_ASSERTIONS[gate_id].items():
        if name not in assertions:
            errors.append(f"{gate_id}: missing assertion {name}")
        elif assertions.get(name) != expected:
            errors.append(
                f"{gate_id}: assertion {name} expected {expected!r}, "
                f"found {assertions.get(name)!r}"
            )
    return errors


def _verify_gate(
    gate_id: str,
    reference: Any,
    *,
    workspace_root: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str]]:
    result: dict[str, Any] = {
        "gate_id": gate_id,
        "verified": False,
        "receipt": reference if isinstance(reference, Mapping) else None,
        "input_count": 0,
        "required_input_roles": list(GATE_REQUIRED_INPUT_ROLES.get(gate_id, ())),
        "declared_input_roles": [],
        "load_bearing_assertions": list(load_bearing_assertions(gate_id)),
        "derived_assertions": {},
        "declared_not_derived_assertions": list(
            load_bearing_assertions(gate_id)
        ),
        "all_load_bearing_assertions_derived": False,
    }
    path, errors = _verify_file_receipt(
        reference,
        workspace_root=workspace_root,
        label=f"{gate_id}.receipt",
    )
    if path is None or errors:
        return None, result, errors
    try:
        receipt = read_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, result, [f"{gate_id}: unreadable receipt: {exc}"]
    if receipt.get("schema") != GATE_RECEIPT_SCHEMA:
        errors.append(f"{gate_id}: gate receipt schema drift")
    if receipt.get("gate_id") != gate_id:
        errors.append(f"{gate_id}: gate_id mismatch in receipt")
    if receipt.get("status") != "PASS":
        errors.append(f"{gate_id}: status is not PASS")
    for field in SAFETY_FALSE_FIELDS[1:]:
        if receipt.get(field) is not False:
            errors.append(f"{gate_id}: {field} is not false")
    errors.extend(_exact_assertions(gate_id, receipt.get("assertions")))

    inputs = receipt.get("input_files")
    if not isinstance(inputs, list) or not inputs:
        errors.append(f"{gate_id}: input_files must be a nonempty list")
        inputs = []
    result["input_count"] = len(inputs)
    required_roles = GATE_REQUIRED_INPUT_ROLES.get(gate_id, ())
    role_paths: dict[str, Path] = {}
    for index, item in enumerate(inputs):
        label = f"{gate_id}.input_files[{index}]"
        input_path, input_errors = _verify_file_receipt(
            item,
            workspace_root=workspace_root,
            label=label,
        )
        errors.extend(input_errors)
        role = item.get("role") if isinstance(item, Mapping) else None
        if not isinstance(role, str) or not role.strip():
            errors.append(
                f"{label}: input_files entries must name the role they play "
                f"for this gate (one of {sorted(required_roles)})"
            )
            continue
        role = role.strip()
        if role in role_paths:
            errors.append(f"{label}: duplicate input role {role}")
            continue
        if input_errors or input_path is None:
            # A stale or missing artifact must never be re-derived from.
            continue
        role_paths[role] = input_path
    result["declared_input_roles"] = sorted(role_paths)
    for missing in sorted(set(required_roles) - set(role_paths)):
        errors.append(
            f"{gate_id}: required input role {missing} is absent or unusable; "
            "its assertions cannot be derived"
        )

    derived, not_derived, derivation_errors = _derive_assertions(
        gate_id,
        receipt.get("assertions"),
        role_paths,
    )
    errors.extend(derivation_errors)
    result["derived_assertions"] = derived
    result["declared_not_derived_assertions"] = not_derived
    result["all_load_bearing_assertions_derived"] = not not_derived
    if not_derived:
        errors.append(
            f"{gate_id}: load-bearing assertions are declared-not-derived: "
            + ", ".join(not_derived)
        )
    result["verified"] = not errors and not not_derived
    result["receipt"] = file_receipt(path)
    return receipt, result, errors


def evaluate_request(
    request: Any,
    *,
    workspace_root: Path,
    request_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a staged-integration request without mutating production."""

    errors: list[str] = []
    if not isinstance(request, Mapping):
        request = {}
        errors.append("request is not a JSON object")
    if request.get("schema") != REQUEST_SCHEMA:
        errors.append("request schema drift")
    if request.get("candidate_count") != EXPECTED_CANDIDATE_COUNT:
        errors.append(
            f"candidate_count must be exactly {EXPECTED_CANDIDATE_COUNT}"
        )
    if request.get("membership_count") != EXPECTED_MEMBERSHIP_COUNT:
        errors.append(
            f"membership_count must be exactly {EXPECTED_MEMBERSHIP_COUNT}"
        )
    safety = request.get("safety")
    if not isinstance(safety, Mapping):
        safety = {}
        errors.append("safety is not an object")
    if safety.get("integration_plan_only") is not True:
        errors.append("safety.integration_plan_only is not true")
    for field in SAFETY_FALSE_FIELDS:
        if safety.get(field) is not False:
            errors.append(f"safety.{field} is not false")

    references = request.get("gates")
    if not isinstance(references, Mapping):
        references = {}
        errors.append("gates is not an object")
    expected_gate_ids = set(GATE_ASSERTIONS)
    observed_gate_ids = set(references)
    for missing in sorted(expected_gate_ids - observed_gate_ids):
        errors.append(f"missing required gate: {missing}")
    for unexpected in sorted(observed_gate_ids - expected_gate_ids):
        errors.append(f"unexpected gate: {unexpected}")

    receipts: dict[str, dict[str, Any]] = {}
    gate_results: dict[str, dict[str, Any]] = {}
    for gate_id in GATE_ASSERTIONS:
        if gate_id not in references:
            gate_results[gate_id] = {
                "gate_id": gate_id,
                "verified": False,
                "receipt": None,
                "input_count": 0,
                "required_input_roles": list(
                    GATE_REQUIRED_INPUT_ROLES.get(gate_id, ())
                ),
                "declared_input_roles": [],
                "load_bearing_assertions": list(
                    load_bearing_assertions(gate_id)
                ),
                "derived_assertions": {},
                "declared_not_derived_assertions": list(
                    load_bearing_assertions(gate_id)
                ),
                "all_load_bearing_assertions_derived": False,
            }
            continue
        receipt, result, gate_errors = _verify_gate(
            gate_id,
            references[gate_id],
            workspace_root=workspace_root,
        )
        gate_results[gate_id] = result
        errors.extend(gate_errors)
        if receipt is not None:
            receipts[gate_id] = receipt

    discovery_hashes: dict[str, str] = {}
    for gate_id in DISCOVERY_BOUND_GATES:
        receipt = receipts.get(gate_id) or {}
        bindings = receipt.get("bindings")
        value = (
            bindings.get("discovery_generation_sha256")
            if isinstance(bindings, Mapping)
            else None
        )
        if not _valid_sha(value):
            errors.append(
                f"{gate_id}: bindings.discovery_generation_sha256 is invalid"
            )
        else:
            discovery_hashes[gate_id] = str(value).casefold()
    if len(set(discovery_hashes.values())) > 1:
        errors.append("discovery generation binding differs across gates")

    recomputation = receipts.get("recomputation") or {}
    regression = receipts.get("regression_currentness_hash_binding") or {}
    recompute_bindings = recomputation.get("bindings")
    regression_bindings = regression.get("bindings")
    recompute_accepted = (
        recompute_bindings.get("accepted_generation_sha256")
        if isinstance(recompute_bindings, Mapping)
        else None
    )
    regression_accepted = (
        regression_bindings.get("accepted_generation_sha256")
        if isinstance(regression_bindings, Mapping)
        else None
    )
    if not _valid_sha(recompute_accepted):
        errors.append(
            "recomputation: bindings.accepted_generation_sha256 is invalid"
        )
    if not _valid_sha(regression_accepted):
        errors.append(
            "regression_currentness_hash_binding: "
            "bindings.accepted_generation_sha256 is invalid"
        )
    if (
        _valid_sha(recompute_accepted)
        and _valid_sha(regression_accepted)
        and str(recompute_accepted).casefold() != str(regression_accepted).casefold()
    ):
        errors.append("accepted generation binding differs across final gates")

    recomputation_ref = gate_results.get("recomputation", {}).get("receipt")
    expected_recompute_receipt_sha = (
        recomputation_ref.get("sha256")
        if isinstance(recomputation_ref, Mapping)
        else None
    )
    actual_recompute_receipt_sha = (
        regression_bindings.get("recomputation_gate_receipt_sha256")
        if isinstance(regression_bindings, Mapping)
        else None
    )
    if not _valid_sha(actual_recompute_receipt_sha):
        errors.append(
            "regression_currentness_hash_binding: "
            "bindings.recomputation_gate_receipt_sha256 is invalid"
        )
    elif actual_recompute_receipt_sha != expected_recompute_receipt_sha:
        errors.append(
            "regression receipt is not bound to the current recomputation gate receipt"
        )

    load_bearing_total = sum(
        len(load_bearing_assertions(gate_id)) for gate_id in GATE_ASSERTIONS
    )
    derived_total = sum(
        len(result.get("derived_assertions") or {})
        for result in gate_results.values()
    )
    underived = sorted(
        f"{gate_id}.{name}"
        for gate_id, result in gate_results.items()
        for name in (result.get("declared_not_derived_assertions") or [])
    )
    assertion_provenance = {
        "load_bearing_assertion_count": load_bearing_total,
        "derived_assertion_count": derived_total,
        "declared_not_derived_assertions": underived,
        "declared_not_derived_assertion_count": len(underived),
        "all_load_bearing_assertions_derived": not underived,
    }

    passed = (
        not errors
        and not underived
        and all(
            result.get("verified") is True for result in gate_results.values()
        )
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema": PLAN_SCHEMA,
        "generated_at": generated_at,
        "status": (
            "READY_FOR_EXPLICIT_INTEGRATION_REVIEW" if passed else "BLOCKED"
        ),
        "candidate_count": EXPECTED_CANDIDATE_COUNT,
        "membership_count": EXPECTED_MEMBERSHIP_COUNT,
        "request": dict(request_receipt or {}),
        "gates": gate_results,
        "assertion_provenance": assertion_provenance,
        "blockers": sorted(set(errors)),
        "all_required_gates_passed": passed,
        "integration_plan_only": True,
        "explicit_human_review_still_required": True,
        "automatic_promotion_allowed": False,
        "production_write_authorized": False,
        "mainframe_write_authorized": False,
        "promotion_command": None,
        "production_write_performed": False,
        "mainframe_write_performed": False,
        "sync_or_promotion_performed": False,
    }


def _validate_output_dir(output_dir: Path, workspace_root: Path) -> Path:
    resolved = output_dir.resolve()
    audits_root = (workspace_root / "audits").resolve()
    if not _under(resolved, audits_root):
        raise GateError(
            f"output directory must remain under workspace audits: {audits_root}"
        )
    return resolved


def run(
    request_path: Path,
    output_dir: Path,
    *,
    workspace_root: Path,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    """Write a plan and receipt only; return the process exit code."""

    workspace_root = workspace_root.resolve()
    output_dir = _validate_output_dir(output_dir, workspace_root)
    request_error: str | None = None
    request: dict[str, Any] = {}
    request_file_receipt: dict[str, Any] | None = None
    try:
        resolved_request = request_path.resolve()
        if not _under(resolved_request, workspace_root):
            raise GateError("request path escapes workspace root")
        request = read_object(resolved_request)
        request_file_receipt = file_receipt(resolved_request)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        request_error = f"request could not be loaded: {exc}"

    plan = evaluate_request(
        request,
        workspace_root=workspace_root,
        request_receipt=request_file_receipt,
    )
    if request_error:
        plan["blockers"] = sorted(set([*plan["blockers"], request_error]))
        plan["status"] = "BLOCKED"
        plan["all_required_gates_passed"] = False

    plan_path = output_dir / "integration_plan.json"
    atomic_json(plan_path, plan)
    plan_file_receipt = file_receipt(plan_path)
    receipt = {
        "schema": PLAN_RECEIPT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": plan["status"],
        "plan": plan_file_receipt,
        "plan_semantic_sha256": stable_sha256(plan),
        "request": request_file_receipt,
        "implementation": file_receipt(Path(__file__)),
        "all_required_gates_passed": plan["all_required_gates_passed"],
        "assertion_provenance": plan["assertion_provenance"],
        "integration_plan_only": True,
        "explicit_human_review_still_required": True,
        "automatic_promotion_allowed": False,
        "production_write_authorized": False,
        "mainframe_write_authorized": False,
        "production_write_performed": False,
        "mainframe_write_performed": False,
        "sync_or_promotion_performed": False,
    }
    atomic_json(output_dir / "receipt.json", receipt)
    return (0 if plan["all_required_gates_passed"] else 3), plan, receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        code, plan, _receipt = run(
            args.request,
            args.out,
            workspace_root=args.workspace_root,
        )
    except GateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": plan["status"],
                "blocker_count": len(plan["blockers"]),
                "output": str(args.out.resolve()),
                "mainframe_write_performed": False,
            },
            sort_keys=True,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())

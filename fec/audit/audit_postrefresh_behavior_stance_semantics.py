#!/usr/bin/env python3
"""Seal domain-semantic frozen/live drift for behavior, receptivity, and stance."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
from typing import Any, Iterable
import uuid


ROOT = Path(__file__).resolve().parents[2]
DRIFT_ROOT = (
    ROOT / "audits" / "legfix5" / "post_weekly_refresh_downstream_drift"
)
DEFAULT_DRIFT_POINTER = DRIFT_ROOT / "current.json"
DEFAULT_OUT_ROOT = (
    ROOT / "audits" / "legfix5"
    / "post_weekly_refresh_behavior_stance_semantics"
)
# 2026-08-04: Pinned downstream inventory re-attested after its allowance list
# grew 20 -> 36 (all additions named to authorized workstreams in
# audit_postrefresh_downstream_drift.py).
# 2026-08-05: Re-attested after Phase 2+3 relocation-aware closure 36 -> 47.
# This pin is a trust root and must be updated in lockstep with deliberate
# re-attestations, never loosened to a pattern.
DEFAULT_EXPECTED_DRIFT_AUDIT_ID = (
    "b51dc6811e1bcbb9dcd99e303f6dae0fefbd2ff98cbbeacdff83189f40bd9f67"
)
TARGET_NAMES = (
    "behavior_panel.json",
    "behavioral_dispositions.json",
    "member_receptivity.json",
    "category_stance.json",
)
MEMBER_ARTIFACTS = TARGET_NAMES[:3]
STANCE_MEMBER_SECTIONS = ("matrix", "scores", "raw_scores")
STANCE_NUMERIC_SECTIONS = ("scores", "raw_scores", "rounding.factors")
AUTHORITY_FIELDS = (
    "authoritative_for_legislation",
    "authoritative_for_scoring",
    "authoritative_for_sync_or_promotion",
)
OUTPUT_FILENAMES = {
    "artifact_semantics": "artifact_semantics.ndjson",
    "numeric_field_drift": "numeric_field_drift.ndjson",
    "identity_closure": "identity_closure.ndjson",
}


class AuditError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def file_item(path: Path, record_count: int | None = None) -> dict[str, Any]:
    require(path.is_file(), f"Missing bound file: {path}")
    result = {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if record_count is not None:
        result["record_count"] = record_count
    return result


def receipt_current(item: Any, expected_path: Path) -> bool:
    if not isinstance(item, dict):
        return False
    expected_path = expected_path.resolve()
    try:
        recorded_path = Path(str(item.get("path") or "")).resolve()
    except (OSError, TypeError, ValueError):
        return False
    return bool(
        expected_path.is_file()
        and recorded_path == expected_path
        and item.get("bytes") == expected_path.stat().st_size
        and item.get("sha256") == sha256_file(expected_path)
    )


def authority_is_false(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and all(value.get(field) is False for field in AUTHORITY_FIELDS)
    )


def authority_contract() -> dict[str, bool]:
    return {field: False for field in AUTHORITY_FIELDS}


def read_json(path: Path) -> Any:
    require(path.is_file(), f"Missing JSON input: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"Unreadable JSON input {path}: {exc}") from exc


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    require(path.is_file(), f"Missing NDJSON input: {path}")
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise AuditError(
                f"Invalid NDJSON at {path}:{line_number}: {exc}"
            ) from exc
        require(isinstance(row, dict), f"Non-object NDJSON row: {path}")
        rows.append(row)
    return rows


def resolve_bound_path(value: str, parent: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else parent / path


def write_if_changed(path: Path, body: bytes) -> bool:
    if path.is_file() and path.read_bytes() == body:
        return False
    path.write_bytes(body)
    return True


def is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def quantile(sorted_values: list[float], probability: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    weight = index - lower
    return (
        sorted_values[lower] * (1.0 - weight)
        + sorted_values[upper] * weight
    )


def summarize_deltas(deltas: Iterable[float]) -> dict[str, Any]:
    values = [float(value) for value in deltas]
    changed = [value for value in values if value != 0.0]
    absolute = sorted(abs(value) for value in changed)
    return {
        "numeric_pair_count": len(values),
        "numeric_changed_count": len(changed),
        "numeric_unchanged_count": len(values) - len(changed),
        "signed_delta_min": min(changed) if changed else 0.0,
        "signed_delta_max": max(changed) if changed else 0.0,
        "signed_delta_mean": (
            sum(changed) / len(changed) if changed else 0.0
        ),
        "absolute_delta_max": max(absolute) if absolute else 0.0,
        "absolute_delta_mean": (
            sum(absolute) / len(absolute) if absolute else 0.0
        ),
        "absolute_delta_median": quantile(absolute, 0.5) or 0.0,
        "absolute_delta_p95": quantile(absolute, 0.95) or 0.0,
    }


def type_label(value: Any) -> str:
    if value is None:
        return "null"
    if is_number(value):
        return "number"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def recursive_change_summary(old: Any, new: Any) -> dict[str, Any]:
    counts = {
        "added_path_count": 0,
        "removed_path_count": 0,
        "null_transition_count": 0,
        "type_drift_count": 0,
        "numeric_pair_count": 0,
        "numeric_changed_count": 0,
        "nonnumeric_pair_count": 0,
        "nonnumeric_changed_count": 0,
    }
    numeric_deltas: list[float] = []
    samples: list[dict[str, Any]] = []

    def add_sample(path: str, change: str, before: Any, after: Any) -> None:
        if len(samples) < 100:
            samples.append(
                {
                    "path": path or "$",
                    "change": change,
                    "frozen_type": type_label(before),
                    "live_type": type_label(after),
                }
            )

    def compare(before: Any, after: Any, path: str = "") -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            before_keys = set(before)
            after_keys = set(after)
            for key in sorted(before_keys - after_keys):
                counts["removed_path_count"] += 1
                add_sample(f"{path}.{key}".lstrip("."), "removed", before[key], None)
            for key in sorted(after_keys - before_keys):
                counts["added_path_count"] += 1
                add_sample(f"{path}.{key}".lstrip("."), "added", None, after[key])
            for key in sorted(before_keys & after_keys):
                compare(
                    before[key],
                    after[key],
                    f"{path}.{key}".lstrip("."),
                )
            return
        if isinstance(before, list) and isinstance(after, list):
            common = min(len(before), len(after))
            for index in range(common):
                compare(before[index], after[index], f"{path}[{index}]")
            for index in range(common, len(before)):
                counts["removed_path_count"] += 1
                add_sample(f"{path}[{index}]", "removed", before[index], None)
            for index in range(common, len(after)):
                counts["added_path_count"] += 1
                add_sample(f"{path}[{index}]", "added", None, after[index])
            return
        if before is None or after is None:
            if before != after:
                counts["null_transition_count"] += 1
                counts["type_drift_count"] += 1
                add_sample(path, "null_transition", before, after)
            else:
                counts["nonnumeric_pair_count"] += 1
            return
        if is_number(before) and is_number(after):
            delta = float(after) - float(before)
            numeric_deltas.append(delta)
            counts["numeric_pair_count"] += 1
            if delta != 0.0:
                counts["numeric_changed_count"] += 1
            return
        if type_label(before) != type_label(after):
            counts["type_drift_count"] += 1
            add_sample(path, "type_drift", before, after)
            return
        counts["nonnumeric_pair_count"] += 1
        if before != after:
            counts["nonnumeric_changed_count"] += 1
            add_sample(path, "nonnumeric_value_changed", before, after)

    compare(old, new)
    delta_summary = summarize_deltas(numeric_deltas)
    for key in ("numeric_pair_count", "numeric_changed_count"):
        require(
            counts[key] == delta_summary[key],
            "Internal numeric change-count mismatch",
        )
    sampled_change_count = (
        counts["added_path_count"]
        + counts["removed_path_count"]
        + counts["type_drift_count"]
        + counts["nonnumeric_changed_count"]
    )
    return {
        **counts,
        "numeric_delta_distribution": delta_summary,
        "change_sample": samples,
        "change_sample_eligible_count": sampled_change_count,
        "change_sample_complete": len(samples) == sampled_change_count,
    }


def object_keys(value: Any, label: str) -> set[str]:
    require(isinstance(value, dict), f"{label} must be an object")
    require(
        all(isinstance(key, str) and key for key in value),
        f"{label} contains invalid keys",
    )
    return set(value)


def require_exact_keys(
    observed: set[str], expected: set[str], label: str
) -> None:
    require(
        observed == expected,
        f"{label} key closure failed: "
        f"added={sorted(observed - expected)} "
        f"removed={sorted(expected - observed)}",
    )


def member_field_rows(
    name: str,
    frozen_members: dict[str, Any],
    live_members: dict[str, Any],
    members: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frozen_fields: set[str] = set()
    live_fields: set[str] = set()
    added_field_paths: list[dict[str, str]] = []
    removed_field_paths: list[dict[str, str]] = []
    for member in members:
        before = object_keys(
            frozen_members[member], f"{name} frozen member {member}"
        )
        after = object_keys(
            live_members[member], f"{name} live member {member}"
        )
        for field in before:
            require(
                not isinstance(frozen_members[member][field], (dict, list)),
                f"{name} frozen member field is not scalar: "
                f"{member}.{field}",
            )
        for field in after:
            require(
                not isinstance(live_members[member][field], (dict, list)),
                f"{name} live member field is not scalar: "
                f"{member}.{field}",
            )
        added_field_paths.extend(
            {"member": member, "field": field}
            for field in sorted(after - before)
        )
        removed_field_paths.extend(
            {"member": member, "field": field}
            for field in sorted(before - after)
        )
        frozen_fields.update(before)
        live_fields.update(after)
    all_fields = frozen_fields | live_fields

    rows: list[dict[str, Any]] = []
    nonnumeric_changes = 0
    null_transitions = 0
    type_drifts = 0
    for field in sorted(all_fields):
        deltas = []
        missing_frozen_count = 0
        missing_live_count = 0
        nonnumeric_pairs = 0
        nonnumeric_changed = 0
        field_null_transitions = 0
        field_type_drifts = 0
        samples = []
        for member in members:
            before_present = field in frozen_members[member]
            after_present = field in live_members[member]
            if not before_present:
                if after_present:
                    missing_frozen_count += 1
                continue
            if not after_present:
                missing_live_count += 1
                continue
            before = frozen_members[member][field]
            after = live_members[member][field]
            if before is None or after is None:
                if before != after:
                    field_null_transitions += 1
                    field_type_drifts += 1
                    if len(samples) < 20:
                        samples.append(
                            {
                                "member": member,
                                "frozen": before,
                                "live": after,
                            }
                        )
                else:
                    nonnumeric_pairs += 1
                continue
            if is_number(before) and is_number(after):
                deltas.append(float(after) - float(before))
                continue
            if type_label(before) != type_label(after):
                field_type_drifts += 1
                if len(samples) < 20:
                    samples.append(
                        {
                            "member": member,
                            "frozen_type": type_label(before),
                            "live_type": type_label(after),
                        }
                    )
                continue
            nonnumeric_pairs += 1
            if before != after:
                nonnumeric_changed += 1
                if len(samples) < 20:
                    samples.append(
                        {
                            "member": member,
                            "frozen": before,
                            "live": after,
                        }
                    )
        nonnumeric_changes += nonnumeric_changed
        null_transitions += field_null_transitions
        type_drifts += field_type_drifts
        rows.append(
            {
                "schema": "post-weekly-refresh-numeric-field-drift-v1",
                "artifact": name,
                "domain": "members",
                "field": field,
                "grouping_method": "artifact+domain+field_across_members",
                "path_template": f"members.<member>.{field}",
                "paired_numeric_values_only": True,
                **summarize_deltas(deltas),
                "missing_frozen_path_count": missing_frozen_count,
                "missing_live_path_count": missing_live_count,
                "nonnumeric_pair_count": nonnumeric_pairs,
                "nonnumeric_changed_count": nonnumeric_changed,
                "null_transition_count": field_null_transitions,
                "type_drift_count": field_type_drifts,
                "change_sample": samples,
                **authority_contract(),
                "affects_gate_decisions": False,
            }
        )
    return rows, {
        "member_count": len(members),
        "field_count": len(all_fields),
        "added_member_count": 0,
        "removed_member_count": 0,
        "added_field_path_count": len(added_field_paths),
        "removed_field_path_count": len(removed_field_paths),
        "added_field_path_sample": added_field_paths[:100],
        "removed_field_path_sample": removed_field_paths[:100],
        "added_field_path_sample_complete": (
            len(added_field_paths) <= 100
        ),
        "removed_field_path_sample_complete": (
            len(removed_field_paths) <= 100
        ),
        "nonnumeric_changed_count": nonnumeric_changes,
        "null_transition_count": null_transitions,
        "type_drift_count": type_drifts,
    }


def nested_get(value: dict[str, Any], dotted: str) -> Any:
    current: Any = value
    for component in dotted.split("."):
        require(
            isinstance(current, dict) and component in current,
            f"Missing stance section: {dotted}",
        )
        current = current[component]
    return current


def stance_numeric_rows(
    frozen: dict[str, Any],
    live: dict[str, Any],
    members: list[str],
    categories: list[str],
) -> list[dict[str, Any]]:
    rows = []
    for section in STANCE_NUMERIC_SECTIONS:
        before_section = nested_get(frozen, section)
        after_section = nested_get(live, section)
        require_exact_keys(
            object_keys(before_section, f"frozen stance {section}"),
            set(members),
            f"frozen stance {section} member",
        )
        require_exact_keys(
            object_keys(after_section, f"live stance {section}"),
            set(members),
            f"live stance {section} member",
        )
        for member in members:
            require_exact_keys(
                object_keys(
                    before_section[member],
                    f"frozen stance {section} {member}",
                ),
                set(categories),
                f"frozen stance {section} category",
            )
            require_exact_keys(
                object_keys(
                    after_section[member],
                    f"live stance {section} {member}",
                ),
                set(categories),
                f"live stance {section} category",
            )
        for category in categories:
            deltas = []
            null_transitions = 0
            type_drifts = 0
            for member in members:
                before = before_section[member][category]
                after = after_section[member][category]
                require(
                    is_number(before) and is_number(after),
                    "Stance numeric path is nonnumeric: "
                    f"{section}.{member}.{category}",
                )
                deltas.append(float(after) - float(before))
            rows.append(
                {
                    "schema": "post-weekly-refresh-numeric-field-drift-v1",
                    "artifact": "category_stance.json",
                    "domain": section,
                    "field": category,
                    "grouping_method": (
                        "artifact+section+category_across_members"
                    ),
                    "path_template": (
                        f"{section}.<member>.{category}"
                    ),
                    "paired_numeric_values_only": True,
                    **summarize_deltas(deltas),
                    "nonnumeric_pair_count": 0,
                    "nonnumeric_changed_count": 0,
                    "null_transition_count": null_transitions,
                    "type_drift_count": type_drifts,
                    "change_sample": [],
                    **authority_contract(),
                    "affects_gate_decisions": False,
                }
            )
    return rows


def cleanup_temporary(path: Path, expected_parent: Path) -> None:
    path = path.resolve()
    expected_parent = expected_parent.resolve()
    require(
        path.parent == expected_parent
        and path.name.startswith(".behavior-stance."),
        f"Unsafe temporary cleanup target: {path}",
    )

    def onexc(function: Any, target: str, error: BaseException) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=onexc)


def build_audit(
    *,
    drift_pointer_path: Path = DEFAULT_DRIFT_POINTER,
    out_root: Path = DEFAULT_OUT_ROOT,
    expected_drift_audit_id: str = DEFAULT_EXPECTED_DRIFT_AUDIT_ID,
    expected_member_count: int = 533,
    expected_category_count: int = 18,
) -> tuple[Path, dict[str, Any], bool]:
    pointer = read_json(drift_pointer_path)
    require(isinstance(pointer, dict), "Drift pointer must be an object")
    require(
        pointer.get("schema")
        == "post-weekly-refresh-downstream-drift-pointer-v1"
        and pointer.get("audit_id") == expected_drift_audit_id
        and authority_is_false(pointer)
        and pointer.get("sync_or_promotion_performed") is False,
        "Downstream drift pointer safety/identity contract failed",
    )
    require(
        pointer.get("audit_id") == expected_drift_audit_id,
        "Unexpected downstream drift audit identity",
    )
    manifest_item = pointer.get("manifest")
    require(isinstance(manifest_item, dict), "Drift manifest binding missing")
    manifest_path = resolve_bound_path(
        manifest_item.get("path", ""), drift_pointer_path.parent
    )
    require(
        receipt_current(manifest_item, manifest_path),
        "Drift manifest hash mismatch",
    )
    drift_manifest = read_json(manifest_path)
    require(
        drift_manifest.get("audit_id") == expected_drift_audit_id,
        "Drift manifest identity mismatch",
    )
    require(
        drift_manifest.get("status")
        == "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
        "Drift receipt is not observational PASS",
    )
    require(
        authority_is_false(drift_manifest)
        and drift_manifest.get("legislation_recomputation_accepted") is False
        and drift_manifest.get("production_or_mainframe_written") is False
        and drift_manifest.get("sync_or_promotion_performed") is False,
        "Drift receipt authority invariant failed",
    )
    observation_generated_at = drift_manifest.get("generated_at")
    require(
        isinstance(observation_generated_at, str)
        and bool(observation_generated_at),
        "Drift receipt generated_at is missing",
    )
    ledger_item = drift_manifest.get("outputs", {}).get("file_drift")
    require(isinstance(ledger_item, dict), "Drift file ledger binding missing")
    ledger_path = resolve_bound_path(
        ledger_item.get("path", ""), manifest_path.parent
    )
    require(
        receipt_current(ledger_item, ledger_path),
        "Drift file ledger hash mismatch",
    )
    drift_rows = read_ndjson(ledger_path)
    require(
        len(drift_rows) == ledger_item.get("record_count") == 55,
        "Drift file ledger does not close over 55 files",
    )
    rows_by_name = {}
    for row in drift_rows:
        name = row.get("name")
        require(
            isinstance(name, str) and name not in rows_by_name,
            "Duplicate or invalid drift file name",
        )
        rows_by_name[name] = row
    require(
        set(TARGET_NAMES) <= set(rows_by_name),
        "Drift receipt missing behavior/stance target",
    )

    frozen_values: dict[str, dict[str, Any]] = {}
    live_values: dict[str, dict[str, Any]] = {}
    inputs = {}
    artifact_rows = []
    numeric_rows = []
    identity_rows = []
    reference_members: set[str] | None = None
    reference_categories: set[str] | None = None

    for name in TARGET_NAMES:
        drift_row = rows_by_name[name]
        require(
            drift_row.get("schema")
            == "post-weekly-refresh-downstream-file-drift-v1"
            and drift_row.get("changed") is True
            and drift_row.get("accepted_legislation_recomputation") is False
            and drift_row.get("authoritative_for_scoring") is False
            and drift_row.get("authoritative_for_sync_or_promotion") is False,
            f"Target drift-row safety contract failed: {name}",
        )
        frozen_path = Path(drift_row.get("frozen_path", ""))
        live_path = Path(drift_row.get("live_path", ""))
        require(frozen_path.is_file(), f"Missing frozen target: {name}")
        require(live_path.is_file(), f"Missing live target: {name}")
        require(
            frozen_path.resolve() != live_path.resolve()
            and frozen_path.name == name
            and live_path.name == name,
            f"Frozen/live target path identity failed: {name}",
        )
        frozen_item = file_item(frozen_path)
        live_item = file_item(live_path)
        require(
            frozen_item["sha256"] == drift_row.get("frozen_sha256")
            and frozen_item["bytes"] == drift_row.get("frozen_bytes"),
            f"Frozen target hash/size moved after drift receipt: {name}",
        )
        require(
            live_item["sha256"] == drift_row.get("live_sha256")
            and live_item["bytes"] == drift_row.get("live_bytes"),
            f"Live target hash/size moved after drift receipt: {name}",
        )
        frozen = read_json(frozen_path)
        live = read_json(live_path)
        require(
            isinstance(frozen, dict) and isinstance(live, dict),
            f"Target root must be an object: {name}",
        )
        frozen_values[name] = frozen
        live_values[name] = live
        inputs[name] = {"frozen": frozen_item, "live": live_item}

        recursive = recursive_change_summary(frozen, live)
        artifact_rows.append(
            {
                "schema": "post-weekly-refresh-behavior-stance-artifact-v1",
                "artifact": name,
                "declared_stage": drift_row.get("declared_stage"),
                "frozen": frozen_item,
                "live": live_item,
                "recursive_change_summary": recursive,
                "attribution": (
                    "external_scheduled_weekly_refresh_observation"
                ),
                "potentially_legislation_bearing": True,
                "accepted_legislation_recomputation": False,
                **authority_contract(),
                "affects_gate_decisions": False,
            }
        )

        if name in MEMBER_ARTIFACTS:
            frozen_members = frozen.get("members")
            live_members = live.get("members")
            frozen_keys = object_keys(
                frozen_members, f"{name} frozen members"
            )
            live_keys = object_keys(live_members, f"{name} live members")
            require_exact_keys(
                live_keys, frozen_keys, f"{name} member identity"
            )
            require(
                len(frozen_keys) == expected_member_count,
                f"{name} expected {expected_member_count} members",
            )
            if reference_members is None:
                reference_members = frozen_keys
            require_exact_keys(
                frozen_keys, reference_members, f"{name} cross-artifact member"
            )
            field_rows, closure = member_field_rows(
                name,
                frozen_members,
                live_members,
                sorted(frozen_keys),
            )
            numeric_rows.extend(field_rows)
            identity_rows.append(
                {
                    "schema": (
                        "post-weekly-refresh-domain-identity-closure-v1"
                    ),
                    "artifact": name,
                    "domain": "members",
                    **closure,
                    "identity_closed": True,
                    **authority_contract(),
                    "affects_gate_decisions": False,
                }
            )

    stance_frozen = frozen_values["category_stance.json"]
    stance_live = live_values["category_stance.json"]
    frozen_categories = stance_frozen.get("cats")
    live_categories = stance_live.get("cats")
    require(
        isinstance(frozen_categories, list)
        and isinstance(live_categories, list)
        and all(isinstance(value, str) and value for value in frozen_categories)
        and all(isinstance(value, str) and value for value in live_categories),
        "Stance category identities are invalid",
    )
    require(
        len(set(frozen_categories)) == len(frozen_categories)
        and len(set(live_categories)) == len(live_categories),
        "Duplicate stance category identity",
    )
    require(
        frozen_categories == live_categories,
        "Stance category identity/order closure failed",
    )
    require(
        len(frozen_categories) == expected_category_count,
        f"Expected {expected_category_count} stance categories",
    )
    reference_categories = set(frozen_categories)
    assert reference_members is not None
    for section in STANCE_MEMBER_SECTIONS:
        before = nested_get(stance_frozen, section)
        after = nested_get(stance_live, section)
        require_exact_keys(
            object_keys(before, f"frozen stance {section}"),
            reference_members,
            f"frozen stance {section} member",
        )
        require_exact_keys(
            object_keys(after, f"live stance {section}"),
            reference_members,
            f"live stance {section} member",
        )
        for member in sorted(reference_members):
            require_exact_keys(
                object_keys(before[member], f"frozen stance {section} member"),
                reference_categories,
                f"frozen stance {section} category",
            )
            require_exact_keys(
                object_keys(after[member], f"live stance {section} member"),
                reference_categories,
                f"live stance {section} category",
            )
        identity_rows.append(
            {
                "schema": "post-weekly-refresh-domain-identity-closure-v1",
                "artifact": "category_stance.json",
                "domain": section,
                "member_count": len(reference_members),
                "category_count": len(reference_categories),
                "member_category_cell_count": (
                    len(reference_members) * len(reference_categories)
                ),
                "added_member_count": 0,
                "removed_member_count": 0,
                "added_category_count": 0,
                "removed_category_count": 0,
                "missing_member_category_cell_count": 0,
                "identity_closed": True,
                **authority_contract(),
                "affects_gate_decisions": False,
            }
        )
    numeric_rows.extend(
        stance_numeric_rows(
            stance_frozen,
            stance_live,
            sorted(reference_members),
            frozen_categories,
        )
    )
    matrix_before = stance_frozen["matrix"]
    matrix_after = stance_live["matrix"]
    matrix_changes = []
    for category in frozen_categories:
        changed_members = [
            member
            for member in sorted(reference_members)
            if matrix_before[member][category] != matrix_after[member][category]
        ]
        matrix_changes.append(
            {
                "category": category,
                "nonnumeric_pair_count": len(reference_members),
                "nonnumeric_changed_count": len(changed_members),
                "changed_member_sample": changed_members[:20],
            }
        )
    identity_rows.append(
        {
            "schema": "post-weekly-refresh-domain-identity-closure-v1",
            "artifact": "category_stance.json",
            "domain": "categories",
            "category_count": len(frozen_categories),
            "categories": frozen_categories,
            "added_category_count": 0,
            "removed_category_count": 0,
            "order_changed": False,
            "matrix_nonnumeric_changes_by_category": matrix_changes,
            "identity_closed": True,
            **authority_contract(),
            "affects_gate_decisions": False,
        }
    )

    artifact_rows.sort(key=lambda row: row["artifact"])
    numeric_rows.sort(
        key=lambda row: (row["artifact"], row["domain"], row["field"])
    )
    identity_rows.sort(key=lambda row: (row["artifact"], row["domain"]))
    artifacts_body = b"".join(
        canonical_bytes(row) for row in artifact_rows
    )
    numeric_body = b"".join(canonical_bytes(row) for row in numeric_rows)
    identities_body = b"".join(
        canonical_bytes(row) for row in identity_rows
    )
    aggregate = {
        "artifact_count": len(artifact_rows),
        "member_count": len(reference_members),
        "category_count": len(reference_categories),
        "numeric_field_group_count": len(numeric_rows),
        "numeric_pair_count": sum(
            row["numeric_pair_count"] for row in numeric_rows
        ),
        "numeric_changed_count": sum(
            row["numeric_changed_count"] for row in numeric_rows
        ),
        "nonnumeric_changed_count": sum(
            row["recursive_change_summary"]["nonnumeric_changed_count"]
            for row in artifact_rows
        ),
        "added_path_count": sum(
            row["recursive_change_summary"]["added_path_count"]
            for row in artifact_rows
        ),
        "removed_path_count": sum(
            row["recursive_change_summary"]["removed_path_count"]
            for row in artifact_rows
        ),
        "null_transition_count": sum(
            row["recursive_change_summary"]["null_transition_count"]
            for row in artifact_rows
        ),
        "type_drift_count": sum(
            row["recursive_change_summary"]["type_drift_count"]
            for row in artifact_rows
        ),
        "numeric_grouping_method": (
            "member artifacts group paired scalar numeric values by "
            "artifact/domain/field across the exact member set; stance "
            "groups paired numeric values by section/category across the "
            "same exact member set"
        ),
        "structural_and_nonnumeric_path_method": (
            "recursive exact-key/index paths with complete samples whenever "
            "the eligible path count is at most 100"
        ),
        "identity_closure_passed": True,
        "accepted_legislation_recomputation_count": 0,
    }
    bound_receipts = {
        "downstream_drift_pointer": file_item(drift_pointer_path),
        "downstream_drift_manifest": file_item(manifest_path),
        "downstream_drift_file_ledger": file_item(
            ledger_path, record_count=len(drift_rows)
        ),
    }
    producer = file_item(Path(__file__).resolve())
    identity = {
        "schema": "post-weekly-refresh-behavior-stance-identity-v1",
        "expected_downstream_drift_audit_id": expected_drift_audit_id,
        "bound_receipts": bound_receipts,
        "inputs": inputs,
        "producer": producer,
        "artifact_semantics_sha256": sha256_bytes(artifacts_body),
        "numeric_field_drift_sha256": sha256_bytes(numeric_body),
        "identity_closure_sha256": sha256_bytes(identities_body),
        "summary": aggregate,
        "legislation_recomputation_accepted": False,
        **authority_contract(),
        "affects_gate_decisions": False,
    }
    audit_id = sha256_bytes(canonical_bytes(identity))
    snapshot_root = out_root / "snapshots"
    snapshot_dir = snapshot_root / audit_id
    out_root.mkdir(parents=True, exist_ok=True)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    temporary = snapshot_root / f".behavior-stance.{uuid.uuid4().hex}"
    temporary.mkdir()
    created = False
    try:
        bodies = {
            "artifact_semantics.ndjson": artifacts_body,
            "numeric_field_drift.ndjson": numeric_body,
            "identity_closure.ndjson": identities_body,
        }
        for filename, body in bodies.items():
            (temporary / filename).write_bytes(body)
        outputs = {
            "artifact_semantics": file_item(
                temporary / "artifact_semantics.ndjson",
                len(artifact_rows),
            ),
            "numeric_field_drift": file_item(
                temporary / "numeric_field_drift.ndjson",
                len(numeric_rows),
            ),
            "identity_closure": file_item(
                temporary / "identity_closure.ndjson",
                len(identity_rows),
            ),
        }
        for key, item in outputs.items():
            item["path"] = str(
                (
                    snapshot_dir
                    / OUTPUT_FILENAMES[key]
                ).resolve()
            )
        output_manifest = {
            "schema": (
                "post-weekly-refresh-behavior-stance-manifest-v1"
            ),
            "audit_id": audit_id,
            "generated_at": observation_generated_at,
            "expected_downstream_drift_audit_id": (
                expected_drift_audit_id
            ),
            "status": "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
            "bound_receipts": bound_receipts,
            "inputs": inputs,
            "producer": producer,
            "outputs": outputs,
            "summary": aggregate,
            "attribution": (
                "External scheduled weekly refresh observation only; "
                "causation is not accepted legislation recomputation."
            ),
            "all_domains_identity_closed": True,
            "potentially_legislation_bearing": True,
            "legislation_recomputation_accepted": False,
            **authority_contract(),
            "affects_gate_decisions": False,
            "production_or_mainframe_written": False,
            "sync_or_promotion_performed": False,
            "network_executed": False,
        }
        (temporary / "manifest.json").write_bytes(pretty_bytes(output_manifest))
        if snapshot_dir.exists():
            existing = read_json(snapshot_dir / "manifest.json")
            require(
                existing.get("audit_id") == audit_id,
                "Existing behavior/stance audit identity mismatch",
            )
            require(
                set(path.name for path in snapshot_dir.iterdir())
                == {"manifest.json", *bodies},
                "Existing behavior/stance snapshot file closure drifted",
            )
            for filename, body in bodies.items():
                require(
                    sha256_file(snapshot_dir / filename)
                    == sha256_bytes(body),
                    f"Existing behavior/stance output tampering: {filename}",
                )
            require(
                existing == output_manifest,
                "Existing behavior/stance manifest semantic drift",
            )
            output_manifest = existing
        else:
            os.replace(temporary, snapshot_dir)
            created = True
    finally:
        if temporary.exists():
            cleanup_temporary(temporary, snapshot_root)

    output_pointer = {
        "schema": "post-weekly-refresh-behavior-stance-pointer-v1",
        "audit_id": audit_id,
        "snapshot_dir": str(snapshot_dir.resolve()),
        "manifest": file_item(snapshot_dir / "manifest.json"),
        "producer": producer,
        **authority_contract(),
        "affects_gate_decisions": False,
        "sync_or_promotion_performed": False,
    }
    write_if_changed(
        out_root / "current.json", pretty_bytes(output_pointer)
    )
    return snapshot_dir, output_manifest, created


def validate_evidence(
    pointer_path: Path = DEFAULT_OUT_ROOT / "current.json",
    *,
    expected_drift_audit_id: str = DEFAULT_EXPECTED_DRIFT_AUDIT_ID,
    expected_member_count: int = 533,
    expected_category_count: int = 18,
) -> dict[str, Any]:
    """Read-only validation of the current informational semantic receipt."""
    pointer_path = pointer_path.resolve()
    pointer = read_json(pointer_path)
    require(
        isinstance(pointer, dict)
        and pointer.get("schema")
        == "post-weekly-refresh-behavior-stance-pointer-v1"
        and isinstance(pointer.get("audit_id"), str)
        and len(pointer["audit_id"]) == 64
        and authority_is_false(pointer)
        and pointer.get("affects_gate_decisions") is False
        and pointer.get("sync_or_promotion_performed") is False,
        "Behavior/stance pointer safety contract drifted",
    )
    audit_id = pointer["audit_id"]
    snapshot_dir = Path(str(pointer.get("snapshot_dir") or "")).resolve()
    require(
        snapshot_dir
        == (pointer_path.parent / "snapshots" / audit_id).resolve()
        and snapshot_dir.is_dir(),
        "Behavior/stance snapshot path/content address drifted",
    )
    producer_path = Path(__file__).resolve()
    require(
        receipt_current(pointer.get("producer"), producer_path),
        "Behavior/stance pointer producer receipt drifted",
    )
    manifest_path = snapshot_dir / "manifest.json"
    require(
        receipt_current(pointer.get("manifest"), manifest_path),
        "Behavior/stance pointer manifest receipt drifted",
    )
    manifest = read_json(manifest_path)
    require(
        isinstance(manifest, dict)
        and manifest.get("schema")
        == "post-weekly-refresh-behavior-stance-manifest-v1"
        and manifest.get("audit_id") == audit_id
        and manifest.get("expected_downstream_drift_audit_id")
        == expected_drift_audit_id
        and manifest.get("status")
        == "PASS_OBSERVATIONAL_NONAUTHORITATIVE"
        and manifest.get("all_domains_identity_closed") is True
        and manifest.get("legislation_recomputation_accepted") is False
        and authority_is_false(manifest)
        and manifest.get("affects_gate_decisions") is False
        and manifest.get("production_or_mainframe_written") is False
        and manifest.get("sync_or_promotion_performed") is False
        and manifest.get("network_executed") is False,
        "Behavior/stance manifest safety/identity contract drifted",
    )
    require(
        receipt_current(manifest.get("producer"), producer_path),
        "Behavior/stance manifest producer receipt drifted",
    )

    bound = manifest.get("bound_receipts")
    require(
        isinstance(bound, dict)
        and set(bound)
        == {
            "downstream_drift_pointer",
            "downstream_drift_manifest",
            "downstream_drift_file_ledger",
        },
        "Behavior/stance upstream receipt closure drifted",
    )
    drift_pointer_path = Path(
        str(bound["downstream_drift_pointer"].get("path") or "")
    ).resolve()
    drift_manifest_path = Path(
        str(bound["downstream_drift_manifest"].get("path") or "")
    ).resolve()
    drift_ledger_path = Path(
        str(bound["downstream_drift_file_ledger"].get("path") or "")
    ).resolve()
    for label, path in (
        ("pointer", drift_pointer_path),
        ("manifest", drift_manifest_path),
        ("file ledger", drift_ledger_path),
    ):
        require(
            receipt_current(
                bound[
                    {
                        "pointer": "downstream_drift_pointer",
                        "manifest": "downstream_drift_manifest",
                        "file ledger": "downstream_drift_file_ledger",
                    }[label]
                ],
                path,
            ),
            f"Behavior/stance upstream {label} receipt drifted",
        )
    upstream_pointer = read_json(drift_pointer_path)
    upstream_manifest = read_json(drift_manifest_path)
    require(
        upstream_pointer.get("audit_id") == expected_drift_audit_id
        and upstream_manifest.get("audit_id") == expected_drift_audit_id
        and authority_is_false(upstream_pointer)
        and authority_is_false(upstream_manifest),
        "Behavior/stance exact downstream audit binding drifted",
    )
    require(
        manifest.get("generated_at") == upstream_manifest.get("generated_at"),
        "Behavior/stance observation timestamp binding drifted",
    )

    inputs = manifest.get("inputs")
    require(
        isinstance(inputs, dict) and set(inputs) == set(TARGET_NAMES),
        "Behavior/stance input artifact closure drifted",
    )
    frozen_values: dict[str, dict[str, Any]] = {}
    live_values: dict[str, dict[str, Any]] = {}
    for name in TARGET_NAMES:
        pair = inputs[name]
        require(
            isinstance(pair, dict)
            and set(pair) == {"frozen", "live"},
            f"Behavior/stance input pair drifted: {name}",
        )
        for side, values in (
            ("frozen", frozen_values),
            ("live", live_values),
        ):
            item = pair[side]
            path = Path(str(item.get("path") or "")).resolve()
            require(
                path.name == name and receipt_current(item, path),
                f"Behavior/stance {side} input receipt drifted: {name}",
            )
            value = read_json(path)
            require(
                isinstance(value, dict),
                f"Behavior/stance {side} input root drifted: {name}",
            )
            values[name] = value

    outputs = manifest.get("outputs")
    require(
        isinstance(outputs, dict)
        and set(outputs) == set(OUTPUT_FILENAMES),
        "Behavior/stance output receipt closure drifted",
    )
    output_rows: dict[str, list[dict[str, Any]]] = {}
    for label, filename in OUTPUT_FILENAMES.items():
        path = snapshot_dir / filename
        item = outputs[label]
        require(
            receipt_current(item, path),
            f"Behavior/stance output receipt drifted: {label}",
        )
        rows = read_ndjson(path)
        require(
            item.get("record_count") == len(rows),
            f"Behavior/stance output row count drifted: {label}",
        )
        output_rows[label] = rows

    artifact_rows = output_rows["artifact_semantics"]
    require(
        len(artifact_rows) == len(TARGET_NAMES)
        and {row.get("artifact") for row in artifact_rows}
        == set(TARGET_NAMES),
        "Behavior/stance artifact semantic row closure drifted",
    )
    for row in artifact_rows:
        name = str(row["artifact"])
        require(
            row.get("schema")
            == "post-weekly-refresh-behavior-stance-artifact-v1"
            and row.get("accepted_legislation_recomputation") is False
            and authority_is_false(row)
            and row.get("affects_gate_decisions") is False
            and row.get("frozen") == inputs[name]["frozen"]
            and row.get("live") == inputs[name]["live"]
            and row.get("recursive_change_summary")
            == recursive_change_summary(
                frozen_values[name], live_values[name]
            ),
            f"Behavior/stance artifact semantic method drifted: {name}",
        )

    reference_members: set[str] | None = None
    expected_numeric_rows: list[dict[str, Any]] = []
    for name in MEMBER_ARTIFACTS:
        frozen_members = frozen_values[name].get("members")
        live_members = live_values[name].get("members")
        frozen_keys = object_keys(frozen_members, f"{name} frozen members")
        live_keys = object_keys(live_members, f"{name} live members")
        require_exact_keys(live_keys, frozen_keys, f"{name} member identity")
        require(
            len(frozen_keys) == expected_member_count,
            f"{name} member count drifted",
        )
        if reference_members is None:
            reference_members = frozen_keys
        require_exact_keys(
            frozen_keys, reference_members, f"{name} cross-artifact member"
        )
        rows, _ = member_field_rows(
            name,
            frozen_members,
            live_members,
            sorted(frozen_keys),
        )
        expected_numeric_rows.extend(rows)
    assert reference_members is not None

    stance_frozen = frozen_values["category_stance.json"]
    stance_live = live_values["category_stance.json"]
    categories = stance_frozen.get("cats")
    require(
        isinstance(categories, list)
        and categories == stance_live.get("cats")
        and len(categories) == len(set(categories)) == expected_category_count
        and all(isinstance(category, str) and category for category in categories),
        "Behavior/stance category identity/order closure drifted",
    )
    category_set = set(categories)
    for section in STANCE_MEMBER_SECTIONS:
        before = nested_get(stance_frozen, section)
        after = nested_get(stance_live, section)
        require_exact_keys(
            object_keys(before, f"frozen stance {section}"),
            reference_members,
            f"frozen stance {section} member",
        )
        require_exact_keys(
            object_keys(after, f"live stance {section}"),
            reference_members,
            f"live stance {section} member",
        )
        for member in reference_members:
            require_exact_keys(
                object_keys(before[member], f"frozen {section} {member}"),
                category_set,
                f"frozen stance {section} category",
            )
            require_exact_keys(
                object_keys(after[member], f"live {section} {member}"),
                category_set,
                f"live stance {section} category",
            )
    expected_numeric_rows.extend(
        stance_numeric_rows(
            stance_frozen,
            stance_live,
            sorted(reference_members),
            categories,
        )
    )
    expected_numeric_rows.sort(
        key=lambda row: (row["artifact"], row["domain"], row["field"])
    )
    numeric_rows = output_rows["numeric_field_drift"]
    require(
        numeric_rows == expected_numeric_rows,
        "Behavior/stance numeric grouping/path methodology drifted",
    )
    require(
        all(
            authority_is_false(row)
            and row.get("affects_gate_decisions") is False
            and row.get("paired_numeric_values_only") is True
            for row in numeric_rows
        ),
        "Behavior/stance numeric row authority/method contract drifted",
    )

    identity_rows = output_rows["identity_closure"]
    expected_identity_keys = {
        (name, "members") for name in MEMBER_ARTIFACTS
    } | {
        ("category_stance.json", section)
        for section in (*STANCE_MEMBER_SECTIONS, "categories")
    }
    require(
        len(identity_rows) == 7
        and {
            (row.get("artifact"), row.get("domain"))
            for row in identity_rows
        }
        == expected_identity_keys
        and all(
            row.get("identity_closed") is True
            and authority_is_false(row)
            and row.get("affects_gate_decisions") is False
            for row in identity_rows
        ),
        "Behavior/stance identity receipt closure/authority drifted",
    )
    category_identity = next(
        row for row in identity_rows if row.get("domain") == "categories"
    )
    require(
        category_identity.get("categories") == categories
        and category_identity.get("category_count") == expected_category_count
        and category_identity.get("order_changed") is False,
        "Behavior/stance category order receipt drifted",
    )

    summary = manifest.get("summary")
    require(
        isinstance(summary, dict)
        and summary.get("artifact_count") == 4
        and summary.get("member_count") == expected_member_count
        and summary.get("category_count") == expected_category_count
        and summary.get("numeric_field_group_count") == len(numeric_rows)
        and summary.get("numeric_pair_count")
        == sum(row["numeric_pair_count"] for row in numeric_rows)
        and summary.get("numeric_changed_count")
        == sum(row["numeric_changed_count"] for row in numeric_rows)
        and summary.get("nonnumeric_changed_count")
        == sum(
            row["recursive_change_summary"]["nonnumeric_changed_count"]
            for row in artifact_rows
        )
        and summary.get("added_path_count")
        == sum(
            row["recursive_change_summary"]["added_path_count"]
            for row in artifact_rows
        )
        and summary.get("identity_closure_passed") is True
        and summary.get("accepted_legislation_recomputation_count") == 0,
        "Behavior/stance summary closure drifted",
    )
    return {
        "status": "CURRENT_INFORMATIONAL",
        "evidence_current": True,
        "informational_only": True,
        "affects_gate_decisions": False,
        "can_satisfy_blocked_gate": False,
        "audit_id": audit_id,
        "expected_downstream_drift_audit_id": expected_drift_audit_id,
        "pointer": file_item(pointer_path),
        "manifest": file_item(manifest_path),
        "producer": file_item(producer_path),
        "summary": summary,
        "authority_fields_all_false": True,
        "member_identity_closure": {
            "count": len(reference_members),
            "closed": True,
        },
        "category_identity_order_closure": {
            "count": len(categories),
            "categories": categories,
            "closed": True,
            "order_changed": False,
        },
        "numeric_grouping_path_method_verified": True,
        "structural_and_nonnumeric_paths_visible": True,
        "sync_or_promotion_performed": False,
        "production_or_mainframe_written": False,
    }


def main() -> int:
    snapshot_dir, manifest, created = build_audit()
    evidence = validate_evidence()
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "created": created,
                "audit_id": manifest["audit_id"],
                "snapshot_dir": str(snapshot_dir),
                "summary": manifest["summary"],
                "evidence_status": evidence["status"],
                "sync_or_promotion_performed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal audit-only semantics for changed planning/dashboard artifacts.

This producer is deliberately observational.  It verifies the immutable
downstream-drift receipt, compares the frozen and live JSON structures and
numeric leaves, and proves embedded JSON identity in the generated HTML where
possible.  It never grants legislation, scoring, synchronization, exclusion,
or promotion authority.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any, Iterable
import uuid


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DRIFT_POINTER = (
    ROOT / "audits" / "legfix5" / "post_weekly_refresh_downstream_drift"
    / "current.json"
)
DEFAULT_OUT_ROOT = (
    ROOT / "audits" / "legfix5"
    / "post_weekly_refresh_planning_dashboard_semantics"
)

JSON_TARGETS = (
    # Names are FROZEN-MANIFEST identities (the drift ledger's keys), not live
    # locations -- Phase 2 relocated four of these artifacts, and the ledger
    # rows carry the relocated live_path; identity stays the manifest name.
    "electoral_plays.json",
    "per_topic_alloc.json",
    "per_topic_coalition.json",
    "budget_frontier.json",
    "budget_frontier_electoral.json",
    "optimize_result.json",
    "im_allocation.json",
    "salience_advisory.json",
    "dashboard_data.json",
    "member_desk_bundle.json",
    "dashboard_exclusion_reconciliation.json",
)
HTML_TARGETS = (
    "Moneyball Dashboard.html",
    "Moneyball.html",
    "cu-member-desk.html",
)
TARGETS = frozenset((*JSON_TARGETS, *HTML_TARGETS))
HTML_PAYLOADS = {
    "Moneyball Dashboard.html": (("DATA", "dashboard_data.json"),),
    "Moneyball.html": (
        ("DATA", "dashboard_data.json"),
        ("D", "member_desk_bundle.json"),
    ),
    "cu-member-desk.html": (("D", "member_desk_bundle.json"),),
}

FALSE_ONLY_KEYS = frozenset(
    {
        "accepted_legislation_recomputation",
        "authoritative_for_exclusion",
        "authoritative_for_legislation",
        "authoritative_for_scoring",
        "authoritative_for_sync_or_promotion",
        "can_satisfy_blocked_gate",
        "exclusion_decisions_performed",
        "legislation_recomputation_accepted",
        "network_executed",
        "production_or_mainframe_written",
        "production_written",
        "scoring_decisions_performed",
        "sync_allowed",
        "sync_or_promotion_performed",
        "write_authority",
    }
)


class AuditError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")


def pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    require(path.is_file(), f"Missing JSON input: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"Unreadable JSON input {path}: {exc}") from exc


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    require(path.is_file(), f"Missing NDJSON input: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise AuditError(
                f"Unreadable NDJSON row {path}:{line_number}: {exc}"
            ) from exc
        require(
            isinstance(row, dict),
            f"NDJSON row is not an object: {path}:{line_number}",
        )
        rows.append(row)
    return rows


def file_item(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"Missing bound file: {path}")
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def validate_file_item(item: Any, label: str) -> Path:
    require(isinstance(item, dict), f"{label} receipt missing")
    path = Path(str(item.get("path") or ""))
    require(path.is_absolute() and path.is_file(), f"{label} path invalid")
    require(
        item.get("bytes") == path.stat().st_size
        and item.get("sha256") == sha256_file(path),
        f"{label} receipt drift",
    )
    return path.resolve()


def require_false_authority(value: Any, label: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{label}.{key}"
            if key in FALSE_ONLY_KEYS:
                require(item is False, f"Authority/write flag is not false: {path}")
            require_false_authority(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            require_false_authority(item, f"{label}[{index}]")


def _receipt_matches(item: Any, path: Path, label: str) -> None:
    require(isinstance(item, dict), f"{label} receipt missing")
    require(
        Path(str(item.get("path") or "")).resolve() == path.resolve()
        and item.get("bytes") == path.stat().st_size
        and item.get("sha256") == sha256_file(path),
        f"{label} receipt mismatch",
    )


def validate_drift_pointer(
    pointer_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    pointer = read_json(pointer_path)
    require(isinstance(pointer, dict), "Drift pointer must be an object")
    require(
        pointer.get("schema")
        == "post-weekly-refresh-downstream-drift-pointer-v1",
        "Unexpected downstream-drift pointer schema",
    )
    require_false_authority(pointer, "drift_pointer")
    manifest_path = validate_file_item(
        pointer.get("manifest"), "Downstream-drift manifest"
    )
    manifest = read_json(manifest_path)
    require(isinstance(manifest, dict), "Drift manifest must be an object")
    require(
        manifest.get("schema")
        == "post-weekly-refresh-downstream-drift-manifest-v1",
        "Unexpected downstream-drift manifest schema",
    )
    require(
        pointer.get("audit_id") == manifest.get("audit_id"),
        "Drift pointer/manifest audit ID mismatch",
    )
    require_false_authority(manifest, "drift_manifest")
    output = (manifest.get("outputs") or {}).get("file_drift")
    ledger_path = validate_file_item(output, "Downstream file-drift ledger")
    rows = read_ndjson(ledger_path)
    require(
        isinstance(output, dict)
        and output.get("record_count") == len(rows) == 55,
        "Downstream file-drift ledger does not close over 55 files",
    )
    require_false_authority(rows, "drift_rows")
    by_name = {
        row.get("name"): row
        for row in rows
        if isinstance(row.get("name"), str)
    }
    require(len(by_name) == len(rows), "Duplicate/invalid drift-ledger names")
    require(TARGETS <= set(by_name), "Planning/dashboard target closure missing")
    target_rows = [by_name[name] for name in sorted(TARGETS)]
    for row in target_rows:
        name = str(row["name"])
        require(
            row.get("changed") is True
            and row.get("byte_identical") is False,
            f"Target is not an upstream-proven changed file: {name}",
        )
        for side in ("frozen", "live"):
            path = Path(str(row.get(f"{side}_path") or ""))
            require(
                path.is_absolute() and path.is_file(),
                f"{name} {side} path invalid",
            )
            require(
                row.get(f"{side}_bytes") == path.stat().st_size
                and row.get(f"{side}_sha256") == sha256_file(path),
                f"{name} {side} drifted after downstream receipt",
            )
    return pointer, manifest, target_rows


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def structural_profile(value: Any) -> dict[str, Any]:
    counts: Counter[str] = Counter()

    def visit(item: Any) -> str:
        kind = json_type(item)
        counts[kind] += 1
        if isinstance(item, dict):
            children = [[str(key), visit(child)] for key, child in item.items()]
            return sha256_bytes(canonical_bytes(["object", sorted(children)]))
        if isinstance(item, list):
            child_counts = Counter(visit(child) for child in item)
            return sha256_bytes(
                canonical_bytes(["array", len(item), sorted(child_counts.items())])
            )
        return sha256_bytes(canonical_bytes(["scalar", kind]))

    root_digest = visit(value)
    return {
        "schema_sha256": root_digest,
        "node_type_counts": dict(sorted(counts.items())),
        "node_count": sum(counts.values()),
        "root_type": json_type(value),
        "top_level_key_count": len(value) if isinstance(value, dict) else None,
        "top_level_keys_sha256": (
            sha256_bytes(canonical_bytes(sorted(value)))
            if isinstance(value, dict)
            else None
        ),
    }


def json_diff(old: Any, new: Any) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    samples: list[dict[str, str]] = []
    numeric_sum = 0.0
    numeric_min: float | None = None
    numeric_max: float | None = None
    largest: list[tuple[float, str, float]] = []

    def add_sample(path: str, change: str) -> None:
        if len(samples) < 100:
            samples.append({"path": path or "$", "change": change})

    def path_join(parent: str, child: str) -> str:
        if child.startswith("["):
            return f"{parent}{child}"
        return f"{parent}.{child}" if parent else child

    def compare(left: Any, right: Any, path: str = "") -> None:
        nonlocal numeric_sum, numeric_min, numeric_max
        left_type = json_type(left)
        right_type = json_type(right)
        if left_type != right_type:
            counts["type_changed"] += 1
            add_sample(path, "type_changed")
            return
        if isinstance(left, dict):
            left_keys, right_keys = set(left), set(right)
            for key in sorted(left_keys - right_keys):
                counts["removed_paths"] += 1
                add_sample(path_join(path, str(key)), "removed")
            for key in sorted(right_keys - left_keys):
                counts["added_paths"] += 1
                add_sample(path_join(path, str(key)), "added")
            for key in sorted(left_keys & right_keys):
                compare(left[key], right[key], path_join(path, str(key)))
            return
        if isinstance(left, list):
            common = min(len(left), len(right))
            for index in range(common):
                compare(left[index], right[index], path_join(path, f"[{index}]"))
            for index in range(common, len(left)):
                counts["removed_paths"] += 1
                add_sample(path_join(path, f"[{index}]"), "removed")
            for index in range(common, len(right)):
                counts["added_paths"] += 1
                add_sample(path_join(path, f"[{index}]"), "added")
            return
        if left == right:
            counts["equal_scalar_leaves"] += 1
            return
        if (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
        ):
            delta = float(right) - float(left)
            counts["numeric_changed"] += 1
            if math.isfinite(delta):
                counts["finite_numeric_deltas"] += 1
                numeric_sum += delta
                numeric_min = delta if numeric_min is None else min(numeric_min, delta)
                numeric_max = delta if numeric_max is None else max(numeric_max, delta)
                entry = (abs(delta), path or "$", delta)
                if len(largest) < 100:
                    heapq.heappush(largest, entry)
                elif entry[0] > largest[0][0]:
                    heapq.heapreplace(largest, entry)
            else:
                counts["nonfinite_numeric_deltas"] += 1
            add_sample(path, "numeric_changed")
        else:
            counts["nonnumeric_scalar_changed"] += 1
            add_sample(path, "nonnumeric_scalar_changed")

    compare(old, new)
    changed = sum(
        counts[key]
        for key in (
            "added_paths",
            "removed_paths",
            "type_changed",
            "numeric_changed",
            "nonnumeric_scalar_changed",
        )
    )
    return {
        **{
            key: counts[key]
            for key in (
                "added_paths",
                "removed_paths",
                "type_changed",
                "numeric_changed",
                "nonnumeric_scalar_changed",
                "equal_scalar_leaves",
            )
        },
        "changed_path_count": changed,
        "changed_path_sample": samples,
        "numeric_delta": {
            "finite_count": counts["finite_numeric_deltas"],
            "nonfinite_count": counts["nonfinite_numeric_deltas"],
            "min": numeric_min,
            "max": numeric_max,
            "sum": numeric_sum,
            "max_abs_path_sample": [
                {"path": path, "delta": delta}
                for _, path, delta in sorted(largest, reverse=True)
            ],
        },
    }


def sequence_receipt(values: Iterable[Any], *, ordered: bool) -> dict[str, Any]:
    materialized = list(values)
    if not ordered:
        materialized = sorted(materialized, key=lambda item: canonical_bytes(item))
    return {
        "count": len(materialized),
        "unique_count": len({canonical_bytes(item) for item in materialized}),
        "ordered": ordered,
        "sha256": sha256_bytes(canonical_bytes(materialized)),
    }


def closure_pair(
    label: str,
    frozen_values: Iterable[Any],
    live_values: Iterable[Any],
    *,
    ordered: bool = False,
) -> dict[str, Any]:
    frozen = list(frozen_values)
    live = list(live_values)
    old_receipt = sequence_receipt(frozen, ordered=ordered)
    new_receipt = sequence_receipt(live, ordered=ordered)
    old_set = {canonical_bytes(value): value for value in frozen}
    new_set = {canonical_bytes(value): value for value in live}
    added = [new_set[key] for key in sorted(new_set.keys() - old_set.keys())[:25]]
    removed = [old_set[key] for key in sorted(old_set.keys() - new_set.keys())[:25]]
    return {
        "label": label,
        "frozen": old_receipt,
        "live": new_receipt,
        "identity_equal": old_receipt["sha256"] == new_receipt["sha256"],
        "added_identity_count": len(new_set.keys() - old_set.keys()),
        "removed_identity_count": len(old_set.keys() - new_set.keys()),
        "added_identity_sample": added,
        "removed_identity_sample": removed,
    }


def _list_of_dict_values(value: Any, key: str) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [item.get(key) for item in value if isinstance(item, dict) and key in item]


def _union_nested_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(
        {
            str(inner_key)
            for inner in value.values()
            if isinstance(inner, dict)
            for inner_key in inner
        }
    )


def domain_closure(name: str, frozen: Any, live: Any) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []

    def add(
        label: str,
        old_values: Iterable[Any],
        new_values: Iterable[Any],
        ordered: bool = False,
    ) -> None:
        pairs.append(
            closure_pair(label, old_values, new_values, ordered=ordered)
        )

    if name == "electoral_plays.json":
        add("member_name_keys", frozen.keys(), live.keys())
    elif name in {"per_topic_alloc.json", "per_topic_coalition.json"}:
        add("topic_keys", frozen.keys(), live.keys())
        add(
            "union_member_name_keys",
            _union_nested_keys(frozen),
            _union_nested_keys(live),
        )
    elif name in {"budget_frontier.json", "budget_frontier_electoral.json"}:
        add(
            "topic_keys",
            (frozen.get("topics") or {}).keys(),
            (live.get("topics") or {}).keys(),
        )
        add(
            "defend_overview_member_names",
            _list_of_dict_values(frozen.get("defend_overview"), "m"),
            _list_of_dict_values(live.get("defend_overview"), "m"),
            True,
        )
        add(
            "topic_path_lengths",
            [
                [key, len(value.get("path") or [])]
                for key, value in (frozen.get("topics") or {}).items()
                if isinstance(value, dict)
            ],
            [
                [key, len(value.get("path") or [])]
                for key, value in (live.get("topics") or {}).items()
                if isinstance(value, dict)
            ],
        )
    elif name == "optimize_result.json":
        add("issue_names", frozen.get("issues") or [], live.get("issues") or [], True)
        add(
            "allocation_member_keys",
            (frozen.get("alloc") or {}).keys(),
            (live.get("alloc") or {}).keys(),
        )
        add(
            "optimization_vector_lengths",
            [
                ["path", len(frozen.get("path") or [])],
                [
                    "selected_marginals",
                    len(frozen.get("selected_marginals") or []),
                ],
            ],
            [
                ["path", len(live.get("path") or [])],
                ["selected_marginals", len(live.get("selected_marginals") or [])],
            ],
        )
    elif name == "im_allocation.json":
        for key in ("flip", "coalition"):
            add(
                f"{key}_member_keys",
                (frozen.get(key) or {}).keys(),
                (live.get(key) or {}).keys(),
            )
    elif name == "salience_advisory.json":
        add(
            "advisory_issue_names",
            _list_of_dict_values(frozen.get("advisory"), "issue"),
            _list_of_dict_values(live.get("advisory"), "issue"),
            True,
        )
    elif name == "dashboard_data.json":
        add("issue_names", frozen.get("issues") or [], live.get("issues") or [], True)
        add("member_names", frozen.get("name") or [], live.get("name") or [], True)
        member_fields = (
            "x", "y", "rank", "nl", "bp", "cf", "stance", "epval",
            "cx", "cy", "cmi", "recept", "antag", "grand",
        )
        issue_fields = ("flip", "coal", "stats", "frontier")
        string_member_fields = ("party", "gen", "play", "elay")
        add(
            "member_aligned_lengths",
            [[key, len(frozen.get(key) or [])] for key in member_fields]
            + [[key, len(frozen.get(key) or "")] for key in string_member_fields],
            [[key, len(live.get(key) or [])] for key in member_fields]
            + [[key, len(live.get(key) or "")] for key in string_member_fields],
        )
        add(
            "issue_aligned_lengths",
            [[key, len(frozen.get(key) or [])] for key in issue_fields],
            [[key, len(live.get(key) or [])] for key in issue_fields],
        )
        add(
            "relationship_array_lengths",
            [[key, len(frozen.get(key) or [])] for key in ("edges", "refs", "coap")],
            [[key, len(live.get(key) or [])] for key in ("edges", "refs", "coap")],
        )
    elif name == "member_desk_bundle.json":
        add("category_names", frozen.get("cats") or [], live.get("cats") or [], True)
        add(
            "member_names",
            _list_of_dict_values(frozen.get("members"), "name"),
            _list_of_dict_values(live.get("members"), "name"),
            True,
        )
        add(
            "relationship_array_lengths",
            [["adj", len(frozen.get("adj") or [])]],
            [["adj", len(live.get("adj") or [])]],
        )
        old_count = len(frozen.get("members") or [])
        new_count = len(live.get("members") or [])
        old_valid = all(
            isinstance(row, list)
            and len(row) >= 2
            and all(isinstance(row[index], int) for index in (0, 1))
            and 0 <= row[0] < old_count
            and 0 <= row[1] < old_count
            for row in frozen.get("adj") or []
        )
        new_valid = all(
            isinstance(row, list)
            and len(row) >= 2
            and all(isinstance(row[index], int) for index in (0, 1))
            and 0 <= row[0] < new_count
            and 0 <= row[1] < new_count
            for row in live.get("adj") or []
        )
        pairs.append(
            {
                "label": "adj_endpoint_index_range",
                "frozen_valid": old_valid,
                "live_valid": new_valid,
                "identity_equal": old_valid == new_valid,
            }
        )
    elif name == "dashboard_exclusion_reconciliation.json":
        add(
            "source_keys",
            (frozen.get("sources") or {}).keys(),
            (live.get("sources") or {}).keys(),
        )
        add(
            "exclusion_sample_keys",
            (frozen.get("exclusion_samples") or {}).keys(),
            (live.get("exclusion_samples") or {}).keys(),
        )
    else:
        raise AuditError(f"No domain-closure definition for {name}")
    return {
        "method": "declared_identity_and_alignment_receipts",
        "checks": pairs,
        "check_count": len(pairs),
        "all_identity_equal": all(
            item.get("identity_equal") is True for item in pairs
        ),
        "domain_key_or_alignment_closure_proven": True,
        "domain_meaning_or_policy_correctness_resolved": False,
    }


def normalize_html(text: str) -> bytes:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip().encode("utf-8")


def extract_json_assignment(
    text: str, variable: str
) -> tuple[Any, int, int]:
    match = re.search(
        rf"\b(?:const|let|var)\s+{re.escape(variable)}\s*=\s*", text
    )
    require(match is not None, f"Embedded assignment missing: {variable}")
    start = match.end()
    require(
        start < len(text) and text[start] in "[{",
        f"Embedded assignment is not JSON: {variable}",
    )
    opening = text[start]
    closing = "}" if opening == "{" else "]"
    stack = [closing]
    in_string = False
    escaped = False
    index = start + 1
    while index < len(text) and stack:
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            stack.append("}" if character == "{" else "]")
        elif character in "]}":
            require(
                character == stack[-1],
                f"Unbalanced embedded JSON: {variable}",
            )
            stack.pop()
        index += 1
    require(not stack and not in_string, f"Truncated embedded JSON: {variable}")
    end = index
    try:
        payload = json.loads(text[start:end])
    except ValueError as exc:
        raise AuditError(f"Invalid embedded JSON {variable}: {exc}") from exc
    return payload, start, end


def html_semantics(
    name: str,
    frozen_path: Path,
    live_path: Path,
    external_json: dict[str, tuple[Any, Any]],
) -> dict[str, Any]:
    try:
        frozen_text = frozen_path.read_text(encoding="utf-8")
        live_text = live_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AuditError(f"Unreadable UTF-8 HTML {name}: {exc}") from exc
    payload_rows: list[dict[str, Any]] = []
    frozen_ranges: list[tuple[int, int, str]] = []
    live_ranges: list[tuple[int, int, str]] = []
    all_exact = True
    for variable, external_name in HTML_PAYLOADS[name]:
        frozen_payload, frozen_start, frozen_end = extract_json_assignment(
            frozen_text, variable
        )
        live_payload, live_start, live_end = extract_json_assignment(
            live_text, variable
        )
        frozen_external, live_external = external_json[external_name]
        frozen_payload_sha = sha256_bytes(canonical_bytes(frozen_payload))
        live_payload_sha = sha256_bytes(canonical_bytes(live_payload))
        frozen_external_sha = sha256_bytes(canonical_bytes(frozen_external))
        live_external_sha = sha256_bytes(canonical_bytes(live_external))
        frozen_exact = frozen_payload_sha == frozen_external_sha
        live_exact = live_payload_sha == live_external_sha
        all_exact = all_exact and frozen_exact and live_exact
        payload_rows.append(
            {
                "variable": variable,
                "external_artifact": external_name,
                "frozen_payload_canonical_sha256": frozen_payload_sha,
                "live_payload_canonical_sha256": live_payload_sha,
                "frozen_external_canonical_sha256": frozen_external_sha,
                "live_external_canonical_sha256": live_external_sha,
                "frozen_payload_matches_external": frozen_exact,
                "live_payload_matches_external": live_exact,
                "payload_changed": frozen_payload_sha != live_payload_sha,
            }
        )
        frozen_ranges.append((frozen_start, frozen_end, variable))
        live_ranges.append((live_start, live_end, variable))

    def stripped(text: str, ranges: list[tuple[int, int, str]]) -> str:
        result = text
        for start, end, variable in sorted(ranges, reverse=True):
            result = (
                result[:start]
                + f'"<AUDITED_EMBEDDED_JSON:{variable}>"'
                + result[end:]
            )
        return result

    frozen_normalized = normalize_html(frozen_text)
    live_normalized = normalize_html(live_text)
    frozen_shell = normalize_html(stripped(frozen_text, frozen_ranges))
    live_shell = normalize_html(stripped(live_text, live_ranges))
    return {
        "method": "balanced_json_assignment_extraction_and_normalized_shell_hash",
        "payloads": payload_rows,
        "payload_count": len(payload_rows),
        "all_embedded_payloads_match_external_artifacts": all_exact,
        "frozen_normalized_sha256": sha256_bytes(frozen_normalized),
        "live_normalized_sha256": sha256_bytes(live_normalized),
        "normalized_content_equal": frozen_normalized == live_normalized,
        "frozen_payload_stripped_shell_sha256": sha256_bytes(frozen_shell),
        "live_payload_stripped_shell_sha256": sha256_bytes(live_shell),
        "payload_stripped_shell_equal": frozen_shell == live_shell,
        "rendered_dom_or_interaction_semantics_resolved": False,
        "browser_execution_performed": False,
        "domain_meaning_or_policy_correctness_resolved": False,
    }


def ndjson_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(row) for row in rows)


def output_receipt(path: Path, record_count: int) -> dict[str, Any]:
    item = file_item(path)
    item["record_count"] = record_count
    return item


def cleanup_temporary(path: Path, expected_parent: Path) -> None:
    path = path.resolve()
    expected_parent = expected_parent.resolve()
    require(
        path.parent == expected_parent
        and path.name.startswith(".planning-dashboard-semantics."),
        f"Unsafe temporary cleanup target: {path}",
    )

    def onexc(function: Any, target: str, error: BaseException) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=onexc)


def write_if_changed(path: Path, body: bytes) -> bool:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == body:
        return False
    require(not path.exists() or path.is_file(), f"Not a file: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(body)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def _validate_existing_pointer(pointer_path: Path) -> None:
    if not pointer_path.exists():
        return
    pointer = read_json(pointer_path)
    require(isinstance(pointer, dict), "Existing pointer must be an object")
    require(
        pointer.get("schema")
        == "post-weekly-refresh-planning-dashboard-semantics-pointer-v1",
        "Existing pointer schema invalid",
    )
    require_false_authority(pointer, "existing_pointer")
    manifest_path = validate_file_item(pointer.get("manifest"), "Existing manifest")
    manifest = read_json(manifest_path)
    require(isinstance(manifest, dict), "Existing manifest must be an object")
    require(
        manifest.get("audit_id") == pointer.get("audit_id"),
        "Existing pointer/manifest ID mismatch",
    )
    require_false_authority(manifest, "existing_manifest")
    for label, item in (manifest.get("outputs") or {}).items():
        validate_file_item(item, f"Existing output {label}")


def build_audit(
    *,
    root: Path = ROOT,
    drift_pointer_path: Path = DEFAULT_DRIFT_POINTER,
    out_root: Path = DEFAULT_OUT_ROOT,
    producer_path: Path | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    producer_path = (producer_path or Path(__file__)).resolve()
    pointer_path = drift_pointer_path.resolve()
    out_root = out_root.resolve()
    pointer, drift_manifest, target_rows = validate_drift_pointer(pointer_path)
    drift_manifest_path = Path(str(pointer["manifest"]["path"])).resolve()
    file_drift_path = Path(
        str(drift_manifest["outputs"]["file_drift"]["path"])
    ).resolve()

    row_by_name = {str(row["name"]): row for row in target_rows}
    loaded_json: dict[str, tuple[Any, Any]] = {}
    semantic_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []

    for name in JSON_TARGETS:
        source = row_by_name[name]
        frozen_path = Path(str(source["frozen_path"])).resolve()
        live_path = Path(str(source["live_path"])).resolve()
        frozen = read_json(frozen_path)
        live = read_json(live_path)
        loaded_json[name] = (frozen, live)
        frozen_profile = structural_profile(frozen)
        live_profile = structural_profile(live)
        closure = domain_closure(name, frozen, live)
        semantic_rows.append(
            {
                "schema": "post-weekly-refresh-planning-json-semantics-v1",
                "name": name,
                "artifact_type": "json",
                "frozen": file_item(frozen_path),
                "live": file_item(live_path),
                "frozen_canonical_sha256": sha256_bytes(canonical_bytes(frozen)),
                "live_canonical_sha256": sha256_bytes(canonical_bytes(live)),
                "structural_profile": {
                    "frozen": frozen_profile,
                    "live": live_profile,
                    "schema_equal": (
                        frozen_profile["schema_sha256"]
                        == live_profile["schema_sha256"]
                    ),
                },
                "value_diff": json_diff(frozen, live),
                "domain_closure": closure,
                "accepted_legislation_causation_resolved": False,
                "authoritative_for_legislation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
                "production_or_mainframe_written": False,
                "sync_or_promotion_performed": False,
            }
        )
        unresolved_rows.append(
            {
                "schema": "post-weekly-refresh-planning-semantic-gap-v1",
                "name": name,
                "resolved": False,
                "resolved_facts": [
                    "frozen/live byte identity",
                    "JSON structural profile",
                    "numeric and scalar path changes",
                    "declared domain identity/alignment closure",
                ],
                "unresolved": [
                    "domain meaning and policy correctness",
                    "causation by accepted legislation recomputation",
                    "fitness for scoring, exclusion, sync, or promotion",
                ],
                "authoritative_for_legislation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
                "sync_or_promotion_performed": False,
            }
        )

    for name in HTML_TARGETS:
        source = row_by_name[name]
        frozen_path = Path(str(source["frozen_path"])).resolve()
        live_path = Path(str(source["live_path"])).resolve()
        evidence = html_semantics(
            name, frozen_path, live_path, loaded_json
        )
        semantic_rows.append(
            {
                "schema": "post-weekly-refresh-planning-html-semantics-v1",
                "name": name,
                "artifact_type": "html",
                "frozen": file_item(frozen_path),
                "live": file_item(live_path),
                "html_semantics": evidence,
                "accepted_legislation_causation_resolved": False,
                "authoritative_for_legislation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
                "production_or_mainframe_written": False,
                "sync_or_promotion_performed": False,
            }
        )
        unresolved = [
            "rendered DOM and browser interaction behavior",
            "domain meaning and policy correctness",
            "causation by accepted legislation recomputation",
            "fitness for scoring, exclusion, sync, or promotion",
        ]
        if not evidence["payload_stripped_shell_equal"]:
            unresolved.append("payload-stripped executable HTML shell change")
        if not evidence["all_embedded_payloads_match_external_artifacts"]:
            unresolved.append("embedded payload/external JSON identity")
        unresolved_rows.append(
            {
                "schema": "post-weekly-refresh-planning-semantic-gap-v1",
                "name": name,
                "resolved": False,
                "resolved_facts": [
                    "frozen/live byte and normalized-content identity",
                    "balanced embedded JSON extraction",
                    "embedded payload/external JSON canonical identity",
                    "payload-stripped normalized shell identity",
                ],
                "unresolved": unresolved,
                "authoritative_for_legislation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
                "sync_or_promotion_performed": False,
            }
        )

    semantic_rows.sort(key=lambda row: row["name"])
    unresolved_rows.sort(key=lambda row: row["name"])
    require(
        {row["name"] for row in semantic_rows} == TARGETS
        and len(semantic_rows) == len(TARGETS),
        "Semantic-row target closure failed",
    )
    require(
        {row["name"] for row in unresolved_rows} == TARGETS
        and len(unresolved_rows) == len(TARGETS),
        "Unresolved-row target closure failed",
    )
    require_false_authority(semantic_rows, "semantic_rows")
    require_false_authority(unresolved_rows, "unresolved_rows")

    semantic_body = ndjson_bytes(semantic_rows)
    unresolved_body = ndjson_bytes(unresolved_rows)
    producer = file_item(producer_path)
    bound_receipts = {
        "downstream_drift_pointer": file_item(pointer_path),
        "downstream_drift_manifest": file_item(drift_manifest_path),
        "downstream_file_drift": file_item(file_drift_path),
    }
    identity = {
        "schema": "post-weekly-refresh-planning-dashboard-semantics-identity-v1",
        "downstream_drift_audit_id": pointer["audit_id"],
        "bound_receipts": bound_receipts,
        "producer": producer,
        "target_names": sorted(TARGETS),
        "artifact_semantics_sha256": sha256_bytes(semantic_body),
        "artifact_semantics_bytes": len(semantic_body),
        "unresolved_semantics_sha256": sha256_bytes(unresolved_body),
        "unresolved_semantics_bytes": len(unresolved_body),
        "authority": {
            "authoritative_for_legislation": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "sync_or_promotion_performed": False,
            "production_or_mainframe_written": False,
            "network_executed": False,
        },
    }
    audit_id = sha256_bytes(canonical_bytes(identity))
    generated_epoch = max(
        [
            producer_path.stat().st_mtime,
            pointer_path.stat().st_mtime,
            drift_manifest_path.stat().st_mtime,
            file_drift_path.stat().st_mtime,
            *[
                Path(str(row[f"{side}_path"])).stat().st_mtime
                for row in target_rows
                for side in ("frozen", "live")
            ],
        ]
    )
    generated_at = datetime.fromtimestamp(
        generated_epoch, timezone.utc
    ).isoformat()
    snapshot_dir = out_root / "snapshots" / audit_id
    artifact_path = snapshot_dir / "artifact_semantics.ndjson"
    unresolved_path = snapshot_dir / "unresolved_semantics.ndjson"
    manifest_path = snapshot_dir / "manifest.json"
    manifest = {
        "schema": "post-weekly-refresh-planning-dashboard-semantics-manifest-v1",
        "audit_id": audit_id,
        "generated_at": generated_at,
        "status": "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
        "downstream_drift_audit_id": pointer["audit_id"],
        "bound_receipts": bound_receipts,
        "producer": producer,
        "summary": {
            "target_count": len(TARGETS),
            "json_target_count": len(JSON_TARGETS),
            "html_target_count": len(HTML_TARGETS),
            "unresolved_target_count": len(unresolved_rows),
            "html_payload_count": sum(
                len(HTML_PAYLOADS[name]) for name in HTML_TARGETS
            ),
            "html_payload_external_identity_proven_count": sum(
                1
                for row in semantic_rows
                if row["artifact_type"] == "html"
                for payload in row["html_semantics"]["payloads"]
                if payload["frozen_payload_matches_external"]
                and payload["live_payload_matches_external"]
            ),
            "html_payload_stripped_shell_equal_count": sum(
                1
                for row in semantic_rows
                if row["artifact_type"] == "html"
                and row["html_semantics"]["payload_stripped_shell_equal"]
            ),
            "domain_meaning_or_policy_correctness_resolved_count": 0,
            "accepted_legislation_causation_resolved_count": 0,
        },
        "outputs": {
            "artifact_semantics": {
                "path": str(artifact_path.resolve()),
                "bytes": len(semantic_body),
                "sha256": sha256_bytes(semantic_body),
                "record_count": len(semantic_rows),
            },
            "unresolved_semantics": {
                "path": str(unresolved_path.resolve()),
                "bytes": len(unresolved_body),
                "sha256": sha256_bytes(unresolved_body),
                "record_count": len(unresolved_rows),
            },
        },
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "accepted_legislation_recomputation": False,
        "network_executed": False,
        "production_or_mainframe_written": False,
        "sync_or_promotion_performed": False,
    }
    require_false_authority(manifest, "manifest")
    manifest_body = pretty_bytes(manifest)

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "snapshots").mkdir(parents=True, exist_ok=True)
    pointer_output_path = out_root / "current.json"
    _validate_existing_pointer(pointer_output_path)
    created = False
    if snapshot_dir.exists():
        require(snapshot_dir.is_dir(), "Snapshot target is not a directory")
        require(
            artifact_path.is_file()
            and artifact_path.read_bytes() == semantic_body,
            "Existing content-addressed artifact ledger drift",
        )
        require(
            unresolved_path.is_file()
            and unresolved_path.read_bytes() == unresolved_body,
            "Existing content-addressed unresolved ledger drift",
        )
        require(
            manifest_path.is_file()
            and manifest_path.read_bytes() == manifest_body,
            "Existing content-addressed manifest drift",
        )
    else:
        temporary = (
            out_root / "snapshots"
            / f".planning-dashboard-semantics.{uuid.uuid4().hex}"
        )
        try:
            temporary.mkdir()
            (temporary / "artifact_semantics.ndjson").write_bytes(semantic_body)
            (temporary / "unresolved_semantics.ndjson").write_bytes(unresolved_body)
            (temporary / "manifest.json").write_bytes(manifest_body)
            os.replace(temporary, snapshot_dir)
            created = True
        finally:
            if temporary.exists():
                cleanup_temporary(temporary, out_root / "snapshots")

    pointer_output = {
        "schema": "post-weekly-refresh-planning-dashboard-semantics-pointer-v1",
        "audit_id": audit_id,
        "snapshot_dir": str(snapshot_dir.resolve()),
        "manifest": file_item(manifest_path),
        "producer": producer,
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "network_executed": False,
        "production_or_mainframe_written": False,
        "sync_or_promotion_performed": False,
    }
    require_false_authority(pointer_output, "pointer")
    write_if_changed(pointer_output_path, pretty_bytes(pointer_output))
    return pointer_output_path, manifest, created


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drift-pointer", type=Path, default=DEFAULT_DRIFT_POINTER
    )
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = parser.parse_args()
    pointer_path, manifest, created = build_audit(
        drift_pointer_path=args.drift_pointer,
        out_root=args.out_root,
    )
    print(
        json.dumps(
            {
                "audit_id": manifest["audit_id"],
                "created": created,
                "pointer": str(pointer_path),
                "status": manifest["status"],
                "summary": manifest["summary"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

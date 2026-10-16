# -*- coding: utf-8 -*-
"""Integrate a human-Greenlit culling proposal into an isolated staged ledger.

This tool is intentionally narrower than production promotion.  It verifies
the coordinator receipt and every lane record, records the user's exact
authorization, preserves unresolved decisions as visible/non-scoring, expands
included candidate decisions into category membership edges, and emits a
reversible supersession ledger for source decisions reconciled away by a
stronger candidate-level decision.

It never mutates discovery, the main corpus, production workbooks, rankings,
or external systems.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "legislation-greenlit-culling-integration-v1"
DECISION_SCHEMA = "legislation-greenlit-integrated-decision-v1"
EDGE_SCHEMA = "legislation-greenlit-integrated-membership-edge-v1"
SUPERSESSION_SCHEMA = "legislation-greenlit-source-supersession-v1"
RATIFICATION_SCHEMA = "legislation-human-greenlight-ratification-v1"
RECEIPT_SCHEMA = "legislation-greenlit-culling-integration-receipt-v1"
HEX_RE = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_SOURCE_COUNTS = {
    "amendment_partition": 3703,
    "short_act_collision": 454,
    "bill_recall": 23,
}
EXPECTED_ACTION_TYPES = {
    "exclude_candidate_false_positive",
    "exclude_membership_edge_false_positive",
    "upsert_candidate_memberships",
    "upsert_membership_edge",
}
EXPECTED_MEASURE_WEIGHTS = {
    "hamdt": 0.5,
    "samdt": 0.5,
}


class IntegrationError(ValueError):
    """The Greenlit proposal cannot be integrated without weakening a gate."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def seal(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["record_content_sha256"] = hashlib.sha256(
        canonical_bytes(result)
    ).hexdigest()
    return result


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationError(f"invalid JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrationError(f"JSON root is not an object: {path}")
    return value


def read_ndjson(path: Path) -> list[tuple[dict[str, Any], str]]:
    result: list[tuple[dict[str, Any], str]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise IntegrationError(f"cannot read NDJSON {path}: {exc}") from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            raise IntegrationError(f"{path}:{number}: blank NDJSON line")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IntegrationError(
                f"{path}:{number}: invalid JSON: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise IntegrationError(f"{path}:{number}: row is not an object")
        result.append((value, sha256_text(line)))
    return result


def file_receipt(path: Path, *, records: int | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    result = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    if records is not None:
        result["records"] = records
    return result


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(
        path,
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n",
    )


def write_ndjson(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    body = b"".join(canonical_bytes(dict(row)) + b"\n" for row in rows)
    atomic_write(path, body)


def require_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    parts = text.split(":")
    if (
        len(parts) != 3
        or parts[0] not in {"116", "117", "118", "119"}
        or not parts[1]
        or not parts[2].isdigit()
    ):
        raise IntegrationError(f"invalid canonical key: {value!r}")
    return text


def measure_weight(key: str) -> float:
    measure_type = key.split(":")[1]
    return EXPECTED_MEASURE_WEIGHTS.get(measure_type, 1.0)


def decision_id(
    canonical_key: str,
    decision_scope: str,
    query_id: Any,
) -> str:
    if decision_scope == "candidate":
        if query_id not in {None, ""}:
            raise IntegrationError(
                f"candidate decision unexpectedly carries query_id: "
                f"{canonical_key}"
            )
        return f"candidate::{canonical_key}"
    if decision_scope == "membership_edge":
        query = str(query_id or "").strip()
        if not query:
            raise IntegrationError(
                f"membership decision lacks query_id: {canonical_key}"
            )
        return f"edge::{canonical_key}::{query}"
    raise IntegrationError(
        f"unsupported decision scope {decision_scope!r}: {canonical_key}"
    )


def category_ids(registry: Mapping[str, Any]) -> set[str]:
    categories = registry.get("categories")
    if not isinstance(categories, list):
        raise IntegrationError("category registry has no categories list")
    result = {
        str(row.get("id") or "").strip()
        for row in categories
        if isinstance(row, dict)
    }
    result.discard("")
    if len(result) != 19:
        raise IntegrationError(
            f"expected 19 canonical categories, found {len(result)}"
        )
    return result


def verify_receipt_outputs(
    coordinator_dir: Path,
    receipt: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    outputs = receipt.get("outputs")
    if not isinstance(outputs, dict):
        raise IntegrationError("coordinator receipt has no outputs")
    checked: dict[str, dict[str, Any]] = {}
    for name in (
        "proposed_integration_actions.ndjson",
        "unresolved_for_ratification.ndjson",
        "summary.json",
        "bill_recall_adversarial_review.json",
    ):
        declared = outputs.get(name)
        path = coordinator_dir / name
        if not isinstance(declared, dict) or not path.is_file():
            raise IntegrationError(f"coordinator output is absent: {name}")
        actual = file_receipt(
            path,
            records=(
                sum(1 for _ in path.open(encoding="utf-8-sig"))
                if path.suffix == ".ndjson"
                else 1
            ),
        )
        if (
            actual["sha256"] != declared.get("sha256")
            or actual["bytes"] != declared.get("bytes")
            or actual["records"] != declared.get("records")
        ):
            raise IntegrationError(f"coordinator output drifted: {name}")
        checked[name] = actual
    return checked


def source_paths(coordinator_dir: Path) -> dict[str, Path]:
    root = coordinator_dir.parent
    return {
        "amendment_partition": (
            root
            / "amendment_culling_opus5_20260728a"
            / "amendment_partition_3703.ndjson"
        ),
        "short_act_collision": (
            root
            / "short_act_collision_culling_opus5_20260728a"
            / "proposal_adjudications_454.ndjson"
        ),
        "bill_recall": (
            root
            / "bill_recall_culling_opus5_20260728a"
            / "proposal_adjudications_23.ndjson"
        ),
    }


def source_natural_key(lane: str, row: Mapping[str, Any]) -> str:
    key = require_key(row.get("canonical_key"))
    if lane in {"amendment_partition", "bill_recall"}:
        return decision_id(key, "candidate", None)
    return decision_id(key, "membership_edge", row.get("query_id"))


def build_source_index(
    paths: Mapping[str, Path],
) -> tuple[
    dict[str, dict[str, tuple[dict[str, Any], str]]],
    dict[str, dict[str, Any]],
]:
    indexes: dict[str, dict[str, tuple[dict[str, Any], str]]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for lane, path in paths.items():
        rows = read_ndjson(path)
        expected = EXPECTED_SOURCE_COUNTS[lane]
        if len(rows) != expected:
            raise IntegrationError(
                f"{lane} source count drifted: {len(rows)} != {expected}"
            )
        index: dict[str, tuple[dict[str, Any], str]] = {}
        for row, line_sha in rows:
            natural = source_natural_key(lane, row)
            if natural in index:
                raise IntegrationError(
                    f"duplicate {lane} source decision: {natural}"
                )
            index[natural] = (row, line_sha)
        indexes[lane] = index
        receipts[lane] = file_receipt(path, records=len(rows))
    return indexes, receipts


def validate_action(
    row: Mapping[str, Any],
    *,
    allowed_categories: set[str],
) -> tuple[str, str, str]:
    key = require_key(row.get("canonical_key"))
    action = str(row.get("action") or "")
    scope = str(row.get("decision_scope") or "")
    if action not in EXPECTED_ACTION_TYPES:
        raise IntegrationError(f"unsupported action {action!r}: {key}")
    if row.get("greenlight_required") is not True:
        raise IntegrationError(f"action was not Greenlight-gated: {key}")
    if float(row.get("measure_weight")) != measure_weight(key):
        raise IntegrationError(f"measure weight drifted: {key}")
    categories = row.get("category_ids")
    if (
        not isinstance(categories, list)
        or len(categories) != len(set(categories))
        or any(category not in allowed_categories for category in categories)
    ):
        raise IntegrationError(f"invalid action categories: {key}")
    if action.startswith("upsert") and not categories:
        raise IntegrationError(f"include action has no categories: {key}")
    if (
        action == "exclude_candidate_false_positive"
        and categories
    ):
        raise IntegrationError(f"candidate exclusion carries categories: {key}")
    if (
        action == "exclude_membership_edge_false_positive"
        and categories != [str(row.get("query_id") or "")]
    ):
        raise IntegrationError(
            f"membership exclusion category/query mismatch: {key}"
        )
    if (
        scope == "candidate"
        and action not in {
            "exclude_candidate_false_positive",
            "upsert_candidate_memberships",
        }
    ) or (
        scope == "membership_edge"
        and action not in {
            "exclude_membership_edge_false_positive",
            "upsert_membership_edge",
        }
    ):
        raise IntegrationError(f"action/scope conflict: {key}")
    natural = decision_id(key, scope, row.get("query_id"))
    lane = str(row.get("source_lane") or "")
    if lane not in EXPECTED_SOURCE_COUNTS:
        raise IntegrationError(f"unknown source lane {lane!r}: {key}")
    return natural, lane, key


def validate_hold(
    row: Mapping[str, Any],
) -> tuple[str, str, str]:
    key = require_key(row.get("canonical_key"))
    if (
        row.get("disposition") != "flagged"
        or row.get("human_ratification_required") is not True
        or float(row.get("measure_weight")) != measure_weight(key)
    ):
        raise IntegrationError(f"invalid held decision: {key}")
    scope = str(row.get("decision_scope") or "")
    natural = decision_id(key, scope, row.get("query_id"))
    lane = str(row.get("source_lane") or "")
    if lane not in EXPECTED_SOURCE_COUNTS:
        raise IntegrationError(f"unknown held source lane {lane!r}: {key}")
    return natural, lane, key


def classification(action: Mapping[str, Any]) -> tuple[str, bool, str | None]:
    name = str(action["action"])
    if name.startswith("exclude"):
        return "excluded_false_positive", False, "ratified_exclusion"
    if action.get("ultimate_direction") in {-1, 0, 1}:
        return "included_scoring_ready", True, None
    return (
        "included_membership_only_non_scoring",
        False,
        "missing_scoring_direction",
    )


def integrate(
    *,
    coordinator_dir: Path,
    out_dir: Path,
    expected_receipt_sha256: str,
    expected_action_count: int,
    expected_hold_count: int,
    greenlight_event_id: str,
    greenlight_text: str,
    ratified_by: str,
    ratified_at: str,
) -> dict[str, Any]:
    coordinator_dir = coordinator_dir.resolve()
    out_dir = out_dir.resolve()
    receipt_path = coordinator_dir / "receipt.json"
    if not HEX_RE.fullmatch(expected_receipt_sha256):
        raise IntegrationError("expected coordinator receipt SHA-256 is invalid")
    if not receipt_path.is_file():
        raise IntegrationError("coordinator receipt is absent")
    if sha256_file(receipt_path) != expected_receipt_sha256:
        raise IntegrationError("coordinator receipt SHA-256 drifted")
    coordinator_receipt = read_object(receipt_path)
    if not (
        coordinator_receipt.get("schema")
        == "legislation-culling-coordinator-receipt-v1"
        and coordinator_receipt.get("status")
        == "DECISION_REQUIRED_BEFORE_INTEGRATION"
        and coordinator_receipt.get("greenlight_required") is True
    ):
        raise IntegrationError("coordinator receipt is not Greenlight-ready")
    coordinator_outputs = verify_receipt_outputs(
        coordinator_dir,
        coordinator_receipt,
    )
    registry_path = Path(
        str(
            (coordinator_receipt.get("inputs") or {})
            .get("canonical_category_registry", {})
            .get("path")
            or ""
        )
    ).resolve()
    if not registry_path.is_file():
        raise IntegrationError("canonical category registry is absent")
    registry_receipt = file_receipt(registry_path, records=19)
    declared_registry = (
        coordinator_receipt.get("inputs") or {}
    ).get("canonical_category_registry") or {}
    if (
        registry_receipt["sha256"] != declared_registry.get("sha256")
        or registry_receipt["bytes"] != declared_registry.get("bytes")
    ):
        raise IntegrationError("canonical category registry drifted")
    allowed_categories = category_ids(read_object(registry_path))

    actions_with_hash = read_ndjson(
        coordinator_dir / "proposed_integration_actions.ndjson"
    )
    holds_with_hash = read_ndjson(
        coordinator_dir / "unresolved_for_ratification.ndjson"
    )
    if len(actions_with_hash) != expected_action_count:
        raise IntegrationError(
            f"action count drifted: {len(actions_with_hash)} "
            f"!= {expected_action_count}"
        )
    if len(holds_with_hash) != expected_hold_count:
        raise IntegrationError(
            f"hold count drifted: {len(holds_with_hash)} "
            f"!= {expected_hold_count}"
        )

    sources, source_receipts = build_source_index(source_paths(coordinator_dir))
    ratification_basis = {
        "schema": RATIFICATION_SCHEMA,
        "ratified_by": ratified_by,
        "ratified_at": ratified_at,
        "authorization_text": greenlight_text,
        "glassbox_decision_event_id": greenlight_event_id,
        "coordinator_receipt_sha256": expected_receipt_sha256,
        "action_ledger_sha256": coordinator_outputs[
            "proposed_integration_actions.ndjson"
        ]["sha256"],
        "held_ledger_sha256": coordinator_outputs[
            "unresolved_for_ratification.ndjson"
        ]["sha256"],
        "authorized_scope": {
            "apply_action_rows": expected_action_count,
            "preserve_held_rows_visible_non_scoring": expected_hold_count,
            "run_isolated_paired_shadow": True,
        },
        "not_authorized": [
            "production_promotion",
            "external_sync",
            "mainframe_write",
            "main_corpus_write",
        ],
    }
    ratification = seal(ratification_basis)
    ratification_sha = hashlib.sha256(canonical_bytes(ratification)).hexdigest()

    integrated: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()
    decisive_candidates: dict[str, dict[str, Any]] = {}

    for action, action_sha in actions_with_hash:
        natural, lane, key = validate_action(
            action,
            allowed_categories=allowed_categories,
        )
        if natural in seen:
            raise IntegrationError(f"duplicate integrated decision: {natural}")
        seen.add(natural)
        source = sources[lane].get(natural)
        if source is None:
            raise IntegrationError(
                f"action lacks an exact source-lane decision: {natural}"
            )
        source_row, source_sha = source
        if source_row.get("disposition") != action.get("disposition"):
            raise IntegrationError(f"action/source disposition drifted: {natural}")
        state, scoring_eligible, zero_reason = classification(action)
        output = {
            "schema": DECISION_SCHEMA,
            "decision_id": natural,
            "canonical_key": key,
            "decision_scope": action["decision_scope"],
            "query_id": action.get("query_id"),
            "action": action["action"],
            "ratified_disposition": action["disposition"],
            "integration_state": state,
            "category_ids": list(action["category_ids"]),
            "ultimate_direction": action.get("ultimate_direction"),
            "measure_weight": float(action["measure_weight"]),
            "scoring_eligible": scoring_eligible,
            "zero_weight_reason": zero_reason,
            "reason": action.get("reason"),
            "source_lane": lane,
            "source_action_record_sha256": action_sha,
            "source_lane_record_sha256": source_sha,
            "ratification_record_sha256": ratification_sha,
            "reversible": True,
            "production_written": False,
        }
        integrated.append(output)
        if action["decision_scope"] == "candidate":
            decisive_candidates[key] = output
        if action["action"].startswith("exclude"):
            exclusions.append(output)
        else:
            for category in action["category_ids"]:
                edges.append(
                    {
                        "schema": EDGE_SCHEMA,
                        "canonical_key": key,
                        "category_id": category,
                        "source_decision_id": natural,
                        "measure_weight": float(action["measure_weight"]),
                        "ultimate_direction": action.get(
                            "ultimate_direction"
                        ),
                        "integration_state": state,
                        "scoring_eligible": scoring_eligible,
                        "zero_weight_reason": zero_reason,
                        "ratification_record_sha256": ratification_sha,
                    }
                )

    for hold_row, hold_sha in holds_with_hash:
        natural, lane, key = validate_hold(hold_row)
        if natural in seen:
            raise IntegrationError(
                f"held and decisive ledgers overlap: {natural}"
            )
        seen.add(natural)
        source = sources[lane].get(natural)
        if source is None:
            raise IntegrationError(
                f"hold lacks an exact source-lane decision: {natural}"
            )
        source_row, source_sha = source
        if source_row.get("disposition") != "flagged":
            raise IntegrationError(f"hold/source disposition drifted: {natural}")
        output = {
            "schema": DECISION_SCHEMA,
            "decision_id": natural,
            "canonical_key": key,
            "decision_scope": hold_row["decision_scope"],
            "query_id": hold_row.get("query_id"),
            "action": "hold_visible_non_scoring",
            "ratified_disposition": "flagged",
            "integration_state": "held_visible_non_scoring",
            "category_ids": (
                [hold_row["query_id"]]
                if hold_row.get("query_id")
                else []
            ),
            "ultimate_direction": None,
            "measure_weight": float(hold_row["measure_weight"]),
            "scoring_eligible": False,
            "zero_weight_reason": "human_review_hold",
            "reason": hold_row.get("reason"),
            "source_lane": lane,
            "source_hold_record_sha256": hold_sha,
            "source_lane_record_sha256": source_sha,
            "ratification_record_sha256": ratification_sha,
            "reversible": True,
            "production_written": False,
        }
        integrated.append(output)
        held.append(output)

    source_universe = {
        natural
        for index in sources.values()
        for natural in index
    }
    extras = seen - source_universe
    if extras:
        raise IntegrationError(
            "integrated decisions are outside the source universe: "
            + ", ".join(sorted(extras)[:10])
        )
    missing = source_universe - seen
    superseded: list[dict[str, Any]] = []
    for natural in sorted(missing):
        if not natural.startswith("edge::"):
            raise IntegrationError(
                f"unaccounted non-edge source decision: {natural}"
            )
        _, key, query_id = natural.split("::", 2)
        candidate = decisive_candidates.get(key)
        source = sources["short_act_collision"].get(natural)
        if (
            candidate is None
            or candidate["action"] != "exclude_candidate_false_positive"
            or source is None
        ):
            raise IntegrationError(
                f"source decision lacks an explicit superseding exclusion: "
                f"{natural}"
            )
        source_row, source_sha = source
        superseded.append(
            {
                "schema": SUPERSESSION_SCHEMA,
                "source_decision_id": natural,
                "canonical_key": key,
                "query_id": query_id,
                "source_lane": "short_act_collision",
                "source_disposition": source_row.get("disposition"),
                "source_lane_record_sha256": source_sha,
                "superseded_by_decision_id": candidate["decision_id"],
                "supersession_rule": (
                    "candidate-level ratified false-positive exclusion "
                    "subsumes the narrower short-act edge decision"
                ),
                "reversible": True,
                "production_written": False,
            }
        )
    if len(superseded) != 15:
        raise IntegrationError(
            f"expected 15 reconciled supersessions, found {len(superseded)}"
        )

    integrated.sort(key=lambda row: row["decision_id"])
    exclusions.sort(key=lambda row: row["decision_id"])
    held.sort(key=lambda row: row["decision_id"])
    edges.sort(
        key=lambda row: (row["canonical_key"], row["category_id"])
    )
    if len({(row["canonical_key"], row["category_id"]) for row in edges}) != len(
        edges
    ):
        raise IntegrationError("expanded membership edges contain duplicates")

    action_counts = Counter(
        row["action"] for row in integrated if row["action"] != "hold_visible_non_scoring"
    )
    state_counts = Counter(row["integration_state"] for row in integrated)
    edge_state_counts = Counter(row["integration_state"] for row in edges)
    scoring_candidates = {
        row["canonical_key"] for row in edges if row["scoring_eligible"]
    }
    membership_only_candidates = {
        row["canonical_key"] for row in edges if not row["scoring_eligible"]
    }

    generation_basis = {
        "schema": SCHEMA,
        "coordinator_receipt_sha256": expected_receipt_sha256,
        "ratification_record_sha256": ratification_sha,
        "source_lane_sha256": {
            lane: value["sha256"]
            for lane, value in sorted(source_receipts.items())
        },
        "integrated_decisions_sha256": hashlib.sha256(
            b"".join(canonical_bytes(row) + b"\n" for row in integrated)
        ).hexdigest(),
        "expanded_membership_edges_sha256": hashlib.sha256(
            b"".join(canonical_bytes(row) + b"\n" for row in edges)
        ).hexdigest(),
        "supersessions_sha256": hashlib.sha256(
            b"".join(canonical_bytes(row) + b"\n" for row in superseded)
        ).hexdigest(),
    }
    generation_id = hashlib.sha256(
        canonical_bytes(generation_basis)
    ).hexdigest()

    gates = {
        "coordinator_receipt_hash_verified": True,
        "coordinator_output_hashes_verified": True,
        "all_4180_source_decisions_accounted": (
            len(integrated) + len(superseded)
            == expected_action_count + expected_hold_count + 15
        ),
        "all_2981_greenlit_actions_applied": (
            len(actions_with_hash) == expected_action_count
        ),
        "all_1184_held_decisions_visible_non_scoring": (
            len(held) == expected_hold_count
            and all(not row["scoring_eligible"] for row in held)
        ),
        "15_reconciled_short_act_supersessions_preserved": (
            len(superseded) == 15
        ),
        "bill_1_amendment_0_5_weights_enforced": True,
        "production_promotion_authorized": False,
        "canonical_promotion_gate_passed": False,
    }
    failed_gates = [
        name
        for name, ok in gates.items()
        if not ok
        and name
        not in (
            "production_promotion_authorized",
            "canonical_promotion_gate_passed",
        )
    ]
    if failed_gates:
        raise IntegrationError(
            "integration gates failed: " + ", ".join(failed_gates)
        )
    missing_direction_edges = edge_state_counts[
        "included_membership_only_non_scoring"
    ]

    if out_dir.exists() and any(out_dir.iterdir()):
        existing = out_dir / "integration_receipt.json"
        if existing.is_file():
            value = read_object(existing)
            if value.get("generation_id") == generation_id:
                content_sha = value.get("record_content_sha256")
                unsealed = dict(value)
                unsealed.pop("record_content_sha256", None)
                if content_sha != hashlib.sha256(
                    canonical_bytes(unsealed)
                ).hexdigest():
                    raise IntegrationError(
                        "existing integration receipt seal drifted"
                    )
                declared_outputs = value.get("outputs")
                if not isinstance(declared_outputs, dict):
                    raise IntegrationError(
                        "existing integration receipt lacks outputs"
                    )
                for name, declared in declared_outputs.items():
                    path = (out_dir / name).resolve()
                    if (
                        not isinstance(declared, dict)
                        or path.parent != out_dir
                        or not path.is_file()
                    ):
                        raise IntegrationError(
                            f"existing integration output is absent: {name}"
                        )
                    actual = file_receipt(
                        path,
                        records=(
                            sum(
                                1
                                for _ in path.open(
                                    encoding="utf-8-sig"
                                )
                            )
                            if path.suffix == ".ndjson"
                            else 1
                        ),
                    )
                    if any(
                        actual[field] != declared.get(field)
                        for field in ("bytes", "sha256", "records")
                    ):
                        raise IntegrationError(
                            f"existing integration output drifted: {name}"
                        )
                return value
        raise IntegrationError(f"output directory is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    output_rows = {
        "integrated_decisions.ndjson": integrated,
        "integrated_membership_edges.ndjson": edges,
        "ratified_exclusions.ndjson": exclusions,
        "held_visible_non_scoring.ndjson": held,
        "source_supersessions.ndjson": superseded,
    }
    write_json(out_dir / "ratification_receipt.json", ratification)
    for name, rows in output_rows.items():
        write_ndjson(out_dir / name, rows)
    outputs = {
        "ratification_receipt.json": file_receipt(
            out_dir / "ratification_receipt.json",
            records=1,
        ),
        **{
            name: file_receipt(out_dir / name, records=len(rows))
            for name, rows in output_rows.items()
        },
    }
    receipt = seal(
        {
            "schema": RECEIPT_SCHEMA,
            "status": "PASS_STAGED_INTEGRATION_WITH_HELD_NONSCORING",
            "generation_id": generation_id,
            "coordinator_run_id": coordinator_receipt.get("run_id"),
            "greenlight": {
                "event_id": greenlight_event_id,
                "authorization_text": greenlight_text,
                "ratified_by": ratified_by,
                "ratified_at": ratified_at,
                "ratification_record_sha256": ratification_sha,
            },
            "inputs": {
                "coordinator_receipt": file_receipt(
                    receipt_path,
                    records=1,
                ),
                "coordinator_outputs": coordinator_outputs,
                "canonical_category_registry": registry_receipt,
                "source_lanes": source_receipts,
            },
            "counts": {
                "source_decision_units": len(source_universe),
                "live_integrated_decision_units": len(integrated),
                "source_supersessions": len(superseded),
                "action_rows_applied": len(actions_with_hash),
                "held_decision_units": len(held),
                "held_unique_candidates": len(
                    {row["canonical_key"] for row in held}
                ),
                "action_counts": dict(sorted(action_counts.items())),
                "integration_state_counts": dict(
                    sorted(state_counts.items())
                ),
                "expanded_membership_edges": len(edges),
                "membership_edge_state_counts": dict(
                    sorted(edge_state_counts.items())
                ),
                "scoring_ready_candidates": len(scoring_candidates),
                "scoring_ready_category_edges": sum(
                    row["scoring_eligible"] for row in edges
                ),
                "membership_only_non_scoring_candidates": len(
                    membership_only_candidates
                ),
                "membership_only_non_scoring_category_edges": sum(
                    not row["scoring_eligible"] for row in edges
                ),
            },
            "gates": gates,
            "canonical_promotion_blockers": [
                f"{len(held)} held decision units remain unresolved by explicit policy",
                f"{missing_direction_edges} Greenlit membership edges have no scoring direction",
                "production promotion and external sync were not authorized",
            ],
            "outputs": outputs,
            "generation_basis": generation_basis,
            "safety": {
                "discovery_mutated": False,
                "main_corpus_written": False,
                "production_workbook_written": False,
                "rank_written": False,
                "external_sync_performed": False,
                "promotion_performed": False,
            },
        }
    )
    write_json(out_dir / "integration_receipt.json", receipt)
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--coordinator-dir", type=Path, required=True)
    result.add_argument("--out", type=Path, required=True)
    result.add_argument("--expected-receipt-sha256", required=True)
    result.add_argument("--expected-action-count", type=int, default=2981)
    result.add_argument("--expected-hold-count", type=int, default=1184)
    result.add_argument("--greenlight-event-id", required=True)
    result.add_argument("--greenlight-text", required=True)
    result.add_argument("--ratified-by", required=True)
    result.add_argument("--ratified-at", required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        receipt = integrate(
            coordinator_dir=args.coordinator_dir,
            out_dir=args.out,
            expected_receipt_sha256=args.expected_receipt_sha256.casefold(),
            expected_action_count=args.expected_action_count,
            expected_hold_count=args.expected_hold_count,
            greenlight_event_id=args.greenlight_event_id,
            greenlight_text=args.greenlight_text,
            ratified_by=args.ratified_by,
            ratified_at=args.ratified_at,
        )
    except (IntegrationError, OSError, json.JSONDecodeError) as exc:
        print(f"Greenlit culling integration: FAIL CLOSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""A2 orchestration and aggregate receipts for model-assisted adjudication.

The A1 framework stays immutable except for its append-only reviewer label
logs.  This module prepares reviewer-isolated packet views, atomically appends
one complete reviewer/partition response, and emits an aggregate-only A2
receipt.  Holdout outcomes remain quarantined in A2.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover
    from fec.lane_file_io import atomic_write_bytes

from .politico_adjudication import (
    FRAMEWORK_SCHEMA_ID,
    LABEL_SCHEMA_ID,
    OUTCOMES,
    PARTITIONS,
    REASON_CODES,
    REVIEWERS,
    AdjudicationFrameworkError,
    _append_lock,
    _load_anchored_receipt,
    _read_json,
    _read_ndjson,
    _require_restricted_output,
    _verify_root_binding,
    label_schema,
    verify_adjudication_framework,
)
from .schema_validation import validate_instance
from .source_manifest import canonical_json_bytes, sha256_bytes, sha256_file
from .validate import assert_public_safe


VIEW_SCHEMA_ID = "staffer-network-politico-adjudication-reviewer-views-v1"
BATCH_SCHEMA_ID = "staffer-network-politico-adjudication-label-batch-v1"
RESULTS_SCHEMA_ID = "staffer-network-politico-adjudication-results-receipt-v1"
RUN_METHOD_VERSION = "politico-dual-model-review-calibration-v1"


def batch_output_schema(
    *, reviewer_id: str, partition: str, expected_count: int
) -> dict[str, Any]:
    if reviewer_id not in REVIEWERS or partition not in PARTITIONS:
        raise AdjudicationFrameworkError("unknown reviewer or partition")
    if expected_count < 1:
        raise AdjudicationFrameworkError("batch must contain at least one candidate")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:melange:staffer-network:politico-label-batch:v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_id", "reviewer_id", "partition", "labels"],
        "properties": {
            "schema_id": {"type": "string", "const": BATCH_SCHEMA_ID},
            "reviewer_id": {"type": "string", "const": reviewer_id},
            "partition": {"type": "string", "const": partition},
            "labels": {
                "type": "array",
                "minItems": expected_count,
                "maxItems": expected_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "candidate_id", "outcome", "reason_codes", "rationale"
                    ],
                    "properties": {
                        "candidate_id": {
                            "type": "string", "pattern": r"^candidate:[0-9a-f]{64}$"
                        },
                        "outcome": {"type": "string", "enum": list(OUTCOMES)},
                        "reason_codes": {
                            "type": "array", "minItems": 1, "maxItems": 4,
                            "items": {"type": "string", "enum": list(REASON_CODES)},
                        },
                        "rationale": {
                            "type": "string", "minLength": 1, "maxLength": 500
                        },
                    },
                },
            },
        },
    }


def _instructions(reviewer_id: str, partition: str, count: int) -> str:
    return f"""# Independent identity-pair review

Reviewer: `{reviewer_id}`
Partition: `{partition}`
Expected records: {count}

Read only `packets.ndjson`. Treat each line independently. Do not inspect parent
directories, other reviewers, machine controls, or previous labels. Return one
strict JSON object conforming to `label_batch_output.schema.json`.

Outcome definitions:

- `MATCH`: the bounded pair evidence is strongly consistent with one person and
  contains no material contradiction.
- `DISTINCT`: the bounded evidence contains a clear identity contradiction.
- `POSSIBLE`: evidence is compatible but insufficient for a reasonably sure
  identity decision, including unresolved office/container or temporal gaps.
- `ABSTAIN`: the record is malformed, unsafe, or cannot be judged from the
  allowed evidence.

Use one to four allowlisted reason codes. Give a concise one-sentence rationale
based only on the two records. Never browse. Never infer hidden identifiers.
Prefer `POSSIBLE` or `ABSTAIN` over an unsupported `MATCH`. These are
`MODEL_ASSISTED` labels, not human gold and not merge authorization.
"""


def prepare_reviewer_views(
    framework_root: str | Path,
    output_root: str | Path,
    *,
    expected_receipt_sha256: str,
    synced_roots: Iterable[str | Path],
    generated_at: str,
) -> dict[str, Any]:
    """Create four isolated read-only reviewer views from the sealed packets."""

    framework = Path(framework_root).resolve()
    output = Path(output_root).resolve()
    _require_restricted_output(output, (Path(root) for root in synced_roots))
    if output.exists():
        raise AdjudicationFrameworkError(f"reviewer-view output already exists: {output}")
    verification = verify_adjudication_framework(
        framework, expected_receipt_sha256=expected_receipt_sha256
    )
    if verification["submitted_label_count"] != 0:
        raise AdjudicationFrameworkError("reviewer views require a zero-label A1 framework")
    framework_receipt = _load_anchored_receipt(
        framework, expected_receipt_sha256=expected_receipt_sha256
    )

    staging_parent = output.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=staging_parent))
    try:
        views: list[dict[str, Any]] = []
        packet_index = {
            (entry["reviewer_id"], entry["partition"]): entry
            for entry in framework_receipt["packet_files"]
        }
        for reviewer_id in REVIEWERS:
            for partition in PARTITIONS:
                source = framework / "reviewer_packets" / (
                    f"{reviewer_id}__{partition.casefold()}.ndjson"
                )
                entry = packet_index[(reviewer_id, partition)]
                if sha256_file(source) != entry["sha256"]:
                    raise AdjudicationFrameworkError("reviewer packet changed before view creation")
                leaf = staging / reviewer_id / partition.casefold()
                leaf.mkdir(parents=True)
                packet_body = source.read_bytes()
                atomic_write_bytes(leaf / "packets.ndjson", packet_body)
                schema = batch_output_schema(
                    reviewer_id=reviewer_id,
                    partition=partition,
                    expected_count=entry["records"],
                )
                atomic_write_bytes(
                    leaf / "label_batch_output.schema.json", canonical_json_bytes(schema)
                )
                atomic_write_bytes(
                    leaf / "instructions.md",
                    _instructions(reviewer_id, partition, entry["records"]).encode("utf-8"),
                )
                views.append({
                    "reviewer_id": reviewer_id,
                    "partition": partition,
                    "records": entry["records"],
                    "packet_sha256": sha256_bytes(packet_body),
                    "schema_sha256": sha256_file(
                        leaf / "label_batch_output.schema.json"
                    ),
                    "instructions_sha256": sha256_file(leaf / "instructions.md"),
                })
        receipt = {
            "schema_id": VIEW_SCHEMA_ID,
            "generated_at": generated_at,
            "status": "PASS",
            "framework_receipt_sha256": expected_receipt_sha256,
            "review_sample_count": framework_receipt["review_sample_count"],
            "reviewer_ids": list(REVIEWERS),
            "partitions": list(PARTITIONS),
            "views": sorted(views, key=lambda row: (row["reviewer_id"], row["partition"])),
            "record_level_storage": "restricted-local-only",
            "cross_reviewer_visibility": False,
            "machine_control_visibility": False,
            "failures": [],
        }
        assert_public_safe(receipt)
        atomic_write_bytes(staging / "reviewer_views_receipt.json", canonical_json_bytes(receipt))
        os.replace(staging, output)
        return receipt
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def append_label_batch(
    framework_root: str | Path,
    *,
    expected_receipt_sha256: str,
    response: Mapping[str, Any],
    submitted_at: str,
) -> dict[str, Any]:
    """Atomically append one complete reviewer/partition model response."""

    root = Path(framework_root).resolve()
    receipt = _load_anchored_receipt(
        root, expected_receipt_sha256=expected_receipt_sha256
    )
    _verify_root_binding(root, receipt)
    allocation_path = root / "sealed_controls" / "allocation_manifest.json"
    if sha256_file(allocation_path) != receipt["allocation_manifest_sha256"]:
        raise AdjudicationFrameworkError("allocation manifest hash mismatch")
    allocation = _read_json(allocation_path)
    reviewer_id = response.get("reviewer_id")
    partition = response.get("partition")
    expected_ids = {
        row["candidate_id"]
        for row in allocation["assignments"]
        if row["reviewer_id"] == reviewer_id and row["partition"] == partition
    }
    schema = batch_output_schema(
        reviewer_id=str(reviewer_id),
        partition=str(partition),
        expected_count=len(expected_ids),
    )
    validate_instance(response, schema)
    labels_payload = response["labels"]
    response_ids = [row["candidate_id"] for row in labels_payload]
    if len(response_ids) != len(set(response_ids)) or set(response_ids) != expected_ids:
        raise AdjudicationFrameworkError("label response does not exactly cover its assignment")

    labels: list[dict[str, Any]] = []
    for row in labels_payload:
        if len(row["reason_codes"]) != len(set(row["reason_codes"])):
            raise AdjudicationFrameworkError("reason codes must be unique")
        payload = {
            "schema_id": LABEL_SCHEMA_ID,
            "candidate_id": row["candidate_id"],
            "reviewer_id": reviewer_id,
            "reviewer_type": "MODEL_ASSISTED",
            "partition": partition,
            "outcome": row["outcome"],
            "reason_codes": row["reason_codes"],
            "rationale": row["rationale"],
            "submitted_at": submitted_at,
            "prior_review_visible": False,
            "source_scope": "restricted-pair-evidence-only",
        }
        label = {
            "label_id": "label:" + sha256_bytes(canonical_json_bytes(payload)),
            **payload,
        }
        validate_instance(label, label_schema())
        assert_public_safe(label)
        labels.append(label)
    labels.sort(key=lambda row: row["candidate_id"])

    label_root = root / "labels"
    label_path = label_root / f"{reviewer_id}.ndjson"
    with _append_lock(label_root):
        existing = _read_ndjson(label_path) if label_path.exists() else []
        existing_ids = {row.get("candidate_id") for row in existing}
        if existing_ids & expected_ids:
            raise AdjudicationFrameworkError("reviewer partition was already appended")
        descriptor = os.open(label_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, b"".join(canonical_json_bytes(row) for row in labels))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    result = {
        "status": "PASS",
        "reviewer_id": reviewer_id,
        "partition": partition,
        "labels_added": len(labels),
        "label_file_sha256": sha256_file(label_path),
    }
    if partition != "HOLDOUT":
        result["outcome_counts"] = dict(sorted(
            Counter(row["outcome"] for row in labels).items()
        ))
    return result


def _validated_labels(
    root: Path,
    receipt: Mapping[str, Any],
    allocation: Mapping[str, Any],
    partition_by_candidate: Mapping[str, str],
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, Any]]]:
    by_reviewer: dict[str, dict[str, dict[str, Any]]] = {}
    files: list[dict[str, Any]] = []
    for reviewer_id in REVIEWERS:
        path = root / "labels" / f"{reviewer_id}.ndjson"
        rows = _read_ndjson(path)
        expected = {
            row["candidate_id"]
            for row in allocation["assignments"]
            if row["reviewer_id"] == reviewer_id
        }
        if len(rows) != len(expected):
            raise AdjudicationFrameworkError("reviewer label count is incomplete")
        index: dict[str, dict[str, Any]] = {}
        for row in rows:
            validate_instance(row, label_schema())
            assert_public_safe(row)
            payload = dict(row)
            label_id = payload.pop("label_id")
            if label_id != "label:" + sha256_bytes(canonical_json_bytes(payload)):
                raise AdjudicationFrameworkError("label ID does not match its payload")
            candidate_id = row["candidate_id"]
            if candidate_id in index or candidate_id not in expected:
                raise AdjudicationFrameworkError("label assignment is duplicated or foreign")
            if row["reviewer_id"] != reviewer_id or row["prior_review_visible"] is not False:
                raise AdjudicationFrameworkError("label reviewer separation is invalid")
            if row["partition"] != partition_by_candidate.get(candidate_id):
                raise AdjudicationFrameworkError(
                    "label partition does not match the sealed split"
                )
            index[candidate_id] = row
        by_reviewer[reviewer_id] = index
        files.append({
            "reviewer_id": reviewer_id,
            "records": len(rows),
            "sha256": sha256_file(path),
        })
    if set(by_reviewer[REVIEWERS[0]]) != set(by_reviewer[REVIEWERS[1]]):
        raise AdjudicationFrameworkError("reviewers labeled different candidate universes")
    return by_reviewer, files


def build_post_label_receipt(
    framework_root: str | Path,
    results_root: str | Path,
    *,
    expected_receipt_sha256: str,
    generated_at: str,
    synced_roots: Iterable[str | Path],
) -> dict[str, Any]:
    """Seal aggregate calibration metrics while keeping holdout outcomes private."""

    root = Path(framework_root).resolve()
    output = Path(results_root).resolve()
    _require_restricted_output(output, (Path(path) for path in synced_roots))
    if output.exists():
        raise AdjudicationFrameworkError(f"results output already exists: {output}")
    framework_receipt = _load_anchored_receipt(
        root, expected_receipt_sha256=expected_receipt_sha256
    )
    _verify_root_binding(root, framework_receipt)
    allocation_path = root / "sealed_controls" / "allocation_manifest.json"
    if sha256_file(allocation_path) != framework_receipt["allocation_manifest_sha256"]:
        raise AdjudicationFrameworkError("allocation manifest hash mismatch")
    allocation = _read_json(allocation_path)
    split_path = root / "sealed_controls" / "split_seal.json"
    if sha256_file(split_path) != framework_receipt["split_seal_sha256"]:
        raise AdjudicationFrameworkError("split seal hash mismatch")
    split = _read_json(split_path)
    partition_by_candidate = split["partition_by_candidate"]
    allocated_candidates = {
        row["candidate_id"] for row in allocation["assignments"]
    }
    if set(partition_by_candidate) != allocated_candidates:
        raise AdjudicationFrameworkError(
            "sealed split and allocation cover different candidates"
        )
    labels, label_files = _validated_labels(
        root, framework_receipt, allocation, partition_by_candidate
    )
    calibration_ids = sorted(
        candidate_id for candidate_id, partition in partition_by_candidate.items()
        if partition == "CALIBRATION"
    )
    holdout_ids = sorted(
        candidate_id for candidate_id, partition in partition_by_candidate.items()
        if partition == "HOLDOUT"
    )
    left = labels[REVIEWERS[0]]
    right = labels[REVIEWERS[1]]
    reviewer_outcomes = {}
    for reviewer in REVIEWERS:
        counts = Counter(
            labels[reviewer][candidate_id]["outcome"] for candidate_id in calibration_ids
        )
        reviewer_outcomes[reviewer] = {outcome: counts[outcome] for outcome in OUTCOMES}
    exact_agreement = sum(
        left[candidate_id]["outcome"] == right[candidate_id]["outcome"]
        for candidate_id in calibration_ids
    )
    determinate_ids = [
        candidate_id for candidate_id in calibration_ids
        if left[candidate_id]["outcome"] in {"MATCH", "DISTINCT"}
        and right[candidate_id]["outcome"] in {"MATCH", "DISTINCT"}
    ]
    determinate_agreement = sum(
        left[candidate_id]["outcome"] == right[candidate_id]["outcome"]
        for candidate_id in determinate_ids
    )
    consensus_match = sum(
        left[candidate_id]["outcome"] == right[candidate_id]["outcome"] == "MATCH"
        for candidate_id in calibration_ids
    )
    consensus_distinct = sum(
        left[candidate_id]["outcome"] == right[candidate_id]["outcome"] == "DISTINCT"
        for candidate_id in calibration_ids
    )
    conflicting_determinate = sum(
        {left[candidate_id]["outcome"], right[candidate_id]["outcome"]}
        == {"MATCH", "DISTINCT"}
        for candidate_id in calibration_ids
    )
    disagreement_rows = [
        {
            "candidate_id": candidate_id,
            "reviewer_a_outcome": left[candidate_id]["outcome"],
            "reviewer_b_outcome": right[candidate_id]["outcome"],
            "partition": "CALIBRATION",
        }
        for candidate_id in calibration_ids
        if left[candidate_id]["outcome"] != right[candidate_id]["outcome"]
    ]

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        disagreement_path = staging / "calibration_disagreements.ndjson"
        atomic_write_bytes(
            disagreement_path,
            b"".join(canonical_json_bytes(row) for row in disagreement_rows),
        )
        determinate_consensus = consensus_match + consensus_distinct
        receipt = {
            "schema_id": RESULTS_SCHEMA_ID,
            "generated_at": generated_at,
            "status": "REPORT",
            "method_version": RUN_METHOD_VERSION,
            "framework_receipt_sha256": expected_receipt_sha256,
            "root_binding_sha256": framework_receipt["root_binding_sha256"],
            "split_seal_sha256": framework_receipt["split_seal_sha256"],
            "review_sample_sha256": framework_receipt["review_sample_sha256"],
            "reviewer_ids": list(REVIEWERS),
            "reviewer_type": "MODEL_ASSISTED",
            "label_files": sorted(label_files, key=lambda row: row["reviewer_id"]),
            "submitted_label_count": sum(row["records"] for row in label_files),
            "reviewed_candidate_count": len(partition_by_candidate),
            "calibration": {
                "candidate_count": len(calibration_ids),
                "reviewer_outcome_counts": reviewer_outcomes,
                "exact_agreement_count": exact_agreement,
                "exact_agreement_rate": exact_agreement / len(calibration_ids),
                "determinate_pair_count": len(determinate_ids),
                "determinate_agreement_count": determinate_agreement,
                "determinate_agreement_rate": (
                    determinate_agreement / len(determinate_ids) if determinate_ids else None
                ),
                "consensus_match_count": consensus_match,
                "consensus_distinct_count": consensus_distinct,
                "consensus_match_rate": (
                    consensus_match / determinate_consensus
                    if determinate_consensus else None
                ),
                "conflicting_determinate_count": conflicting_determinate,
                "possible_or_abstain_pair_count": len(calibration_ids) - len(determinate_ids),
                "disagreement_queue_count": len(disagreement_rows),
                "disagreement_queue_sha256": sha256_file(disagreement_path),
            },
            "holdout": {
                "candidate_count": len(holdout_ids),
                "reviewer_label_counts": {
                    reviewer: sum(candidate_id in labels[reviewer] for candidate_id in holdout_ids)
                    for reviewer in REVIEWERS
                },
                "status": "QUARANTINED",
                "outcome_counts_published": False,
                "evaluation_count": 0,
            },
            "human_gold_adjudicated_count": 0,
            "estimated_human_gold_precision": None,
            "model_assisted_only": True,
            "source_enabled": False,
            "canonical_entity_merges": 0,
            "auto_merge_enabled": False,
            "failures": [],
        }
        assert_public_safe(receipt)
        atomic_write_bytes(
            staging / "adjudication_results_receipt.json", canonical_json_bytes(receipt)
        )
        os.replace(staging, output)
        return receipt
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

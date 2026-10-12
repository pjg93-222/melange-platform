# -*- coding: utf-8 -*-
"""Fail-closed, restricted-local adjudication framework for the Politico pilot.

This module does not adjudicate identities and cannot authorize a canonical
merge.  It converts the sealed strict-candidate sample into deterministic,
source-position-randomized reviewer packets, seals a calibration/holdout split
before any labels exist, and provides a single-writer append-only label API.
Record-level artifacts must remain outside synced/project storage.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover
    from fec.lane_file_io import atomic_write_bytes

from .schema_validation import validate_instance
from .publication import _validate_record_semantics, schema_path
from .source_manifest import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    validate_source_manifest,
)
from .validate import assert_public_safe


FRAMEWORK_SCHEMA_ID = "staffer-network-politico-adjudication-framework-receipt-v1"
LABEL_SCHEMA_ID = "staffer-network-politico-adjudication-label-v1"
METHOD_VERSION = "politico-strict-dual-review-blinded-v2"
INSTRUCTIONS_VERSION = "politico-pair-adjudication-v1"
REVIEWERS = ("codex-evidence-review", "fable-independent-review")
PARTITIONS = ("CALIBRATION", "HOLDOUT")
OUTCOMES = ("MATCH", "DISTINCT", "POSSIBLE", "ABSTAIN")
REASON_CODES = (
    "NAME_COMPATIBLE",
    "TITLE_COMPATIBLE",
    "OFFICE_COMPATIBLE",
    "CONTAINER_MISMATCH",
    "TEMPORAL_EVIDENCE_MISSING",
    "IDENTIFIER_CONFLICT",
    "INSUFFICIENT_EVIDENCE",
    "OTHER",
)
EXCLUDED_MACHINE_FIELDS = {
    "disposition",
    "title_match",
    "office_evidence_available",
    "office_match",
    "review_status",
}
REVIEW_PACKET_FIELDS = {
    "schema_id",
    "candidate_id",
    "reviewer_id",
    "partition",
    "record_a",
    "record_b",
    "allowed_outcomes",
    "instructions_version",
}
REVIEW_RECORD_FIELDS = {
    "record_id",
    "display_name",
    "role_context",
    "office_context",
}
_CANDIDATE_ID = re.compile(r"^candidate:[0-9a-f]{64}$")


class AdjudicationFrameworkError(RuntimeError):
    """Raised when an A1 invariant would be violated."""


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AdjudicationFrameworkError(f"{path.name} must contain one object")
    return value


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise AdjudicationFrameworkError(
                    f"{path.name}:{line_number}: blank line"
                )
            value = json.loads(line)
            if not isinstance(value, dict):
                raise AdjudicationFrameworkError(
                    f"{path.name}:{line_number}: non-object"
                )
            values.append(value)
    return values


def _write_ndjson(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    atomic_write_bytes(path, b"".join(canonical_json_bytes(row) for row in records))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _require_restricted_output(output: Path, synced_roots: Iterable[Path]) -> None:
    resolved = output.resolve()
    for root in synced_roots:
        if _is_within(resolved, Path(root).resolve()):
            raise AdjudicationFrameworkError(
                "record-level adjudication output cannot be inside a synced root"
            )


def _path_fingerprint(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve()))
    return sha256_bytes(normalized.encode("utf-8"))


def _load_anchored_receipt(
    root: Path, *, expected_receipt_sha256: str
) -> dict[str, Any]:
    receipt_path = root / "framework_receipt.json"
    if not re.fullmatch(r"[0-9a-f]{64}", expected_receipt_sha256):
        raise AdjudicationFrameworkError("expected receipt SHA-256 is invalid")
    if not receipt_path.is_file() or sha256_file(receipt_path) != expected_receipt_sha256:
        raise AdjudicationFrameworkError("framework receipt differs from external anchor")
    receipt = _read_json(receipt_path)
    try:
        schema = _read_json(schema_path(
            Path(__file__).resolve().parent / "schemas", FRAMEWORK_SCHEMA_ID
        ))
        validate_instance(receipt, schema)
        _validate_record_semantics(receipt, FRAMEWORK_SCHEMA_ID)
    except (ValueError, RuntimeError) as exc:
        raise AdjudicationFrameworkError("anchored framework receipt is invalid") from exc
    return receipt


def _verify_root_binding(root: Path, receipt: Mapping[str, Any]) -> None:
    binding_path = root / "sealed_controls" / "root_binding.json"
    if (
        not binding_path.is_file()
        or sha256_file(binding_path) != receipt.get("root_binding_sha256")
    ):
        raise AdjudicationFrameworkError("root binding hash mismatch")
    binding = _read_json(binding_path)
    if (
        binding.get("schema_id")
        != "staffer-network-politico-adjudication-root-binding-v1"
        or binding.get("normalization") != "path-resolve-normcase-v1"
        or binding.get("sealed_before_label_count") != 0
        or binding.get("resolved_root_sha256") != _path_fingerprint(root)
    ):
        raise AdjudicationFrameworkError("framework moved from its sealed restricted root")


def _rank(seed: str, *parts: str) -> str:
    return hashlib.sha256("|".join((seed, *parts)).encode("utf-8")).hexdigest()


def _record_id(value: str) -> str:
    return "record:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _records_for_candidate(candidate: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    politico_roles = candidate.get("politico_roles")
    if not isinstance(politico_roles, list) or not politico_roles or not all(
        isinstance(role, str) and role.strip() for role in politico_roles
    ):
        raise AdjudicationFrameworkError("candidate politico_roles are invalid")
    legacy_title = candidate.get("legacy_title")
    legacy_member = candidate.get("legacy_member_name")
    if not isinstance(legacy_title, str) or not legacy_title.strip():
        raise AdjudicationFrameworkError("candidate legacy_title is invalid")
    if not isinstance(legacy_member, str) or not legacy_member.strip():
        raise AdjudicationFrameworkError("candidate legacy_member_name is invalid")

    politico = {
        "record_id": _record_id("politico|" + str(candidate["source_profile_uuid"])),
        "display_name": candidate["politico_name"],
        "role_context": politico_roles,
        "office_context": [
            role for role in politico_roles
            if any(marker in role.casefold() for marker in (
                "office of", "committee on", "house", "senate", "caucus"
            ))
        ],
    }
    legacy = {
        "record_id": _record_id("legacy|" + str(candidate["legacy_staff_id"])),
        "display_name": candidate["legacy_name"],
        "role_context": [legacy_title],
        "office_context": [legacy_member],
    }
    for record in (politico, legacy):
        if set(record) != REVIEW_RECORD_FIELDS:
            raise AdjudicationFrameworkError("blinded record shape mismatch")
        assert_public_safe(record)
    return politico, legacy


def _packet(
    candidate: Mapping[str, Any],
    *,
    reviewer_id: str,
    partition: str,
    seed: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    politico, legacy = _records_for_candidate(candidate)
    politico_first = int(
        _rank(seed, reviewer_id, str(candidate["candidate_id"]), "orientation"), 16
    ) % 2 == 0
    record_a, record_b = (politico, legacy) if politico_first else (legacy, politico)
    packet = {
        "schema_id": "staffer-network-politico-adjudication-packet-v1",
        "candidate_id": candidate["candidate_id"],
        "reviewer_id": reviewer_id,
        "partition": partition,
        "record_a": record_a,
        "record_b": record_b,
        "allowed_outcomes": list(OUTCOMES),
        "instructions_version": INSTRUCTIONS_VERSION,
    }
    if set(packet) != REVIEW_PACKET_FIELDS:
        raise AdjudicationFrameworkError("review packet shape mismatch")
    if EXCLUDED_MACHINE_FIELDS & set(packet):
        raise AdjudicationFrameworkError("review packet exposes machine fields")
    assert_public_safe(packet)
    crosswalk = {
        "candidate_id": candidate["candidate_id"],
        "reviewer_id": reviewer_id,
        "partition": partition,
        "record_a_source": "POLITICO" if politico_first else "LEGACY",
        "record_b_source": "LEGACY" if politico_first else "POLITICO",
        "machine_disposition": candidate["disposition"],
        "machine_title_match": candidate["title_match"],
        "machine_office_evidence_available": candidate["office_evidence_available"],
        "machine_office_match": candidate["office_match"],
    }
    return packet, crosswalk


def label_schema() -> dict[str, Any]:
    """Return the exact label-record schema written beside restricted packets."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:melange:staffer-network:politico-adjudication-label:v1",
        "title": "Restricted-local independent identity-adjudication label",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_id", "label_id", "candidate_id", "reviewer_id",
            "reviewer_type", "partition", "outcome", "reason_codes",
            "rationale", "submitted_at", "prior_review_visible", "source_scope",
        ],
        "properties": {
            "schema_id": {"const": LABEL_SCHEMA_ID},
            "label_id": {"type": "string", "pattern": r"^label:[0-9a-f]{64}$"},
            "candidate_id": {"type": "string", "pattern": r"^candidate:[0-9a-f]{64}$"},
            "reviewer_id": {"enum": list(REVIEWERS)},
            "reviewer_type": {"const": "MODEL_ASSISTED"},
            "partition": {"enum": list(PARTITIONS)},
            "outcome": {"enum": list(OUTCOMES)},
            "reason_codes": {
                "type": "array", "minItems": 1, "uniqueItems": True,
                "items": {"enum": list(REASON_CODES)},
            },
            "rationale": {"type": "string", "minLength": 1, "maxLength": 2000},
            "submitted_at": {"type": "string", "format": "date-time"},
            "prior_review_visible": {"const": False},
            "source_scope": {"const": "restricted-pair-evidence-only"},
        },
    }


def build_adjudication_framework(
    pilot_root: str | Path,
    source_manifest_path: str | Path,
    output_root: str | Path,
    *,
    allocation_seed: str,
    generated_at: str,
    synced_roots: Iterable[str | Path],
    calibration_fraction: float = 0.5,
) -> dict[str, Any]:
    """Create an immutable A1 framework and return its public-safe receipt."""

    pilot = Path(pilot_root).resolve()
    output = Path(output_root).resolve()
    source_manifest_file = Path(source_manifest_path).resolve()
    _require_restricted_output(output, (Path(root) for root in synced_roots))
    if output.exists():
        raise AdjudicationFrameworkError(f"output already exists: {output}")
    if not allocation_seed.strip():
        raise AdjudicationFrameworkError("allocation_seed must be nonempty")
    if not 0.0 < calibration_fraction < 1.0:
        raise AdjudicationFrameworkError("calibration_fraction must be in (0,1)")

    pilot_receipt_path = pilot / "resolution_pilot_receipt.json"
    sample_path = pilot / "strict_candidate_review_sample.ndjson"
    if not pilot_receipt_path.is_file() or not sample_path.is_file():
        raise AdjudicationFrameworkError("pilot root is incomplete")
    pilot_receipt = _read_json(pilot_receipt_path)
    if (
        pilot_receipt.get("schema_id") != "politico-directory-resolution-pilot-receipt-v1"
        or pilot_receipt.get("status") != "REPORT"
        or pilot_receipt.get("adjudicated_sample_count") != 0
        or pilot_receipt.get("estimated_precision") is not None
        or pilot_receipt.get("canonical_entity_merges") != 0
        or pilot_receipt.get("auto_merge_enabled") is not False
        or pilot_receipt.get("adjudication_required") is not True
        or pilot_receipt.get("failures") != []
    ):
        raise AdjudicationFrameworkError("pilot receipt violates the pre-label gate")
    if sha256_file(sample_path) != pilot_receipt.get("review_sample_sha256"):
        raise AdjudicationFrameworkError("review sample hash differs from pilot receipt")

    manifest = _read_json(source_manifest_file)
    validate_source_manifest(manifest)
    if (
        manifest.get("source_system_id") != "politico-pro-staffer-directory"
        or manifest.get("authorization_status") != "AUTHORIZED"
        or not isinstance(manifest.get("enabled"), bool)
        or manifest.get("redistribution_class") == "prohibited"
    ):
        raise AdjudicationFrameworkError(
            "Politico source must be authorized and non-prohibited"
        )

    sample = _read_ndjson(sample_path)
    if len(sample) != pilot_receipt.get("review_sample_count") or len(sample) < 2:
        raise AdjudicationFrameworkError("review sample count is invalid")
    candidate_ids: list[str] = []
    for row in sample:
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise AdjudicationFrameworkError("sample candidate_id is invalid")
        if row.get("disposition") != "STRICT_CANDIDATE_NAME_TITLE_OFFICE":
            raise AdjudicationFrameworkError("strict review sample contains another stratum")
        if not EXCLUDED_MACHINE_FIELDS <= set(row):
            raise AdjudicationFrameworkError("sample lacks required machine audit fields")
        candidate_ids.append(candidate_id)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise AdjudicationFrameworkError("review sample candidate IDs are not unique")

    ranked = sorted(sample, key=lambda row: _rank(allocation_seed, row["candidate_id"], "split"))
    calibration_count = round(len(ranked) * calibration_fraction)
    calibration_count = max(1, min(len(ranked) - 1, calibration_count))
    partition_by_candidate = {
        row["candidate_id"]: (
            "CALIBRATION" if index < calibration_count else "HOLDOUT"
        )
        for index, row in enumerate(ranked)
    }
    split_seal = {
        "schema_id": "staffer-network-politico-adjudication-split-seal-v1",
        "review_sample_sha256": pilot_receipt["review_sample_sha256"],
        "allocation_seed": allocation_seed,
        "allocation_seed_sha256": sha256_bytes(allocation_seed.encode("utf-8")),
        "calibration_fraction": calibration_fraction,
        "calibration_count": calibration_count,
        "holdout_count": len(ranked) - calibration_count,
        "partition_by_candidate": dict(sorted(partition_by_candidate.items())),
        "sealed_before_label_count": 0,
    }

    staging_parent = output.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=staging_parent))
    try:
        packet_root = staging / "reviewer_packets"
        control_root = staging / "sealed_controls"
        label_root = staging / "labels"
        packet_root.mkdir()
        control_root.mkdir()
        label_root.mkdir()
        root_binding = {
            "schema_id": "staffer-network-politico-adjudication-root-binding-v1",
            "normalization": "path-resolve-normcase-v1",
            "resolved_root_sha256": _path_fingerprint(output),
            "sealed_before_label_count": 0,
        }
        atomic_write_bytes(
            control_root / "root_binding.json", canonical_json_bytes(root_binding)
        )
        atomic_write_bytes(
            control_root / "split_seal.json", canonical_json_bytes(split_seal)
        )

        packet_files: list[dict[str, Any]] = []
        crosswalk_rows: list[dict[str, Any]] = []
        assignment_rows: list[dict[str, Any]] = []
        sample_by_id = {row["candidate_id"]: row for row in sample}
        for reviewer_id in REVIEWERS:
            for partition in PARTITIONS:
                selected_ids = [
                    candidate_id for candidate_id, assigned in partition_by_candidate.items()
                    if assigned == partition
                ]
                selected_ids.sort(
                    key=lambda candidate_id: _rank(
                        allocation_seed, reviewer_id, partition, candidate_id, "packet-order"
                    )
                )
                packets: list[dict[str, Any]] = []
                for candidate_id in selected_ids:
                    packet, crosswalk = _packet(
                        sample_by_id[candidate_id], reviewer_id=reviewer_id,
                        partition=partition, seed=allocation_seed,
                    )
                    packets.append(packet)
                    crosswalk_rows.append(crosswalk)
                    assignment_rows.append({
                        "candidate_id": candidate_id,
                        "reviewer_id": reviewer_id,
                        "partition": partition,
                    })
                filename = f"{reviewer_id}__{partition.casefold()}.ndjson"
                path = packet_root / filename
                _write_ndjson(path, packets)
                packet_files.append({
                    "reviewer_id": reviewer_id,
                    "partition": partition,
                    "records": len(packets),
                    "sha256": sha256_file(path),
                })

        crosswalk_rows.sort(
            key=lambda row: (row["candidate_id"], row["reviewer_id"])
        )
        assignment_rows.sort(
            key=lambda row: (row["candidate_id"], row["reviewer_id"])
        )
        _write_ndjson(control_root / "machine_crosswalk.ndjson", crosswalk_rows)
        allocation_manifest = {
            "schema_id": "staffer-network-politico-adjudication-allocation-v1",
            "review_sample_sha256": pilot_receipt["review_sample_sha256"],
            "split_seal_sha256": sha256_file(control_root / "split_seal.json"),
            "reviewer_ids": list(REVIEWERS),
            "assignments": assignment_rows,
            "labels_visible_across_reviewers": False,
            "holdout_permitted_for_rule_revision": False,
        }
        atomic_write_bytes(
            control_root / "allocation_manifest.json",
            canonical_json_bytes(allocation_manifest),
        )
        schema = label_schema()
        atomic_write_bytes(
            control_root / "label_record_schema.json", canonical_json_bytes(schema)
        )
        label_store_policy = {
            "schema_id": "staffer-network-politico-label-store-policy-v1",
            "mode": "append-only-single-writer",
            "initial_label_count": 0,
            "reviewers": list(REVIEWERS),
            "prior_review_visibility_required": False,
            "record_level_storage": "restricted-local-only",
        }
        atomic_write_bytes(
            label_root / "label_store_policy.json",
            canonical_json_bytes(label_store_policy),
        )

        receipt = {
            "schema_id": FRAMEWORK_SCHEMA_ID,
            "generated_at": generated_at,
            "status": "REPORT",
            "method_version": METHOD_VERSION,
            "source_manifest_sha256": manifest["manifest_sha256"],
            "source_authorization_status": manifest["authorization_status"],
            "source_enabled": manifest["enabled"],
            "source_canonical_use_enabled": False,
            "resolution_pilot_receipt_sha256": sha256_file(pilot_receipt_path),
            "review_sample_sha256": pilot_receipt["review_sample_sha256"],
            "review_sample_count": len(sample),
            "allocation_seed_sha256": split_seal["allocation_seed_sha256"],
            "root_binding_sha256": sha256_file(control_root / "root_binding.json"),
            "split_seal_sha256": sha256_file(control_root / "split_seal.json"),
            "calibration_count": calibration_count,
            "holdout_count": len(sample) - calibration_count,
            "reviewer_ids": list(REVIEWERS),
            "review_assignments": len(assignment_rows),
            "review_packet_fields": sorted(REVIEW_PACKET_FIELDS),
            "excluded_machine_fields": sorted(EXCLUDED_MACHINE_FIELDS),
            "packet_files": sorted(
                packet_files, key=lambda row: (row["reviewer_id"], row["partition"])
            ),
            "machine_crosswalk_sha256": sha256_file(
                control_root / "machine_crosswalk.ndjson"
            ),
            "allocation_manifest_sha256": sha256_file(
                control_root / "allocation_manifest.json"
            ),
            "label_record_schema_sha256": sha256_file(
                control_root / "label_record_schema.json"
            ),
            "submitted_label_count": 0,
            "adjudicated_candidate_count": 0,
            "estimated_precision": None,
            "legacy_row_collision_count": pilot_receipt["legacy_outcomes"][
                "REVIEW_NAME_COLLISION"
            ],
            "politico_profile_collision_count": pilot_receipt["dispositions"][
                "REVIEW_NAME_COLLISION"
            ],
            "canonical_entity_merges": 0,
            "auto_merge_enabled": False,
            "failures": [],
        }
        assert_public_safe(receipt)
        atomic_write_bytes(
            staging / "framework_receipt.json", canonical_json_bytes(receipt)
        )
        os.replace(staging, output)
        return receipt
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


@contextmanager
def _append_lock(label_root: Path):
    lock_path = label_root / ".append.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
    except FileExistsError as exc:
        raise AdjudicationFrameworkError("label store is already locked") from exc
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def append_label(
    framework_root: str | Path,
    *,
    expected_receipt_sha256: str,
    candidate_id: str,
    reviewer_id: str,
    partition: str,
    outcome: str,
    reason_codes: list[str],
    rationale: str,
    submitted_at: str,
    prior_review_visible: bool = False,
) -> dict[str, Any]:
    """Append one independent label, refusing duplicates and cross-review leakage."""

    root = Path(framework_root).resolve()
    receipt = _load_anchored_receipt(
        root, expected_receipt_sha256=expected_receipt_sha256
    )
    _verify_root_binding(root, receipt)
    allocation_path = root / "sealed_controls" / "allocation_manifest.json"
    if (
        not allocation_path.is_file()
        or sha256_file(allocation_path) != receipt["allocation_manifest_sha256"]
    ):
        raise AdjudicationFrameworkError("allocation manifest hash mismatch")
    allocation = _read_json(allocation_path)
    assigned = {
        (row["candidate_id"], row["reviewer_id"], row["partition"])
        for row in allocation.get("assignments", [])
    }
    if (candidate_id, reviewer_id, partition) not in assigned:
        raise AdjudicationFrameworkError("label is not in the sealed assignment")
    if prior_review_visible:
        raise AdjudicationFrameworkError("independent review cannot see the prior review")
    if receipt.get("canonical_entity_merges") != 0 or receipt.get("auto_merge_enabled") is not False:
        raise AdjudicationFrameworkError("framework no-merge gate is not intact")

    payload = {
        "schema_id": LABEL_SCHEMA_ID,
        "candidate_id": candidate_id,
        "reviewer_id": reviewer_id,
        "reviewer_type": "MODEL_ASSISTED",
        "partition": partition,
        "outcome": outcome,
        "reason_codes": reason_codes,
        "rationale": rationale,
        "submitted_at": submitted_at,
        "prior_review_visible": False,
        "source_scope": "restricted-pair-evidence-only",
    }
    label_id = "label:" + sha256_bytes(canonical_json_bytes(payload))
    label = {"label_id": label_id, **payload}
    validate_instance(label, label_schema())
    assert_public_safe(label)

    label_root = root / "labels"
    label_root.mkdir(exist_ok=True)
    label_path = label_root / f"{reviewer_id}.ndjson"
    with _append_lock(label_root):
        prior = _read_ndjson(label_path) if label_path.exists() else []
        if any(row.get("candidate_id") == candidate_id for row in prior):
            raise AdjudicationFrameworkError(
                "reviewer already labeled this candidate"
            )
        descriptor = os.open(
            label_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600
        )
        try:
            os.write(descriptor, canonical_json_bytes(label))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return label


def verify_adjudication_framework(
    framework_root: str | Path, *, expected_receipt_sha256: str
) -> dict[str, Any]:
    """Verify a framework's sealed files without returning record-level data."""

    root = Path(framework_root).resolve()
    receipt = _load_anchored_receipt(
        root, expected_receipt_sha256=expected_receipt_sha256
    )
    _verify_root_binding(root, receipt)
    control_root = root / "sealed_controls"
    split_path = control_root / "split_seal.json"
    allocation_path = control_root / "allocation_manifest.json"
    crosswalk_path = control_root / "machine_crosswalk.ndjson"
    label_schema_path = control_root / "label_record_schema.json"
    expected_hashes = {
        control_root / "root_binding.json": receipt["root_binding_sha256"],
        split_path: receipt["split_seal_sha256"],
        allocation_path: receipt["allocation_manifest_sha256"],
        crosswalk_path: receipt["machine_crosswalk_sha256"],
        label_schema_path: receipt["label_record_schema_sha256"],
    }
    for path, expected in expected_hashes.items():
        if not path.is_file() or sha256_file(path) != expected:
            raise AdjudicationFrameworkError(f"sealed framework hash mismatch: {path.name}")
    if _read_json(label_schema_path) != label_schema():
        raise AdjudicationFrameworkError("label schema content drifted")

    split = _read_json(split_path)
    seed = split.get("allocation_seed")
    if not isinstance(seed, str) or sha256_bytes(seed.encode("utf-8")) != receipt[
        "allocation_seed_sha256"
    ]:
        raise AdjudicationFrameworkError("allocation seed does not match its seal")
    partition_by_candidate = split.get("partition_by_candidate")
    if not isinstance(partition_by_candidate, dict):
        raise AdjudicationFrameworkError("split seal lacks candidate allocation")
    split_counts = {
        partition: sum(value == partition for value in partition_by_candidate.values())
        for partition in PARTITIONS
    }
    if (
        split_counts["CALIBRATION"] != receipt["calibration_count"]
        or split_counts["HOLDOUT"] != receipt["holdout_count"]
        or len(partition_by_candidate) != receipt["review_sample_count"]
        or split.get("sealed_before_label_count") != 0
    ):
        raise AdjudicationFrameworkError("split seal counts are inconsistent")

    allocation = _read_json(allocation_path)
    assignments = allocation.get("assignments")
    if not isinstance(assignments, list) or len(assignments) != receipt[
        "review_assignments"
    ]:
        raise AdjudicationFrameworkError("allocation assignment count is inconsistent")
    assigned = {
        (row.get("candidate_id"), row.get("reviewer_id"), row.get("partition"))
        for row in assignments if isinstance(row, dict)
    }
    if len(assigned) != receipt["review_assignments"]:
        raise AdjudicationFrameworkError("allocation contains duplicate assignments")

    seen = {reviewer: set() for reviewer in REVIEWERS}
    verified_records = 0
    for entry in receipt["packet_files"]:
        reviewer_id = entry["reviewer_id"]
        partition = entry["partition"]
        path = root / "reviewer_packets" / (
            f"{reviewer_id}__{partition.casefold()}.ndjson"
        )
        if not path.is_file() or sha256_file(path) != entry["sha256"]:
            raise AdjudicationFrameworkError("review packet hash mismatch")
        rows = _read_ndjson(path)
        if len(rows) != entry["records"]:
            raise AdjudicationFrameworkError("review packet count mismatch")
        for row in rows:
            if set(row) != REVIEW_PACKET_FIELDS or EXCLUDED_MACHINE_FIELDS & set(row):
                raise AdjudicationFrameworkError("review packet is not blinded")
            if row["reviewer_id"] != reviewer_id or row["partition"] != partition:
                raise AdjudicationFrameworkError("review packet assignment drifted")
            key = (row["candidate_id"], reviewer_id, partition)
            if key not in assigned:
                raise AdjudicationFrameworkError("review packet row is unassigned")
            assert_public_safe(row)
            seen[reviewer_id].add(row["candidate_id"])
            verified_records += 1
    expected_ids = set(partition_by_candidate)
    if any(candidate_ids != expected_ids for candidate_ids in seen.values()):
        raise AdjudicationFrameworkError("reviewer coverage differs from sealed sample")
    if verified_records != receipt["review_assignments"]:
        raise AdjudicationFrameworkError("verified review-record count is inconsistent")
    if receipt["submitted_label_count"] == 0 and list((root / "labels").glob("*.ndjson")):
        raise AdjudicationFrameworkError("pre-label framework contains label records")
    return {
        "status": "PASS",
        "review_sample_count": receipt["review_sample_count"],
        "calibration_count": receipt["calibration_count"],
        "holdout_count": receipt["holdout_count"],
        "reviewer_count": len(REVIEWERS),
        "verified_review_records": verified_records,
        "submitted_label_count": receipt["submitted_label_count"],
        "canonical_entity_merges": receipt["canonical_entity_merges"],
        "auto_merge_enabled": receipt["auto_merge_enabled"],
    }


def request_canonical_merge(_: Mapping[str, Any]) -> None:
    """A1 negative control: no input can authorize a canonical merge."""

    raise AdjudicationFrameworkError(
        "canonical merge prohibited: accepted I-4/Q-1 freeze packet is absent"
    )

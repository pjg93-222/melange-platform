#!/usr/bin/env python3
"""Materialize a lossless, proposal-only queue from an immutable prefill.

The queue is a reviewer work surface, not a decision ledger.  It cannot
ratify, include, exclude, score, sync, promote, or write production data.
Every selected query membership is conserved exactly once in the main queue.
Additional visibility files are overlapping views only; they never remove a
membership from the queue.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, TextIO

try:
    from lane_file_io import open_ndjson
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import open_ndjson


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREFILL_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "partial_review_prefill"
    / "current.json"
)
DEFAULT_OUT_ROOT = (
    ROOT / "audits" / "legfix5" / "partial_review_queue" / "snapshots"
)
DEFAULT_CURRENT = (
    ROOT
    / "audits"
    / "legfix5"
    / "partial_review_queue"
    / "current.json"
)
SHA256_CHARS = frozenset("0123456789abcdef")
RISK_LABELS = {
    1: "possible_false_positive_or_ambiguous",
    2: "possible_coverage_miss",
    3: "official_title_summary_evidence_missing",
    4: "official_title_or_summary_nexus",
    5: "unresolved_context",
}
AUTHORITY_FALSE_FIELDS = (
    "candidate_creation_performed",
    "candidate_exclusion_performed",
    "semantic_exclusion_performed",
    "ratified_decision_performed",
    "automatic_scoring_performed",
    "sync_or_promotion_performed",
    "production_write_performed",
    "membership_disposition_authority",
    "ratification_authority",
    "scoring_authority",
)


class ContractError(RuntimeError):
    """Raised when immutable inputs or losslessness constraints drift."""


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"Expected JSON object: {path}")
    return value


def iter_ndjson(path: Path) -> Iterable[dict[str, Any]]:
    with open_ndjson(path) as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ContractError(
                    f"Expected object at {path}:{line_number}"
                )
            yield value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_item(
    path: Path, *, record_count: int | None = None
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if record_count is not None:
        item["record_count"] = record_count
    return item


def require_file_item(item: dict[str, Any], label: str) -> Path:
    raw_path = item.get("path")
    expected = str(item.get("sha256") or "").casefold()
    if not isinstance(raw_path, str) or not raw_path:
        raise ContractError(f"{label} lacks a path")
    if len(expected) != 64 or set(expected) - SHA256_CHARS:
        raise ContractError(f"{label} lacks an exact SHA-256")
    path = Path(raw_path).resolve()
    if not path.is_file():
        raise ContractError(f"{label} is missing: {path}")
    observed = sha256_file(path)
    if observed != expected:
        raise ContractError(
            f"{label} SHA-256 mismatch: expected {expected}, got {observed}"
        )
    expected_bytes = item.get("bytes")
    if expected_bytes is not None and expected_bytes != path.stat().st_size:
        raise ContractError(f"{label} byte count drift")
    return path


def write_ndjson_row(handle: TextIO, row: dict[str, Any]) -> None:
    handle.write(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    )


def manifest_from_pointer(pointer_path: Path) -> Path:
    pointer = read_object(pointer_path.resolve())
    if not (
        pointer.get("schema")
        == "legislation-partial-review-prefill-pointer-v1"
        and pointer.get("review_prefill_only") is True
        and pointer.get("ratification_authority") is False
        and pointer.get("scoring_authority") is False
        and pointer.get("sync_or_promotion_performed") is False
    ):
        raise ContractError("Partial-review prefill pointer authority drift")
    path = require_file_item(
        pointer.get("manifest") or {}, "partial-review prefill manifest"
    )
    if (
        str(pointer.get("prefill_id") or "") != path.parent.name
        or Path(str(pointer.get("prefill_dir") or "")).resolve()
        != path.parent
    ):
        raise ContractError("Partial-review prefill pointer identity drift")
    return path


def load_prefill_manifest(
    manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Path]]:
    manifest_path = manifest_path.resolve()
    manifest = read_object(manifest_path)
    if not (
        manifest.get("schema")
        == "legislation-partial-review-prefill-manifest-v1"
        and manifest.get("status") == "PASS_NON_RATIFIED_PARTIAL"
        and manifest.get("prefill_id") == manifest_path.parent.name
        and manifest.get("partial_discovery_only") is True
        and manifest.get("stale_complete_manifest_authority_used") is False
        and manifest.get("all_selected_memberships_have_proposals") is True
        and manifest.get("review_prefill_only") is True
        and manifest.get("human_ratification_required") is True
        and manifest.get("absence_is_neutral") is True
        and manifest.get("acceptance_passed") is False
        and manifest.get("scoring_weight") == 0
        and all(manifest.get(field) is False for field in AUTHORITY_FALSE_FIELDS)
    ):
        raise ContractError("Partial-review prefill manifest authority drift")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ContractError("Partial-review prefill lacks outputs")
    required = (
        "selected_records",
        "selected_memberships",
        "membership_review_proposals",
        "unsupported_amendments",
    )
    if any(name not in outputs for name in required):
        raise ContractError("Partial-review prefill output inventory drift")
    paths: dict[str, Path] = {}
    for name, item in outputs.items():
        if not isinstance(item, dict):
            raise ContractError(f"Invalid prefill output item: {name}")
        paths[name] = require_file_item(item, f"prefill output {name}")
    membership_count = outputs["selected_memberships"].get("record_count")
    proposal_count = outputs["membership_review_proposals"].get(
        "record_count"
    )
    if not (
        membership_count
        == proposal_count
        == manifest.get("selected_membership_count")
        == manifest.get("proposal_count")
    ):
        raise ContractError("Prefill membership/proposal count drift")
    return manifest, paths


def membership_key(row: dict[str, Any]) -> tuple[str, str]:
    query_id = row.get("query_id")
    canonical_key = row.get("canonical_key")
    if not (
        isinstance(query_id, str)
        and query_id
        and isinstance(canonical_key, str)
        and canonical_key
    ):
        raise ContractError("Membership lacks query/candidate identity")
    return query_id, canonical_key


def proposal_key(row: dict[str, Any]) -> tuple[str, str]:
    query_id = row.get("raw_query_id")
    canonical_key = row.get("canonical_key")
    if not (
        isinstance(query_id, str)
        and query_id
        and isinstance(canonical_key, str)
        and canonical_key
    ):
        raise ContractError("Proposal lacks query/candidate identity")
    return query_id, canonical_key


def compact_record_projection(row: dict[str, Any]) -> dict[str, Any]:
    key = row.get("canonical_key")
    if not isinstance(key, str) or not key:
        raise ContractError("Selected record lacks canonical key")
    return {
        "canonical_key": key,
        "display_number": row.get("display_number"),
        "title": row.get("title") or row.get("purpose"),
        "canonical_url": row.get("canonical_url"),
        "record_kind": row.get("record_kind"),
        "selected_record_row_sha256": stable_json_sha256(row),
    }


def compact_membership_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_id": row.get("query_id"),
        "query_label": row.get("label"),
        "record_kind": row.get("record_kind"),
        "scoring_category": row.get("scoring_category"),
        "parent_category_id": row.get("parent_category_id"),
        "parent_category_label": row.get("parent_category_label"),
        "identifier_text": row.get("identifier_text"),
        "title_alias": row.get("title_alias"),
        "ambiguous_short_act_alias": row.get(
            "ambiguous_short_act_alias"
        ),
        "membership_non_dispositive": row.get(
            "membership_non_dispositive"
        ),
        "source_paragraph": row.get("source_paragraph"),
        "source_page": row.get("source_page"),
        "source_rank": row.get("source_rank"),
        "result_url": row.get("result_url"),
        "selected_membership_row_sha256": stable_json_sha256(row),
    }


def missing_official_title_summary(row: dict[str, Any]) -> bool:
    return not bool(row.get("official_title") or row.get("official_summary"))


def exclusion_like(row: dict[str, Any]) -> bool:
    return bool(
        row.get("likely_false_positive_reason")
        or row.get("review_bucket") == "ambiguous_short_act_collision"
    )


def ambiguous(row: dict[str, Any]) -> bool:
    return bool(
        row.get("ambiguous_short_act_alias_probe")
        or row.get("matched_short_act_title_aliases")
        or row.get("review_bucket") == "ambiguous_short_act_collision"
        or row.get("review_bucket")
        == "title_probe_parent_category_confirmation_collision"
    )


def risk_rank(row: dict[str, Any]) -> int:
    """Return reviewer workload risk, never a legislative score."""
    if exclusion_like(row):
        return 1
    if (
        row.get("review_bucket")
        == "title_probe_parent_category_confirmation_collision"
    ):
        return 1
    if row.get("review_bucket") == "recall_only_possible_category_miss":
        return 2
    if missing_official_title_summary(row):
        return 3
    if row.get("review_bucket") in {
        "official_title_or_summary_nexus",
        "multiple_independent_relevance_signals",
        "official_operative_text_signal",
    }:
        return 4
    return 5


def validate_proposal_authority(row: dict[str, Any]) -> None:
    if not (
        row.get("disposition") == "unresolved"
        and row.get("ratification_state") == "NOT_RATIFIED"
        and row.get("human_review_required") is True
        and row.get("proposal_only") is True
        and row.get("candidate_retained") is True
        and row.get("exclusion_performed") is False
        and row.get("membership_disposition_authority") is False
        and row.get("ratification_authority") is False
        and row.get("scoring_authority") is False
        and row.get("scoring_weight") == 0
    ):
        raise ContractError(
            f"Proposal authority drift: {row.get('canonical_key')}"
        )


def validate_provenance_alignment(
    proposal: dict[str, Any],
    record: dict[str, Any],
    membership: dict[str, Any],
) -> None:
    comparisons = {
        "canonical_key": record["canonical_key"],
        "display_number": record["display_number"],
        "canonical_url": record["canonical_url"],
        "raw_query_id": membership["query_id"],
        "raw_record_kind": membership["record_kind"],
        "raw_scoring_category": membership["scoring_category"],
        "raw_membership_non_dispositive": membership[
            "membership_non_dispositive"
        ],
        "source_page": membership["source_page"],
        "source_rank": membership["source_rank"],
        "result_url": membership["result_url"],
    }
    drift = [
        field
        for field, expected in comparisons.items()
        if proposal.get(field) != expected
    ]
    if drift:
        raise ContractError(
            "Proposal provenance drift for "
            f"{proposal.get('raw_query_id')}/{proposal.get('canonical_key')}: "
            f"{drift}"
        )


def staged_row(
    proposal: dict[str, Any],
    record: dict[str, Any],
    membership: dict[str, Any],
    *,
    prefill_id: str,
    prefill_manifest_sha256: str,
) -> dict[str, Any]:
    rank = risk_rank(proposal)
    is_exclusion_like = exclusion_like(proposal)
    is_ambiguous = ambiguous(proposal)
    is_missing = missing_official_title_summary(proposal)
    identity = {
        "prefill_id": prefill_id,
        "raw_query_id": proposal["raw_query_id"],
        "canonical_key": proposal["canonical_key"],
    }
    return {
        **proposal,
        "staged_membership_id": stable_json_sha256(identity),
        "queue_risk_rank": rank,
        "queue_risk_label": RISK_LABELS[rank],
        "queue_order_is_review_workload_only": True,
        "queue_order_has_scoring_authority": False,
        "visibility_flags": {
            "exclusion_like_proposal": is_exclusion_like,
            "ambiguous": is_ambiguous,
            "missing_official_title_summary_evidence": is_missing,
            "unsupported_amendment": bool(
                proposal.get("unsupported_amendment")
            ),
            "unresolved_retained": True,
        },
        "official_title_summary_evidence": {
            "available": bool(proposal.get("official_evidence_available")),
            "title": proposal.get("official_title"),
            "title_type": proposal.get("official_title_type"),
            "summary": proposal.get("official_summary"),
            "official_source_url": proposal.get("official_source_url"),
            "official_source_sha256": proposal.get(
                "official_source_sha256"
            ),
            "parsed_evidence_sha256": proposal.get(
                "parsed_evidence_sha256"
            ),
            "missing_title_and_summary": is_missing,
            "absence_is_neutral": True,
        },
        "candidate_provenance": record,
        "query_membership_provenance": membership,
        "source_proposal_row_sha256": stable_json_sha256(proposal),
        "source_prefill_id": prefill_id,
        "source_prefill_manifest_sha256": prefill_manifest_sha256,
        "proposal_only": True,
        "candidate_retained": True,
        "exclusion_performed": False,
        "membership_disposition_authority": False,
        "ratification_authority": False,
        "scoring_authority": False,
        "scoring_weight": 0,
    }


def visibility_row(
    row: dict[str, Any], visibility_kind: str, reason: str
) -> dict[str, Any]:
    return {
        **row,
        "visibility_kind": visibility_kind,
        "visibility_reason": reason,
        "visibility_is_overlapping_non_dispositive_view": True,
        "candidate_retained": True,
        "exclusion_performed": False,
        "membership_disposition_authority": False,
        "ratification_authority": False,
        "scoring_authority": False,
        "scoring_weight": 0,
    }


def validate_existing(out_dir: Path, expected_id: str) -> dict[str, Any]:
    manifest = read_object(out_dir / "manifest.json")
    if not (
        manifest.get("schema")
        == "legislation-partial-staged-review-queue-manifest-v1"
        and manifest.get("queue_id") == expected_id
        and manifest.get("status") == "PASS_LOSSLESS_PROPOSAL_ONLY"
        and manifest.get("all_memberships_present_exactly_once") is True
        and manifest.get("membership_missing_count") == 0
        and manifest.get("membership_extra_count") == 0
        and manifest.get("visibility_views_overlap_main_queue") is True
        and manifest.get("visibility_views_are_non_dispositive") is True
        and manifest.get("all_candidates_retained") is True
        and manifest.get("all_amendments_retained") is True
        and manifest.get("all_unresolved_retained") is True
        and manifest.get("review_prefill_only") is True
        and manifest.get("proposal_only") is True
        and manifest.get("human_ratification_required") is True
        and manifest.get("absence_is_neutral") is True
        and manifest.get("acceptance_passed") is False
        and manifest.get("scoring_weight") == 0
        and all(manifest.get(field) is False for field in AUTHORITY_FALSE_FIELDS)
    ):
        raise ContractError(f"Existing immutable queue identity drift: {out_dir}")
    if not (
        manifest.get("selected_membership_count")
        == manifest.get("staged_queue_count")
        == manifest.get("unique_staged_membership_count")
        == (
            (manifest.get("outputs") or {})
            .get("staged_review_queue", {})
            .get("record_count")
        )
    ):
        raise ContractError(
            f"Existing immutable queue count drift: {out_dir}"
        )
    for label, item in (manifest.get("outputs") or {}).items():
        require_file_item(item, f"existing staged queue output {label}")
    return manifest


def build_queue(
    *,
    prefill_manifest_path: Path,
    out_root: Path,
    current_path: Path | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    prefill_manifest_path = prefill_manifest_path.resolve()
    out_root = out_root.resolve()
    prefill, paths = load_prefill_manifest(prefill_manifest_path)
    producer = Path(__file__).resolve()
    bindings = {
        "schema": "legislation-partial-staged-review-queue-v1",
        "prefill_id": prefill["prefill_id"],
        "prefill_manifest_sha256": sha256_file(prefill_manifest_path),
        "prefill_membership_logical_sha256": prefill[
            "membership_logical_sha256"
        ],
        "producer_sha256": sha256_file(producer),
        "risk_order_contract": [
            {"rank": rank, "label": RISK_LABELS[rank]}
            for rank in sorted(RISK_LABELS)
        ],
    }
    queue_id = stable_json_sha256(bindings)
    out_dir = out_root / queue_id
    if out_dir.exists():
        result = validate_existing(out_dir, queue_id)
        created = False
    else:
        records: dict[str, dict[str, Any]] = {}
        for source in iter_ndjson(paths["selected_records"]):
            projection = compact_record_projection(source)
            key = projection["canonical_key"]
            if key in records:
                raise ContractError(f"Duplicate selected record: {key}")
            records[key] = projection
        memberships: dict[tuple[str, str], dict[str, Any]] = {}
        for source in iter_ndjson(paths["selected_memberships"]):
            key = membership_key(source)
            if key in memberships:
                raise ContractError(f"Duplicate selected membership: {key}")
            memberships[key] = compact_membership_projection(source)
        if (
            len(records) != prefill["selected_record_count"]
            or len(memberships) != prefill["selected_membership_count"]
        ):
            raise ContractError("Selected prefill inventory count drift")

        out_root.mkdir(parents=True, exist_ok=True)
        tmp_dir = out_root / f".{queue_id}.tmp.{os.getpid()}"
        tmp_dir.mkdir(parents=False, exist_ok=False)
        spools = {
            rank: (tmp_dir / f".risk_{rank}.ndjson").open(
                "w", encoding="utf-8", newline="\n"
            )
            for rank in sorted(RISK_LABELS)
        }
        visibility_specs = {
            "exclusion_like_visibility": (
                tmp_dir / "exclusion_like_visibility.ndjson"
            ),
            "ambiguous_visibility": (
                tmp_dir / "ambiguous_visibility.ndjson"
            ),
            "missing_official_evidence_visibility": (
                tmp_dir / "missing_official_evidence_visibility.ndjson"
            ),
        }
        visibility_handles = {
            label: path.open("w", encoding="utf-8", newline="\n")
            for label, path in visibility_specs.items()
        }
        seen: set[tuple[str, str]] = set()
        bucket_counts: Counter[str] = Counter()
        risk_counts: Counter[str] = Counter()
        visibility_counts: Counter[str] = Counter()
        proposal_count = 0
        try:
            for proposal in iter_ndjson(
                paths["membership_review_proposals"]
            ):
                validate_proposal_authority(proposal)
                key = proposal_key(proposal)
                if key in seen:
                    raise ContractError(f"Duplicate proposal membership: {key}")
                membership = memberships.get(key)
                record = records.get(key[1])
                if membership is None or record is None:
                    raise ContractError(
                        f"Proposal is outside selected membership union: {key}"
                    )
                validate_provenance_alignment(
                    proposal, record, membership
                )
                row = staged_row(
                    proposal,
                    record,
                    membership,
                    prefill_id=prefill["prefill_id"],
                    prefill_manifest_sha256=bindings[
                        "prefill_manifest_sha256"
                    ],
                )
                rank = row["queue_risk_rank"]
                write_ndjson_row(spools[rank], row)
                bucket_counts[str(row["review_bucket"])] += 1
                risk_counts[str(row["queue_risk_label"])] += 1
                flags = row["visibility_flags"]
                if flags["exclusion_like_proposal"]:
                    write_ndjson_row(
                        visibility_handles["exclusion_like_visibility"],
                        visibility_row(
                            row,
                            "exclusion_like_proposal",
                            str(
                                row.get("likely_false_positive_reason")
                                or "Ambiguous short-Act collision signal."
                            ),
                        ),
                    )
                    visibility_counts["exclusion_like_visibility"] += 1
                if flags["ambiguous"]:
                    write_ndjson_row(
                        visibility_handles["ambiguous_visibility"],
                        visibility_row(
                            row,
                            "ambiguous",
                            "Short-Act or alias ambiguity requires review.",
                        ),
                    )
                    visibility_counts["ambiguous_visibility"] += 1
                if flags["missing_official_title_summary_evidence"]:
                    reason = (
                        "Unsupported amendment retained without standalone "
                        "official title/summary package."
                        if flags["unsupported_amendment"]
                        else "Official title and summary are both absent."
                    )
                    write_ndjson_row(
                        visibility_handles[
                            "missing_official_evidence_visibility"
                        ],
                        visibility_row(
                            row,
                            "missing_official_title_summary_evidence",
                            reason,
                        ),
                    )
                    visibility_counts[
                        "missing_official_evidence_visibility"
                    ] += 1
                seen.add(key)
                proposal_count += 1
        finally:
            for handle in spools.values():
                handle.close()
            for handle in visibility_handles.values():
                handle.close()

        missing = set(memberships) - seen
        extra = seen - set(memberships)
        expected_count = prefill["selected_membership_count"]
        if missing or extra or proposal_count != expected_count:
            raise ContractError(
                "Lossless membership conservation failed: "
                f"expected={expected_count}, observed={proposal_count}, "
                f"missing={len(missing)}, extra={len(extra)}"
            )

        queue_path = tmp_dir / "staged_review_queue.ndjson"
        with queue_path.open(
            "w", encoding="utf-8", newline="\n"
        ) as destination:
            for rank in sorted(RISK_LABELS):
                spool_path = tmp_dir / f".risk_{rank}.ndjson"
                with open_ndjson(spool_path) as source:
                    for line in source:
                        destination.write(line)
                spool_path.unlink()

        outputs = {
            "staged_review_queue": file_item(
                queue_path, record_count=proposal_count
            ),
            **{
                label: file_item(
                    path, record_count=visibility_counts[label]
                )
                for label, path in visibility_specs.items()
            },
        }
        for item in outputs.values():
            item["path"] = str(
                (out_dir / Path(item["path"]).name).resolve()
            )
        result = {
            "schema": (
                "legislation-partial-staged-review-queue-manifest-v1"
            ),
            "status": "PASS_LOSSLESS_PROPOSAL_ONLY",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "queue_id": queue_id,
            "bindings": bindings,
            "source_prefill_id": prefill["prefill_id"],
            "partial_discovery_only": True,
            "required_query_count": prefill["required_query_count"],
            "completed_query_count": prefill["completed_query_count"],
            "selected_record_count": len(records),
            "selected_membership_count": len(memberships),
            "staged_queue_count": proposal_count,
            "unique_staged_membership_count": len(seen),
            "all_memberships_present_exactly_once": (
                proposal_count == len(seen) == len(memberships)
            ),
            "membership_missing_count": len(missing),
            "membership_extra_count": len(extra),
            "review_bucket_counts": dict(sorted(bucket_counts.items())),
            "risk_order_contract": bindings["risk_order_contract"],
            "risk_counts": dict(sorted(risk_counts.items())),
            "exclusion_like_visibility_count": visibility_counts[
                "exclusion_like_visibility"
            ],
            "ambiguous_visibility_count": visibility_counts[
                "ambiguous_visibility"
            ],
            "missing_official_evidence_visibility_count": (
                visibility_counts[
                    "missing_official_evidence_visibility"
                ]
            ),
            "visibility_views_overlap_main_queue": True,
            "visibility_views_are_non_dispositive": True,
            "all_candidates_retained": True,
            "all_amendments_retained": True,
            "all_unresolved_retained": True,
            "official_title_summary_evidence_carried": True,
            "candidate_provenance_carried": True,
            "query_membership_provenance_carried": True,
            "queue_order_is_review_workload_only": True,
            "queue_order_has_scoring_authority": False,
            "inputs": {
                "prefill_manifest": file_item(prefill_manifest_path),
                "prefill_outputs": prefill["outputs"],
                "producer": file_item(producer),
            },
            "outputs": outputs,
            "review_prefill_only": True,
            "proposal_only": True,
            "human_ratification_required": True,
            "candidate_creation_performed": False,
            "candidate_exclusion_performed": False,
            "semantic_exclusion_performed": False,
            "ratified_decision_performed": False,
            "automatic_scoring_performed": False,
            "sync_or_promotion_performed": False,
            "production_write_performed": False,
            "absence_is_neutral": True,
            "membership_disposition_authority": False,
            "ratification_authority": False,
            "scoring_authority": False,
            "scoring_weight": 0,
            "acceptance_passed": False,
        }
        write_json(tmp_dir / "manifest.json", result)
        os.replace(tmp_dir, out_dir)
        created = True

    if current_path is not None:
        current_path = current_path.resolve()
        current_path.relative_to(ROOT)
        pointer = {
            "schema": "legislation-partial-staged-review-queue-pointer-v1",
            "queue_id": queue_id,
            "queue_dir": str(out_dir),
            "manifest": file_item(out_dir / "manifest.json"),
            "source_prefill": file_item(prefill_manifest_path),
            "proposal_only": True,
            "ratification_authority": False,
            "scoring_authority": False,
            "sync_or_promotion_performed": False,
        }
        write_json(current_path, pointer)
    return out_dir, result, created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefill-manifest", type=Path)
    parser.add_argument(
        "--prefill-pointer", type=Path, default=DEFAULT_PREFILL_POINTER
    )
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--no-current-pointer", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = (
        args.prefill_manifest.resolve()
        if args.prefill_manifest
        else manifest_from_pointer(args.prefill_pointer)
    )
    out_dir, report, created = build_queue(
        prefill_manifest_path=manifest,
        out_root=args.out_root,
        current_path=None if args.no_current_pointer else args.current,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "queue_id": report["queue_id"],
                "out_dir": str(out_dir),
                "created": created,
                "selected_membership_count": report[
                    "selected_membership_count"
                ],
                "staged_queue_count": report["staged_queue_count"],
                "all_memberships_present_exactly_once": report[
                    "all_memberships_present_exactly_once"
                ],
                "risk_counts": report["risk_counts"],
                "visibility_counts": {
                    "exclusion_like": report[
                        "exclusion_like_visibility_count"
                    ],
                    "ambiguous": report["ambiguous_visibility_count"],
                    "missing_official_evidence": report[
                        "missing_official_evidence_visibility_count"
                    ],
                },
                "proposal_only": report["proposal_only"],
                "sync_or_promotion_performed": report[
                    "sync_or_promotion_performed"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

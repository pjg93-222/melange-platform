#!/usr/bin/env python3
"""Freeze a review-only audit of named-outline recall-miss proposals.

The audit is intentionally non-authoritative.  It verifies Congress-qualified
identity, outline/registry provenance, exact Advanced Search query provenance,
official BILLSTATUS title/summary evidence, and the structured BILLSTATUS
sections for every currently visible named-outline recall-only proposal.

No row produced here can include, exclude, ratify, score, sync, or promote a
measure.  In particular, the audit distinguishes a proposal emitted for an
isolated coverage-probe edge from a candidate actually absent from its parent
category query.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

try:
    from lane_file_io import open_ndjson
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import open_ndjson


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREFILL_POINTER = (
    ROOT / "audits" / "legfix5" / "partial_review_prefill" / "current.json"
)
DEFAULT_STRUCTURED_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "billstatus_bulk_enrichment"
    / "structured_relevance_current.json"
)
DEFAULT_CURRENT_CHECKPOINT = (
    ROOT
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "presync_current"
    / "checkpoint.json"
)
DEFAULT_OUT_ROOT = (
    ROOT
    / "audits"
    / "legfix5"
    / "named_outline_recall_miss_audit"
    / "snapshots"
)
DEFAULT_CURRENT = (
    ROOT
    / "audits"
    / "legfix5"
    / "named_outline_recall_miss_audit"
    / "current.json"
)
PRIMARY = "Congress.gov Advanced Search - Legislation"
SHA256_CHARS = frozenset("0123456789abcdef")
EXPECTED_SECTION_NAMES = (
    "policy_area",
    "legislative_subjects",
    "committees",
    "related_bills",
    "amendments",
    "laws",
    "committee_reports",
    "cbo_cost_estimates",
)


class ContractError(RuntimeError):
    """Raised when immutable input evidence does not close exactly."""


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"Expected JSON object: {path}")
    return value


def iter_ndjson_with_receipts(
    path: Path,
) -> Iterable[tuple[int, dict[str, Any], str]]:
    with open_ndjson(path, newline="") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            payload = raw_line.rstrip("\r\n")
            if not payload.strip():
                continue
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ContractError(
                    f"Expected object at {path}:{line_number}"
                )
            yield (
                line_number,
                value,
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
            count += 1
    return count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
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
    expected_sha = str(item.get("sha256") or "").casefold()
    if not isinstance(raw_path, str) or not raw_path:
        raise ContractError(f"{label} lacks path")
    if (
        len(expected_sha) != 64
        or set(expected_sha) - SHA256_CHARS
    ):
        raise ContractError(f"{label} lacks exact SHA-256")
    path = Path(raw_path).resolve()
    if not path.is_file():
        raise ContractError(f"{label} is missing: {path}")
    observed_sha = sha256_file(path)
    if observed_sha != expected_sha:
        raise ContractError(
            f"{label} SHA-256 mismatch: "
            f"expected {expected_sha}, got {observed_sha}"
        )
    expected_bytes = item.get("bytes")
    if (
        isinstance(expected_bytes, int)
        and path.stat().st_size != expected_bytes
    ):
        raise ContractError(f"{label} byte-count mismatch")
    return path


def require_pointer_manifest(
    pointer_path: Path,
    *,
    manifest_schema: str,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    pointer_path = pointer_path.resolve()
    pointer = read_object(pointer_path)
    manifest_item = pointer.get("manifest")
    if not isinstance(manifest_item, dict):
        raise ContractError(f"Pointer lacks manifest receipt: {pointer_path}")
    manifest_path = require_file_item(
        manifest_item, f"{pointer_path.name} manifest"
    )
    manifest = read_object(manifest_path)
    if manifest.get("schema") != manifest_schema:
        raise ContractError(
            f"Unexpected manifest schema at {manifest_path}"
        )
    return pointer, manifest_path, manifest


def canonical_key_from_known_bill(row: dict[str, Any]) -> str:
    congress = row.get("congress")
    number = str(row.get("number") or "")
    match = re.fullmatch(r"\s*([A-Za-z.]+)\s*(\d+)\s*", number)
    if not isinstance(congress, int) or match is None:
        raise ContractError(f"Malformed known-bill identity: {row!r}")
    measure_type = match.group(1).replace(".", "").casefold()
    return f"{congress}:{measure_type}:{int(match.group(2))}"


def line_evidence(
    *,
    path: Path,
    line_number: int,
    line_sha256: str,
) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "line_number": line_number,
        "line_payload_sha256": line_sha256,
    }


def exact_rows_for_keys(
    path: Path,
    keys: set[str],
) -> dict[str, list[tuple[int, dict[str, Any], str]]]:
    found = {key: [] for key in keys}
    for line_number, row, line_sha256 in iter_ndjson_with_receipts(path):
        key = str(row.get("canonical_key") or "")
        if key in found:
            found[key].append((line_number, row, line_sha256))
    return found


def require_single(
    rows: list[tuple[int, dict[str, Any], str]],
    *,
    label: str,
) -> tuple[int, dict[str, Any], str]:
    if len(rows) != 1:
        raise ContractError(f"{label}: expected one row, got {len(rows)}")
    return rows[0]


def load_query_artifact(
    item: dict[str, Any],
) -> tuple[Path, dict[str, Any], dict[str, Any], bool]:
    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ContractError(
            f"Query artifact {item.get('query_id')} lacks path"
        )
    path = Path(raw_path).resolve()
    if not path.is_file():
        raise ContractError(f"Query artifact is missing: {path}")
    current_receipt = file_item(path)
    query = read_object(path)
    if (
        query.get("query_id") != item.get("query_id")
        or query.get("primary_instrument") != PRIMARY
        or query.get("validation")
        != {
            "exact_result_total": True,
            "unique_result_total": True,
            "every_page_exact": True,
        }
        or query.get("result_total") != query.get("unique_record_count")
        or query.get("unique_record_count")
        != len(query.get("records") or [])
        or query.get("records_sha256") != item.get("records_sha256")
        or len(query.get("records") or []) != item.get("record_count")
    ):
        raise ContractError(f"Query artifact failed exact closure: {path}")
    outer_file_sha_matches_prefill = (
        current_receipt["sha256"] == item.get("sha256")
        and current_receipt["bytes"] == item.get("bytes")
    )
    return path, query, current_receipt, outer_file_sha_matches_prefill


def query_record(
    query: dict[str, Any],
    canonical_key: str,
) -> dict[str, Any] | None:
    matches = [
        row
        for row in query.get("records") or []
        if isinstance(row, dict)
        and row.get("canonical_key") == canonical_key
    ]
    if len(matches) > 1:
        raise ContractError(
            f"Duplicate {canonical_key} in query {query.get('query_id')}"
        )
    return matches[0] if matches else None


def section_receipt(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": section.get("status"),
        "source_xpath": section.get("source_xpath"),
        "source_record_sha256": section.get("source_record_sha256"),
        "parsed_fragment_sha256": section.get(
            "parsed_fragment_sha256"
        ),
        "direct_child_count": section.get("direct_child_count"),
        "item_count": section.get("item_count"),
        "leaf_text_count": section.get("leaf_text_count"),
        "data": section.get("data"),
    }


def partial_manifest_semantic_fingerprint(
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    queries = manifest.get("queries")
    if not isinstance(queries, list):
        raise ContractError("Partial manifest lacks queries array")
    fields = (
        "query_id",
        "label",
        "scoring_category",
        "record_kind",
        "parent_category_id",
        "parent_category_label",
        "identifier_text",
        "title_alias",
        "ambiguous_short_act_alias",
        "membership_non_dispositive",
        "result_total",
        "unique_record_count",
        "page_count",
        "counts_by_congress",
        "counts_by_type",
        "records_sha256",
        "validation",
    )
    return [
        {field: row.get(field) for field in fields}
        for row in queries
        if isinstance(row, dict)
    ]


def require_partial_manifest_contract(
    manifest: dict[str, Any],
    *,
    registry_sha256: str,
    label: str,
) -> list[dict[str, Any]]:
    fingerprint = partial_manifest_semantic_fingerprint(manifest)
    query_ids = [str(row.get("query_id") or "") for row in fingerprint]
    if not (
        manifest.get("schema") == "acu-congress-advanced-pages-manifest-v1"
        and manifest.get("primary_instrument") == PRIMARY
        and manifest.get("quorum_used") is False
        and manifest.get("registry_sha256") == registry_sha256
        and manifest.get("completed_at") is None
        and manifest.get("union") is None
        and len(fingerprint) == 22
        and len(query_ids) == len(set(query_ids))
        and all(
            row.get("result_total") == row.get("unique_record_count")
            and row.get("validation")
            == {
                "exact_result_total": True,
                "unique_result_total": True,
                "every_page_exact": True,
            }
            for row in fingerprint
        )
    ):
        raise ContractError(f"{label} partial-manifest contract drift")
    return fingerprint


def audit(
    *,
    prefill_pointer_path: Path,
    structured_pointer_path: Path,
    current_checkpoint_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    (
        _prefill_pointer,
        prefill_manifest_path,
        prefill_manifest,
    ) = require_pointer_manifest(
        prefill_pointer_path,
        manifest_schema="legislation-partial-review-prefill-manifest-v1",
    )
    if not (
        prefill_manifest.get("status") == "PASS_NON_RATIFIED_PARTIAL"
        and prefill_manifest.get("primary_discovery") == PRIMARY
        and prefill_manifest.get("review_prefill_only") is True
        and prefill_manifest.get("human_ratification_required") is True
        and prefill_manifest.get("sync_or_promotion_performed") is False
    ):
        raise ContractError("Prefill non-authority contract drift")

    outputs = prefill_manifest.get("outputs")
    inputs = prefill_manifest.get("inputs")
    if not isinstance(outputs, dict) or not isinstance(inputs, dict):
        raise ContractError("Prefill lacks input/output receipts")
    selected_records_path = require_file_item(
        outputs.get("selected_records") or {},
        "selected records",
    )
    selected_memberships_path = require_file_item(
        outputs.get("selected_memberships") or {},
        "selected memberships",
    )
    proposals_path = require_file_item(
        outputs.get("membership_review_proposals") or {},
        "membership review proposals",
    )
    registry_path = require_file_item(
        inputs.get("registry") or {}, "search registry"
    )
    prefill_selected_manifest_path = require_file_item(
        inputs.get("selected_partial_manifest") or {},
        "prefill-selected partial manifest",
    )
    sidecar_manifest_path = require_file_item(
        inputs.get("billstatus_sidecar_manifest") or {},
        "BILLSTATUS review-sidecar manifest",
    )
    sidecar_manifest = read_object(sidecar_manifest_path)
    if not (
        sidecar_manifest.get("review_prefill_only") is True
        and sidecar_manifest.get("official_source_only") is True
        and sidecar_manifest.get("sync_or_promotion_performed") is False
    ):
        raise ContractError("BILLSTATUS review-sidecar authority drift")
    metadata_item = (
        (sidecar_manifest.get("outputs") or {}).get("measure_metadata")
    )
    if not isinstance(metadata_item, dict):
        raise ContractError("Review sidecar lacks metadata receipt")
    metadata_path = require_file_item(
        metadata_item, "BILLSTATUS measure metadata"
    )

    (
        structured_pointer,
        structured_manifest_path,
        structured_manifest,
    ) = require_pointer_manifest(
        structured_pointer_path,
        manifest_schema="govinfo-billstatus-structured-relevance-sidecar-v1",
    )
    if not (
        structured_manifest.get("review_prefill_only") is True
        and structured_manifest.get("absence_is_neutral") is True
        and structured_manifest.get("sync_or_promotion_performed") is False
        and structured_manifest.get("candidate_creation_performed") is False
        and structured_manifest.get("semantic_exclusion_performed") is False
    ):
        raise ContractError("Structured sidecar non-authority drift")
    structured_dir = Path(
        str(structured_pointer.get("sidecar_dir") or "")
    ).resolve()
    measures_item = (
        (structured_manifest.get("outputs") or {}).get("measures")
    )
    if not isinstance(measures_item, dict):
        raise ContractError("Structured sidecar lacks measures receipt")
    measures_path = structured_dir / str(measures_item.get("file") or "")
    observed_measures = file_item(
        measures_path,
        record_count=measures_item.get("record_count"),
    )
    if (
        observed_measures["sha256"] != measures_item.get("sha256")
        or observed_measures["bytes"] != measures_item.get("bytes")
    ):
        raise ContractError("Structured measures receipt mismatch")

    registry = read_object(registry_path)
    if (
        registry.get("collection_contract", {}).get("primary_instrument")
        != PRIMARY
        or registry.get("collection_contract", {}).get("quorum_used")
        is not False
    ):
        raise ContractError("Registry discovery contract drift")
    outline_path = (
        ROOT
        / str(registry.get("source", {}).get("repository_path") or "")
    ).resolve()
    outline_receipt = file_item(outline_path)
    if outline_receipt["sha256"] != registry.get("source", {}).get(
        "sha256"
    ):
        raise ContractError("Outline SHA-256 differs from registry binding")

    current_checkpoint_path = current_checkpoint_path.resolve()
    checkpoint = read_object(current_checkpoint_path)
    current_strict_item = (
        (checkpoint.get("coverage") or {}).get("strict_manifest")
        or checkpoint.get("strict_manifest")
    )
    if not isinstance(current_strict_item, dict):
        raise ContractError(
            "Current checkpoint lacks selected strict-manifest receipt"
        )
    current_selected_manifest_path = require_file_item(
        current_strict_item, "current selected strict manifest"
    )
    prefill_selected_manifest = read_object(prefill_selected_manifest_path)
    current_selected_manifest = read_object(current_selected_manifest_path)
    registry_sha256 = sha256_file(registry_path)
    prefill_semantics = require_partial_manifest_contract(
        prefill_selected_manifest,
        registry_sha256=registry_sha256,
        label="prefill-selected",
    )
    current_semantics = require_partial_manifest_contract(
        current_selected_manifest,
        registry_sha256=registry_sha256,
        label="current-selected",
    )
    if prefill_semantics != current_semantics:
        raise ContractError(
            "Prefill and current selected manifests are not semantically "
            "equivalent"
        )
    manifest_semantic_sha256 = stable_json_sha256(current_semantics)

    proposal_matches: list[tuple[int, dict[str, Any], str]] = []
    for item in iter_ndjson_with_receipts(proposals_path):
        row = item[1]
        signals = row.get("recall_trigger_signals") or []
        if (
            row.get("proposal_class")
            == "possible_category_miss_review"
            and row.get("recall_only_miss_visibility_preserved") is True
            and "explicit_named_outline_bill" in signals
        ):
            proposal_matches.append(item)
    if len(proposal_matches) != 5:
        raise ContractError(
            "Expected five current named-outline recall-miss proposals, "
            f"got {len(proposal_matches)}"
        )
    proposal_keys = [str(item[1].get("canonical_key") or "") for item in proposal_matches]
    keys = set(proposal_keys)
    if len(keys) != len(proposal_matches):
        raise ContractError("Named-outline proposal identities are not unique")

    categories = {
        str(row.get("id") or ""): row
        for row in registry.get("categories") or []
        if isinstance(row, dict)
    }
    probes = {
        str(row.get("id") or ""): row
        for row in registry.get("coverage_probes") or []
        if isinstance(row, dict)
    }
    known_bill_map: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for category in categories.values():
        for known_bill in category.get("known_bills") or []:
            key = canonical_key_from_known_bill(known_bill)
            if key in known_bill_map:
                raise ContractError(f"Duplicate registry known bill: {key}")
            known_bill_map[key] = (category, known_bill)

    records_by_key = exact_rows_for_keys(selected_records_path, keys)
    memberships_by_key = exact_rows_for_keys(selected_memberships_path, keys)
    metadata_by_key = exact_rows_for_keys(metadata_path, keys)
    structured_by_key = exact_rows_for_keys(measures_path, keys)

    query_items = {
        str(item.get("query_id") or ""): item
        for item in prefill_manifest.get("query_artifacts") or []
        if isinstance(item, dict)
    }
    needed_query_ids = {
        str(row.get("raw_query_id") or "")
        for _, row, _ in proposal_matches
    } | {
        str(row.get("review_target_query_id") or "")
        for _, row, _ in proposal_matches
    }
    queries: dict[
        str, tuple[Path, dict[str, Any], dict[str, Any], bool]
    ] = {}
    for query_id in sorted(needed_query_ids):
        item = query_items.get(query_id)
        if item is None:
            raise ContractError(
                f"Selected prefill lacks query artifact {query_id}"
            )
        queries[query_id] = load_query_artifact(item)

    audit_rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for proposal_line, proposal, proposal_sha in proposal_matches:
        key = str(proposal["canonical_key"])
        category_and_bill = known_bill_map.get(key)
        if category_and_bill is None:
            raise ContractError(f"Proposal is not registry-known bill: {key}")
        category, known_bill = category_and_bill
        target_query_id = str(proposal.get("review_target_query_id") or "")
        probe_query_id = str(proposal.get("raw_query_id") or "")
        if (
            target_query_id != category.get("id")
            or probe_query_id not in probes
            or probes[probe_query_id].get("parent_category_id")
            != target_query_id
        ):
            raise ContractError(f"Query/registry mapping drift for {key}")

        record_line, record, record_sha = require_single(
            records_by_key[key], label=f"{key} selected record"
        )
        metadata_line, metadata, metadata_sha = require_single(
            metadata_by_key[key], label=f"{key} official metadata"
        )
        structured_line, structured, structured_sha = require_single(
            structured_by_key[key], label=f"{key} structured evidence"
        )
        all_memberships = memberships_by_key[key]
        target_memberships = [
            item
            for item in all_memberships
            if item[1].get("query_id") == target_query_id
        ]
        probe_memberships = [
            item
            for item in all_memberships
            if item[1].get("query_id") == probe_query_id
        ]
        target_membership = require_single(
            target_memberships,
            label=f"{key} parent-category membership",
        )
        probe_membership = require_single(
            probe_memberships,
            label=f"{key} probe membership",
        )

        (
            target_path,
            target_query,
            target_current_receipt,
            target_outer_sha_match,
        ) = queries[target_query_id]
        (
            probe_path,
            probe_query,
            probe_current_receipt,
            probe_outer_sha_match,
        ) = queries[probe_query_id]
        target_record = query_record(target_query, key)
        probe_record = query_record(probe_query, key)
        parent_query_missed = target_record is None
        if target_record is None or probe_record is None:
            raise ContractError(
                f"Selected membership lacks exact query record for {key}"
            )

        identity = structured.get("identity") or {}
        exact_identity = {
            "congress": record.get("congress"),
            "type": record.get("type"),
            "number": record.get("number"),
            "canonical_key": key,
            "canonical_url": record.get("canonical_url"),
            "display_number": record.get("display_number"),
        }
        expected_key = (
            f"{identity.get('congress')}:{identity.get('type')}:"
            f"{identity.get('number')}"
        )
        if not (
            expected_key == key
            and target_record.get("canonical_key") == key
            and probe_record.get("canonical_key") == key
            and metadata.get("canonical_key") == key
            and structured.get("canonical_key") == key
            and proposal.get("canonical_url")
            == record.get("canonical_url")
            == metadata.get("canonical_url")
        ):
            raise ContractError(f"Congress-qualified identity drift for {key}")

        official_source_sha = metadata.get("official_source_sha256")
        structured_source_sha = (
            structured.get("provenance", {})
            .get("raw_source", {})
            .get("sha256")
        )
        sections = structured.get("sections")
        if not isinstance(sections, dict) or set(sections) != set(
            EXPECTED_SECTION_NAMES
        ):
            raise ContractError(f"Structured section closure drift for {key}")
        if not (
            official_source_sha == structured_source_sha
            and all(
                section.get("source_record_sha256")
                == official_source_sha
                for section in sections.values()
            )
        ):
            raise ContractError(f"Official source hash drift for {key}")

        cite_term = (
            f'(congressId:{known_bill["congress"]} '
            f'AND cite:"{known_bill["compact_cite"]}")'
        )
        cite_present = any(
            item.get("kind") == "known_bill_cite"
            and item.get("query") == cite_term
            for item in category.get("query_terms") or []
            if isinstance(item, dict)
        )
        if not cite_present:
            raise ContractError(f"Known-bill cite missing for {key}")

        target_line, target_membership_row, target_membership_sha = (
            target_membership
        )
        probe_line, probe_membership_row, probe_membership_sha = (
            probe_membership
        )
        for membership_row, query_row in (
            (target_membership_row, target_record),
            (probe_membership_row, probe_record),
        ):
            if not (
                membership_row.get("source_page")
                == query_row.get("source_page")
                and membership_row.get("source_rank")
                == query_row.get("source_rank")
                and membership_row.get("result_url")
                == query_row.get("result_url")
            ):
                raise ContractError(
                    f"Query membership provenance drift for {key}"
                )

        all_membership_visibility = []
        for membership_line, membership, membership_sha in all_memberships:
            all_membership_visibility.append(
                {
                    "query_id": membership.get("query_id"),
                    "record_kind": membership.get("record_kind"),
                    "scoring_category": membership.get("scoring_category"),
                    "membership_non_dispositive": membership.get(
                        "membership_non_dispositive"
                    ),
                    "source_page": membership.get("source_page"),
                    "source_rank": membership.get("source_rank"),
                    "result_url": membership.get("result_url"),
                    "line_evidence": line_evidence(
                        path=selected_memberships_path,
                        line_number=membership_line,
                        line_sha256=membership_sha,
                    ),
                }
            )
        all_membership_visibility.sort(
            key=lambda row: (
                str(row["query_id"]),
                int(row["source_rank"] or 0),
            )
        )

        section_evidence = {
            name: section_receipt(sections[name])
            for name in EXPECTED_SECTION_NAMES
        }
        row = {
            "schema": "legislation-named-outline-recall-miss-audit-row-v1",
            "canonical_key": key,
            "identity": exact_identity,
            "registry_outline_provenance": {
                "category_id": category.get("id"),
                "category_label": category.get("label"),
                "category_outline_paragraph_range": category.get(
                    "outline_paragraph_range"
                ),
                "source_paragraph": known_bill.get("source_paragraph"),
                "source_text_exact_from_registry": known_bill.get(
                    "source_text"
                ),
                "registered_number": known_bill.get("number"),
                "compact_cite": known_bill.get("compact_cite"),
                "registered_position": known_bill.get("acu_position"),
                "position_is_provenance_not_audit_decision": True,
            },
            "recall_proposal_provenance": {
                "proposal_class": proposal.get("proposal_class"),
                "review_bucket": proposal.get("review_bucket"),
                "raw_query_id": probe_query_id,
                "review_target_query_id": target_query_id,
                "source_title_alias_probe_ids": proposal.get(
                    "source_title_alias_probe_ids"
                ),
                "source_title_aliases": proposal.get(
                    "source_title_aliases"
                ),
                "recall_trigger_signals": proposal.get(
                    "recall_trigger_signals"
                ),
                "candidate_retained": proposal.get("candidate_retained"),
                "proposal_only": proposal.get("proposal_only"),
                "ratification_state": proposal.get("ratification_state"),
                "scoring_weight": proposal.get("scoring_weight"),
                "line_evidence": line_evidence(
                    path=proposals_path,
                    line_number=proposal_line,
                    line_sha256=proposal_sha,
                ),
            },
            "advanced_search_probe": {
                "query_id": probe_query_id,
                "title_alias": probes[probe_query_id].get("title_alias"),
                "ambiguous_short_act_alias": probes[probe_query_id].get(
                    "ambiguous_short_act_alias"
                ),
                "membership_non_dispositive": probes[probe_query_id].get(
                    "membership_non_dispositive"
                ),
                "advanced_search_query": probe_query.get(
                    "advanced_search_query"
                ),
                "prefill_expected_query_artifact_receipt": query_items[
                    probe_query_id
                ],
                "current_query_artifact_receipt": (
                    probe_current_receipt
                ),
                "outer_query_file_sha256_matches_prefill": (
                    probe_outer_sha_match
                ),
                "records_payload_sha256_matches_prefill": True,
                "query_result_total": probe_query.get("result_total"),
                "records_sha256": probe_query.get("records_sha256"),
                "source_page": probe_record.get("source_page"),
                "source_rank": probe_record.get("source_rank"),
                "result_url": probe_record.get("result_url"),
                "title_returned": probe_record.get("title"),
                "membership_line_evidence": line_evidence(
                    path=selected_memberships_path,
                    line_number=probe_line,
                    line_sha256=probe_membership_sha,
                ),
            },
            "parent_category_query_check": {
                "query_id": target_query_id,
                "advanced_search_query": target_query.get(
                    "advanced_search_query"
                ),
                "prefill_expected_query_artifact_receipt": query_items[
                    target_query_id
                ],
                "current_query_artifact_receipt": (
                    target_current_receipt
                ),
                "outer_query_file_sha256_matches_prefill": (
                    target_outer_sha_match
                ),
                "records_payload_sha256_matches_prefill": True,
                "query_result_total": target_query.get("result_total"),
                "records_sha256": target_query.get("records_sha256"),
                "known_bill_cite_branch": cite_term,
                "known_bill_cite_branch_present": cite_present,
                "candidate_returned": not parent_query_missed,
                "parent_category_query_missed_candidate": (
                    parent_query_missed
                ),
                "individual_or_branch_attribution_available": False,
                "source_page": target_record.get("source_page"),
                "source_rank": target_record.get("source_rank"),
                "result_url": target_record.get("result_url"),
                "title_returned": target_record.get("title"),
                "membership_line_evidence": line_evidence(
                    path=selected_memberships_path,
                    line_number=target_line,
                    line_sha256=target_membership_sha,
                ),
            },
            "all_selected_query_memberships_preserved": (
                all_membership_visibility
            ),
            "official_billstatus": {
                "official_title": metadata.get("official_title"),
                "official_title_type": metadata.get(
                    "official_title_type"
                ),
                "all_official_titles": metadata.get(
                    "all_official_titles"
                ),
                "summary": metadata.get("summary"),
                "summary_source": metadata.get("summary_source"),
                "official_source_url": metadata.get(
                    "official_source_url"
                ),
                "official_source_sha256": official_source_sha,
                "parsed_evidence_sha256": metadata.get(
                    "parsed_evidence_sha256"
                ),
                "metadata_line_evidence": line_evidence(
                    path=metadata_path,
                    line_number=metadata_line,
                    line_sha256=metadata_sha,
                ),
                "structured_sections": section_evidence,
                "missing_sections": structured.get("missing_sections"),
                "empty_sections": structured.get("empty_sections"),
                "structured_line_evidence": line_evidence(
                    path=measures_path,
                    line_number=structured_line,
                    line_sha256=structured_sha,
                ),
            },
            "selected_record_line_evidence": line_evidence(
                path=selected_records_path,
                line_number=record_line,
                line_sha256=record_sha,
            ),
            "audit_observation": {
                "probe_edge_is_visible": True,
                "candidate_is_visible": True,
                "parent_category_query_also_returned_candidate": (
                    not parent_query_missed
                ),
                "proposal_label_is_not_candidate_level_miss": (
                    not parent_query_missed
                ),
                "inclusion_or_exclusion_decision": None,
                "classification_change_performed": False,
            },
            "authority": {
                "review_only": True,
                "candidate_creation_authority": False,
                "membership_disposition_authority": False,
                "inclusion_authority": False,
                "exclusion_authority": False,
                "ratification_authority": False,
                "scoring_authority": False,
                "sync_or_promotion_authority": False,
                "scoring_weight": 0,
            },
        }
        if not parent_query_missed:
            blockers.append(
                {
                    "code": (
                        "recall_only_label_not_candidate_level_category_miss"
                    ),
                    "canonical_key": key,
                    "proposal_query_id": probe_query_id,
                    "parent_category_query_id": target_query_id,
                }
            )
        audit_rows.append(row)

    audit_rows.sort(key=lambda row: row["canonical_key"])
    parent_hits = sum(
        row["parent_category_query_check"]["candidate_returned"]
        for row in audit_rows
    )
    summaries_available = sum(
        row["official_billstatus"]["summary"] is not None
        for row in audit_rows
    )
    section_status_counts = {
        name: {
            status: sum(
                row["official_billstatus"]["structured_sections"][name][
                    "status"
                ]
                == status
                for row in audit_rows
            )
            for status in ("present", "present_empty", "missing")
        }
        for name in EXPECTED_SECTION_NAMES
    }
    manifest_core = {
        "schema": "legislation-named-outline-recall-miss-audit-manifest-v1",
        "audit_status": "PASS_EXACT_NON_AUTHORITATIVE",
        "acceptance_gate_passed": False,
        "acceptance_blocker": (
            "All five membership-level recall-only proposals are also "
            "present in the selected parent-category query.  The current "
            "proposal label therefore does not describe a candidate-level "
            "category miss and must not be used as such downstream."
        ),
        "primary_discovery": PRIMARY,
        "quorum_used": False,
        "review_only": True,
        "candidate_visibility_preserved": True,
        "current_selected_manifest_bound": True,
        "prefill_selected_manifest_semantically_equivalent_to_current": True,
        "selected_manifest_semantic_equivalence": {
            "query_count": len(current_semantics),
            "semantic_fields": list(current_semantics[0]),
            "semantic_fingerprint_sha256": manifest_semantic_sha256,
            "prefill_selected_manifest": file_item(
                prefill_selected_manifest_path
            ),
            "current_selected_manifest": file_item(
                current_selected_manifest_path
            ),
            "same_physical_manifest": (
                prefill_selected_manifest_path
                == current_selected_manifest_path
            ),
            "semantic_equivalence_proven": True,
        },
        "absence_is_neutral": True,
        "candidate_creation_performed": False,
        "membership_disposition_performed": False,
        "inclusion_or_exclusion_decision_performed": False,
        "ratification_performed": False,
        "automatic_scoring_performed": False,
        "sync_or_promotion_performed": False,
        "counts": {
            "named_outline_recall_only_proposals": len(audit_rows),
            "exact_congress_qualified_identities": len(audit_rows),
            "parent_category_query_hits": parent_hits,
            "actual_parent_category_query_misses": (
                len(audit_rows) - parent_hits
            ),
            "official_titles_available": sum(
                row["official_billstatus"]["official_title"] is not None
                for row in audit_rows
            ),
            "official_summaries_available": summaries_available,
            "official_summaries_missing_preserved": (
                len(audit_rows) - summaries_available
            ),
            "classification_drift_blockers": len(blockers),
            "selected_query_artifacts_rewritten_since_prefill": sum(
                not outer_sha_match
                for _path, _query, _receipt, outer_sha_match in (
                    queries.values()
                )
            ),
            "rewritten_query_artifacts_with_unchanged_record_payload": sum(
                not outer_sha_match
                for _path, _query, _receipt, outer_sha_match in (
                    queries.values()
                )
            ),
        },
        "structured_section_status_counts": section_status_counts,
        "blockers": blockers,
        "inputs": {
            "prefill_pointer": file_item(prefill_pointer_path.resolve()),
            "prefill_manifest": file_item(prefill_manifest_path),
            "prefill_selected_manifest": file_item(
                prefill_selected_manifest_path
            ),
            "current_checkpoint": file_item(current_checkpoint_path),
            "current_selected_manifest": file_item(
                current_selected_manifest_path
            ),
            "registry": file_item(registry_path),
            "source_outline": outline_receipt,
            "selected_records": file_item(selected_records_path),
            "selected_memberships": file_item(selected_memberships_path),
            "membership_review_proposals": file_item(proposals_path),
            "billstatus_review_sidecar_manifest": file_item(
                sidecar_manifest_path
            ),
            "billstatus_measure_metadata": file_item(metadata_path),
            "structured_pointer": file_item(
                structured_pointer_path.resolve()
            ),
            "structured_manifest": file_item(structured_manifest_path),
            "structured_measures": observed_measures,
            "query_artifacts": {
                query_id: current_receipt
                for query_id, (
                    _path,
                    _query,
                    current_receipt,
                    _outer_sha_match,
                ) in sorted(queries.items())
            },
            "prefill_expected_query_artifact_receipts": {
                query_id: query_items[query_id]
                for query_id in sorted(queries)
            },
        },
    }
    return audit_rows, manifest_core


def materialize(
    *,
    prefill_pointer_path: Path,
    structured_pointer_path: Path,
    current_checkpoint_path: Path,
    out_root: Path,
    current_path: Path,
) -> tuple[Path, dict[str, Any]]:
    audit_rows, manifest_core = audit(
        prefill_pointer_path=prefill_pointer_path,
        structured_pointer_path=structured_pointer_path,
        current_checkpoint_path=current_checkpoint_path,
    )
    producer_receipt = file_item(Path(__file__).resolve())
    build_key = stable_json_sha256(
        {
            "schema": manifest_core["schema"],
            "inputs": {
                key: (
                    value.get("sha256")
                    if isinstance(value, dict) and "sha256" in value
                    else {
                        nested_key: nested_value["sha256"]
                        for nested_key, nested_value in value.items()
                    }
                )
                for key, value in manifest_core["inputs"].items()
            },
            "producer_sha256": producer_receipt["sha256"],
            "row_payload_sha256": stable_json_sha256(audit_rows),
        }
    )
    audit_id = stable_json_sha256(
        {
            "schema": manifest_core["schema"],
            "build_key": build_key,
            "proposal_keys": [
                row["canonical_key"] for row in audit_rows
            ],
        }
    )
    out_root = out_root.resolve()
    out_root.relative_to(ROOT)
    out_dir = out_root / audit_id
    manifest_path = out_dir / "manifest.json"
    if not out_dir.exists():
        tmp_dir = out_root / f".{audit_id}.tmp.{os.getpid()}"
        tmp_dir.mkdir(parents=True, exist_ok=False)
        rows_path = tmp_dir / "named_outline_recall_miss_audit.ndjson"
        row_count = write_ndjson(rows_path, audit_rows)
        manifest = {
            **manifest_core,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "audit_id": audit_id,
            "build_key": build_key,
            "producer": producer_receipt,
            "outputs": {
                "audit_rows": file_item(
                    rows_path, record_count=row_count
                )
            },
        }
        # Bind immutable output paths to the final content-addressed directory.
        manifest["outputs"]["audit_rows"]["path"] = str(
            (out_dir / rows_path.name).resolve()
        )
        write_json(tmp_dir / "manifest.json", manifest)
        tmp_dir.rename(out_dir)
    manifest = read_object(manifest_path)
    if (
        manifest.get("audit_id") != audit_id
        or manifest.get("build_key") != build_key
        or manifest.get("audit_status")
        != "PASS_EXACT_NON_AUTHORITATIVE"
    ):
        raise ContractError("Existing immutable audit identity drift")
    rows_item = manifest.get("outputs", {}).get("audit_rows")
    if not isinstance(rows_item, dict):
        raise ContractError("Audit manifest lacks rows receipt")
    require_file_item(rows_item, "immutable audit rows")

    current_path = current_path.resolve()
    current_path.relative_to(ROOT)
    pointer = {
        "schema": "legislation-named-outline-recall-miss-audit-pointer-v1",
        "audit_id": audit_id,
        "audit_dir": str(out_dir),
        "manifest": file_item(manifest_path),
        "review_only": True,
        "candidate_visibility_preserved": True,
        "acceptance_gate_passed": False,
        "sync_or_promotion_performed": False,
    }
    write_json(current_path, pointer)
    return manifest_path, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prefill-pointer",
        type=Path,
        default=DEFAULT_PREFILL_POINTER,
    )
    parser.add_argument(
        "--structured-pointer",
        type=Path,
        default=DEFAULT_STRUCTURED_POINTER,
    )
    parser.add_argument(
        "--current-checkpoint",
        type=Path,
        default=DEFAULT_CURRENT_CHECKPOINT,
    )
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path, manifest = materialize(
        prefill_pointer_path=args.prefill_pointer,
        structured_pointer_path=args.structured_pointer,
        current_checkpoint_path=args.current_checkpoint,
        out_root=args.out_root,
        current_path=args.current,
    )
    print(
        json.dumps(
            {
                "audit_status": manifest["audit_status"],
                "acceptance_gate_passed": manifest[
                    "acceptance_gate_passed"
                ],
                "audit_id": manifest["audit_id"],
                "manifest": str(manifest_path),
                **manifest["counts"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

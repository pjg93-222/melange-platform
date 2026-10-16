#!/usr/bin/env python3
"""Build a non-authoritative review prefill for partial strict discovery.

This adapter is deliberately unable to ratify, include, exclude, score, sync,
or promote legislation.  It joins the exact completed-query snapshot from the
currently selected Congress.gov Advanced Search partial manifest to immutable
official BILLSTATUS title/summary evidence and applies the same specificity
helpers used by the full membership-review producer.

Every raw query membership remains visible.  Unsupported amendment records
from the BILLSTATUS sidecar remain visible even when they are historical-only
and therefore absent from the current selected strict-query union.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from legislation import review_legislation_candidate_memberships as REVIEW

try:
    from lane_file_io import open_ndjson
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import open_ndjson


ROOT = Path(__file__).resolve().parents[2]
PRIMARY = "Congress.gov Advanced Search - Legislation"
DEFAULT_RUN = (
    ROOT / "audits" / "legfix5" / "congress_advanced_pages_20260725"
)
DEFAULT_REGISTRY = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_search_registry.json"
)
DEFAULT_CHECKPOINT = (
    ROOT
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "presync_current"
    / "checkpoint.json"
)
DEFAULT_SIDECAR_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "billstatus_bulk_enrichment"
    / "review_sidecar_current.json"
)
DEFAULT_OUT_ROOT = (
    ROOT / "audits" / "legfix5" / "partial_review_prefill" / "snapshots"
)
DEFAULT_CURRENT = (
    ROOT
    / "audits"
    / "legfix5"
    / "partial_review_prefill"
    / "current.json"
)
SHA256_CHARS = frozenset("0123456789abcdef")
YEAR_TOKEN_RE = re.compile(r"^(?:18|19|20)\d{2}$")


class ContractError(RuntimeError):
    """Raised when immutable input evidence does not close exactly."""


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
    """Match the collector's SHA-256(JSON.stringify(value))."""
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
    return path


def selected_manifest_from_checkpoint(checkpoint_path: Path) -> Path:
    checkpoint = read_object(checkpoint_path.resolve())
    strict = (
        (checkpoint.get("coverage") or {})
        .get("strict_manifest")
    )
    if not isinstance(strict, dict):
        raise ContractError(
            "Current pre-sync checkpoint lacks selected strict manifest"
        )
    path = require_file_item(
        {
            "path": strict.get("path"),
            "sha256": strict.get("sha256"),
        },
        "selected strict manifest",
    )
    if path.parent != DEFAULT_RUN.resolve():
        # Explicit CLI paths are available for tests/controlled alternate runs;
        # the default checkpoint must never silently switch discovery runs.
        raise ContractError(
            f"Selected strict manifest is outside the expected run: {path}"
        )
    return path


def sidecar_manifest_from_pointer(pointer_path: Path) -> Path:
    pointer = read_object(pointer_path.resolve())
    if (
        pointer.get("schema")
        != "govinfo-billstatus-review-sidecar-pointer-v1"
        or pointer.get("review_prefill_only") is not True
        or pointer.get("sync_or_promotion_performed") is not False
    ):
        raise ContractError("BILLSTATUS sidecar pointer contract drift")
    path = require_file_item(
        pointer.get("manifest") or {}, "BILLSTATUS sidecar manifest"
    )
    if str(pointer.get("sidecar_id") or "") != path.parent.name:
        raise ContractError("BILLSTATUS sidecar pointer identity drift")
    return path


def registry_query_map(
    registry: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    categories = registry.get("categories")
    probes = registry.get("coverage_probes")
    if not isinstance(categories, list) or not isinstance(probes, list):
        raise ContractError("Registry lacks categories/coverage_probes")
    category_by_id = {
        str(row.get("id")): row
        for row in categories
        if isinstance(row, dict) and row.get("id")
    }
    probe_by_id = {
        str(row.get("id")): row
        for row in probes
        if isinstance(row, dict) and row.get("id")
    }
    if (
        len(category_by_id) != len(categories)
        or len(probe_by_id) != len(probes)
        or set(category_by_id) & set(probe_by_id)
    ):
        raise ContractError("Registry query IDs are missing or duplicated")
    return category_by_id, probe_by_id


def canonical_sort_key(key: str) -> tuple[int, str, int]:
    congress, measure_type, number = key.split(":")
    return int(congress), measure_type, int(number)


def validate_partial_and_build_union(
    manifest_path: Path,
    registry_path: Path,
    registry: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Validate completed query receipts and reproduce the collector union."""
    manifest_path = manifest_path.resolve()
    registry_path = registry_path.resolve()
    manifest = read_object(manifest_path)
    category_by_id, probe_by_id = registry_query_map(registry)
    known_ids = set(category_by_id) | set(probe_by_id)
    selected = manifest.get("selected_query_ids")
    completed_rows = manifest.get("queries")
    if (
        manifest.get("schema")
        != "acu-congress-advanced-pages-manifest-v1"
        or manifest.get("primary_instrument") != PRIMARY
        or manifest.get("quorum_used") is not False
        or manifest.get("registry_sha256") != sha256_file(registry_path)
    ):
        raise ContractError("Selected partial-manifest identity drift")
    if (
        manifest.get("status") == "complete"
        or manifest.get("completed_at") is not None
        or manifest.get("union") is not None
    ):
        raise ContractError(
            "Complete/stale union manifest cannot authorize partial prefill"
        )
    if not manifest_path.name.startswith("manifest.partial."):
        raise ContractError("Selected manifest is not an immutable partial")
    if not (
        isinstance(selected, list)
        and all(isinstance(value, str) and value for value in selected)
        and len(selected) == len(set(selected))
        and set(selected) == known_ids
    ):
        raise ContractError("Selected query inventory does not equal registry")
    if not (
        isinstance(completed_rows, list)
        and completed_rows
        and len(completed_rows) < len(selected)
    ):
        raise ContractError("Partial manifest does not contain partial progress")

    run_dir = manifest_path.parent
    record_map: dict[str, dict[str, Any]] = {}
    memberships: list[dict[str, Any]] = []
    query_receipts: list[dict[str, Any]] = []
    completed_ids: set[str] = set()
    for entry in completed_rows:
        if not isinstance(entry, dict):
            raise ContractError("Partial manifest query row is not an object")
        query_id = str(entry.get("query_id") or "")
        query_file = entry.get("query_file")
        if (
            query_id not in known_ids
            or query_id in completed_ids
            or not isinstance(query_file, str)
            or not query_file
        ):
            raise ContractError("Invalid/duplicate completed query identity")
        query_path = (run_dir / query_file).resolve()
        try:
            query_path.relative_to(run_dir)
        except ValueError as error:
            raise ContractError(
                f"Query artifact escapes run directory: {query_id}"
            ) from error
        artifact = read_object(query_path)
        records = artifact.get("records")
        logical_hash = (
            stable_json_sha256(records) if isinstance(records, list) else None
        )
        validation = artifact.get("validation") or {}
        manifest_validation = entry.get("validation") or {}
        expected_meta = category_by_id.get(query_id) or probe_by_id[query_id]
        expected_effective = {
            "label": expected_meta.get("label"),
            "scoring_category": expected_meta.get("scoring_category"),
            "record_kind": expected_meta.get("record_kind", "category"),
            "parent_category_id": expected_meta.get("parent_category_id"),
            "title_alias": expected_meta.get("title_alias"),
            "ambiguous_short_act_alias": expected_meta.get(
                "ambiguous_short_act_alias", False
            ),
            "membership_non_dispositive": expected_meta.get(
                "membership_non_dispositive", False
            ),
        }
        if not (
            artifact.get("schema") == "congress-advanced-search-query-v1"
            and artifact.get("primary_instrument") == PRIMARY
            and artifact.get("query_id") == query_id
            and artifact.get("registry_sha256")
            == manifest.get("registry_sha256")
            and isinstance(records, list)
            and all(isinstance(row, dict) for row in records)
            and artifact.get("records_sha256")
            == entry.get("records_sha256")
            == logical_hash
            and artifact.get("result_total")
            == artifact.get("unique_record_count")
            == entry.get("result_total")
            == entry.get("unique_record_count")
            == len(records)
            and all(
                validation.get(field) is True
                and manifest_validation.get(field) is True
                for field in (
                    "exact_result_total",
                    "unique_result_total",
                    "every_page_exact",
                )
            )
            and all(
                entry.get(field)
                == artifact.get(field)
                == expected_value
                for field, expected_value in expected_effective.items()
            )
        ):
            raise ContractError(f"Completed query receipt drift: {query_id}")
        keys = [str(row.get("canonical_key") or "") for row in records]
        if not all(keys) or len(keys) != len(set(keys)):
            raise ContractError(
                f"Completed query has invalid/duplicate keys: {query_id}"
            )
        for source in records:
            key = str(source["canonical_key"])
            membership = {
                "query_id": query_id,
                "label": artifact.get("label"),
                "scoring_category": artifact.get("scoring_category"),
                "record_kind": artifact.get("record_kind"),
                "parent_category_id": artifact.get("parent_category_id"),
                "parent_category_label": artifact.get(
                    "parent_category_label"
                ),
                "identifier_text": artifact.get("identifier_text"),
                "title_alias": artifact.get("title_alias"),
                "ambiguous_short_act_alias": artifact.get(
                    "ambiguous_short_act_alias"
                ),
                "membership_non_dispositive": artifact.get(
                    "membership_non_dispositive"
                ),
                "source_paragraph": artifact.get("source_paragraph"),
                "canonical_key": key,
                "source_page": source.get("source_page"),
                "source_rank": source.get("source_rank"),
                "result_url": source.get("result_url"),
            }
            memberships.append(membership)
            if key not in record_map:
                row = dict(source)
                row.pop("source_query_id", None)
                row.pop("source_page", None)
                row.pop("source_rank", None)
                row["matched_scoring_categories"] = []
                row["matched_monitor_categories"] = []
                row["matched_coverage_probes"] = []
                record_map[key] = row
            if artifact.get("record_kind") == "coverage_probe":
                bucket = "matched_coverage_probes"
            elif artifact.get("scoring_category") is True:
                bucket = "matched_scoring_categories"
            else:
                bucket = "matched_monitor_categories"
            record_map[key][bucket].append(query_id)
        query_receipts.append(
            {
                "query_id": query_id,
                **file_item(query_path),
                "record_count": len(records),
                "records_sha256": logical_hash,
            }
        )
        completed_ids.add(query_id)

    for record in record_map.values():
        for field in (
            "matched_scoring_categories",
            "matched_monitor_categories",
            "matched_coverage_probes",
        ):
            record[field] = sorted(set(record[field]))
    records = [
        record_map[key]
        for key in sorted(record_map, key=canonical_sort_key)
    ]
    memberships.sort(
        key=lambda row: (
            row["query_id"],
            canonical_sort_key(row["canonical_key"]),
        )
    )
    return manifest, records, memberships, query_receipts


def load_sidecar(
    sidecar_manifest_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    sidecar = read_object(sidecar_manifest_path.resolve())
    forbidden_false = (
        "production_write_performed",
        "sync_or_promotion_performed",
        "candidate_creation_performed",
        "semantic_exclusion_performed",
        "ratified_decision_performed",
        "automatic_scoring_performed",
        "membership_disposition_authority",
        "ratification_authority",
        "scoring_authority",
    )
    if not (
        sidecar.get("schema")
        == "congress-sponsorship-enrichment-manifest-v1"
        and sidecar.get("adapter_schema")
        == "govinfo-billstatus-review-sidecar-v1"
        and sidecar.get("status") == "complete"
        and sidecar.get("official_source_only") is True
        and sidecar.get("review_prefill_only") is True
        and sidecar.get("snapshot_only") is True
        and sidecar.get("partial_discovery_snapshot") is True
        and sidecar.get("absence_is_neutral") is True
        and all(sidecar.get(field) is False for field in forbidden_false)
    ):
        raise ContractError("BILLSTATUS review sidecar authority drift")
    outputs = sidecar.get("outputs") or {}
    metadata_path = require_file_item(
        outputs.get("measure_metadata") or {},
        "BILLSTATUS measure metadata",
    )
    unsupported_path = require_file_item(
        outputs.get("verification_ledger") or {},
        "BILLSTATUS unresolved-measure ledger",
    )
    # Validate every output, including the complete neutral-gap inventory.
    for label, item in outputs.items():
        require_file_item(item, f"BILLSTATUS sidecar output {label}")
    metadata = {
        str(row.get("canonical_key")): row
        for row in iter_ndjson(metadata_path)
    }
    unsupported = {
        str(row.get("canonical_key")): row
        for row in iter_ndjson(unsupported_path)
    }
    if (
        len(metadata) != outputs["measure_metadata"].get("record_count")
        or len(unsupported)
        != outputs["verification_ledger"].get("record_count")
        or set(metadata) & set(unsupported)
    ):
        raise ContractError("BILLSTATUS sidecar candidate partition drift")
    for key, row in unsupported.items():
        if not (
            row.get("candidate_retained") is True
            and row.get("absence_is_neutral") is True
            and row.get("membership_disposition_authority") is False
            and row.get("ratification_authority") is False
            and row.get("scoring_authority") is False
        ):
            raise ContractError(
                f"Unsupported amendment authority drift: {key}"
            )
    return sidecar, metadata, unsupported


def known_bill_keys(category: dict[str, Any] | None) -> set[str]:
    if not category:
        return set()
    keys: set[str] = set()
    for bill in category.get("known_bills", []):
        try:
            key = REVIEW.compact_cite_key(
                int(bill["congress"]), str(bill["compact_cite"])
            )
        except (KeyError, TypeError, ValueError):
            key = None
        if key:
            keys.add(key)
    return keys


def normalized_tokens(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", REVIEW.normalize(value).casefold())


def contains_bounded_tokens(value: Any, phrase: Any) -> bool:
    """Match a phrase as a bounded token sequence, never a substring."""
    haystack = normalized_tokens(value)
    needle = normalized_tokens(phrase)
    if not haystack or not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(
        haystack[index : index + width] == needle
        for index in range(len(haystack) - width + 1)
    )


def primary_title_matches_alias(value: Any, alias: Any) -> bool:
    """Require title identity, allowing only the conventional ``of YEAR``."""
    title_tokens = normalized_tokens(value)
    alias_tokens = normalized_tokens(alias)
    if title_tokens == alias_tokens:
        return True
    suffix = title_tokens[len(alias_tokens) :]
    return (
        bool(alias_tokens)
        and title_tokens[: len(alias_tokens)] == alias_tokens
        and len(suffix) == 2
        and suffix[0] == "of"
        and YEAR_TOKEN_RE.fullmatch(suffix[1]) is not None
    )


def title_alias_evidence(
    *,
    discovery_title: Any,
    official_title: Any,
    official_summary: Any,
    category: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """Separate primary-title identity from incidental alias references."""
    evidence = {
        "primary_specific": [],
        "primary_short_act": [],
        "discovery_primary_specific": [],
        "official_primary_specific": [],
        "summary_specific_references": [],
        "short_act_references": [],
        "all_specific_references": [],
    }
    if not category:
        return evidence
    for alias in category.get("title_aliases", []):
        normalized_alias = REVIEW.normalize(alias)
        if not normalized_alias:
            continue
        tokens = normalized_tokens(alias)
        distinctive = {
            token
            for token in tokens
            if token not in REVIEW.GENERIC_EVIDENCE_TOKENS
        }
        is_short_act = (
            "act" in tokens and len(distinctive) < 3
        )
        discovery_primary = primary_title_matches_alias(
            discovery_title, alias
        )
        official_primary = primary_title_matches_alias(
            official_title, alias
        )
        if discovery_primary or official_primary:
            target = (
                evidence["primary_short_act"]
                if is_short_act
                else evidence["primary_specific"]
            )
            target.append(normalized_alias)
            if not is_short_act:
                if discovery_primary:
                    evidence["discovery_primary_specific"].append(
                        normalized_alias
                    )
                if official_primary:
                    evidence["official_primary_specific"].append(
                        normalized_alias
                    )
        for value, summary_reference in (
            (discovery_title, False),
            (official_title, False),
            (official_summary, True),
        ):
            if not contains_bounded_tokens(value, alias):
                continue
            if is_short_act:
                evidence["short_act_references"].append(normalized_alias)
            else:
                evidence["all_specific_references"].append(
                    normalized_alias
                )
                if summary_reference and not (
                    discovery_primary or official_primary
                ):
                    evidence["summary_specific_references"].append(
                        normalized_alias
                    )
    return {
        field: sorted(set(values))
        for field, values in evidence.items()
    }


def domain_anchors(value: Any) -> list[str]:
    return sorted(
        {
            REVIEW.normalize(match.group(0))
            for match in REVIEW.DOMAIN_ANCHOR_RE.finditer(
                REVIEW.normalize(value)
            )
        },
        key=str.casefold,
    )


def proposal_class(bucket: str) -> str:
    if bucket == "title_probe_parent_category_confirmation_collision":
        return "non_dispositive_title_probe_confirmation_collision_review"
    if bucket == "ambiguous_short_act_collision":
        return "careful_false_positive_collision_review"
    if (
        bucket
        == "ambiguous_short_act_collision_with_independent_official_nexus"
    ):
        return "possible_relevance_with_collision_warning_review"
    if bucket in {
        "official_title_or_summary_nexus",
        "multiple_independent_relevance_signals",
        "official_operative_text_signal",
    }:
        return "possible_relevance_review"
    if bucket == "recall_only_possible_category_miss":
        return "possible_category_miss_review"
    return "unresolved_context_review"


def build_proposals(
    records: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
    registry: dict[str, Any],
    metadata_by_key: dict[str, dict[str, Any]],
    unsupported_by_key: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    category_by_id, _ = registry_query_map(registry)
    record_by_key = {row["canonical_key"]: row for row in records}
    alias_hits: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for membership in memberships:
        parent = membership.get("parent_category_id")
        if (
            membership.get("membership_non_dispositive") is True
            and parent in category_by_id
        ):
            alias_hits[(membership["canonical_key"], str(parent))].append(
                membership
            )
    parent_category_memberships: dict[
        tuple[str, str], dict[str, Any]
    ] = {}
    for membership in memberships:
        query_id = str(membership.get("query_id") or "")
        if (
            membership.get("record_kind") == "category"
            and query_id in category_by_id
        ):
            pair = (membership["canonical_key"], query_id)
            if pair in parent_category_memberships:
                raise ContractError(
                    "Duplicate selected parent-category membership: "
                    f"{pair[0]}/{pair[1]}"
                )
            parent_category_memberships[pair] = membership

    proposals: list[dict[str, Any]] = []
    for membership in memberships:
        key = membership["canonical_key"]
        record = record_by_key[key]
        raw_query_id = membership["query_id"]
        target_query_id = (
            membership.get("parent_category_id")
            if membership.get("parent_category_id") in category_by_id
            else raw_query_id
        )
        category = category_by_id.get(str(target_query_id))
        metadata = metadata_by_key.get(key, {})
        unsupported = unsupported_by_key.get(key)
        evidence = REVIEW.evidence_text(record, metadata)
        official_title = REVIEW.normalize(metadata.get("official_title"))
        official_summary = REVIEW.normalize(metadata.get("summary"))
        discovery_title = REVIEW.normalize(
            record.get("title") or record.get("purpose")
        )
        phrase_matches = (
            REVIEW.matched_identifier_phrases(evidence, category)
            if category
            else []
        )
        title_identifier_matches = (
            REVIEW.matched_identifier_phrases(official_title, category)
            if category
            else []
        )
        summary_identifier_matches = (
            REVIEW.matched_identifier_phrases(official_summary, category)
            if category
            else []
        )
        alias_evidence = title_alias_evidence(
            discovery_title=discovery_title,
            official_title=official_title,
            official_summary=official_summary,
            category=category,
        )
        specific_aliases = alias_evidence["primary_specific"]
        short_act_aliases = alias_evidence["short_act_references"]
        named_identifier_act_references = (
            REVIEW.matched_named_act_references(evidence, category)
            if category
            else []
        )
        named_act_references = sorted(
            set(named_identifier_act_references)
            | (
                set(alias_evidence["all_specific_references"])
                - set(alias_evidence["primary_specific"])
            )
        )
        same_target_alias_hits = alias_hits.get((key, str(target_query_id)), [])
        ambiguous_alias_hit = (
            membership.get("ambiguous_short_act_alias") is True
            or any(
                row.get("ambiguous_short_act_alias") is True
                for row in same_target_alias_hits
            )
        )
        named_outline = key in known_bill_keys(category)
        coverage_probe = membership.get("record_kind") == "coverage_probe"
        title_probe = bool(
            coverage_probe
            and membership.get("membership_non_dispositive") is True
            and REVIEW.normalize(membership.get("title_alias"))
        )
        parent_category_membership = parent_category_memberships.get(
            (key, str(target_query_id))
        )
        title_probe_parent_confirmation = bool(
            title_probe and parent_category_membership is not None
        )
        row = {
            "canonical_key": key,
            "display_number": record.get("display_number"),
            "title": record.get("title") or record.get("purpose"),
            "canonical_url": record.get("canonical_url"),
            "raw_query_id": raw_query_id,
            "review_target_query_id": target_query_id,
            "category_label": (
                category.get("label")
                if category
                else membership.get("label")
            ),
            "raw_record_kind": membership.get("record_kind"),
            "raw_scoring_category": membership.get("scoring_category"),
            "raw_membership_non_dispositive": membership.get(
                "membership_non_dispositive"
            ),
            "source_page": membership.get("source_page"),
            "source_rank": membership.get("source_rank"),
            "result_url": membership.get("result_url"),
            "coverage_probe_unclassified": coverage_probe,
            "source_title_alias_probe_ids": sorted(
                {
                    hit["query_id"]
                    for hit in same_target_alias_hits
                }
            ),
            "source_title_aliases": sorted(
                {
                    REVIEW.normalize(hit.get("title_alias"))
                    for hit in same_target_alias_hits
                    if REVIEW.normalize(hit.get("title_alias"))
                }
            ),
            "ambiguous_short_act_alias_probe": ambiguous_alias_hit,
            "title_alias_probe_is_non_dispositive": bool(
                same_target_alias_hits
            ),
            "selected_parent_category_membership_present": bool(
                parent_category_membership is not None
            ),
            "selected_parent_category_membership_provenance": (
                {
                    "query_id": parent_category_membership.get("query_id"),
                    "record_kind": parent_category_membership.get(
                        "record_kind"
                    ),
                    "scoring_category": parent_category_membership.get(
                        "scoring_category"
                    ),
                    "source_page": parent_category_membership.get(
                        "source_page"
                    ),
                    "source_rank": parent_category_membership.get(
                        "source_rank"
                    ),
                    "result_url": parent_category_membership.get(
                        "result_url"
                    ),
                }
                if parent_category_membership is not None
                else None
            ),
            "title_probe_parent_category_confirmation": (
                title_probe_parent_confirmation
            ),
            "named_outline_bill": named_outline,
            "matched_identifier_phrases": phrase_matches,
            "official_title_identifier_matches": title_identifier_matches,
            "official_summary_identifier_matches": summary_identifier_matches,
            "matched_named_identifier_act_references_non_dispositive": (
                named_identifier_act_references
            ),
            "matched_named_act_references_non_dispositive": (
                named_act_references
            ),
            "rejected_non_specific_identifier_phrases": (
                REVIEW.rejected_non_specific_identifiers(category)
                if category
                else []
            ),
            "matched_specific_title_aliases": specific_aliases,
            "matched_short_act_title_aliases": short_act_aliases,
            "matched_specific_title_aliases_anywhere_non_dispositive": (
                alias_evidence["all_specific_references"]
            ),
            "matched_specific_title_alias_references_non_dispositive": (
                sorted(
                    set(alias_evidence["all_specific_references"])
                    - set(alias_evidence["primary_specific"])
                )
            ),
            "official_summary_specific_title_alias_references_"
            "non_dispositive": alias_evidence[
                "summary_specific_references"
            ],
            "discovery_primary_specific_title_aliases": alias_evidence[
                "discovery_primary_specific"
            ],
            "official_primary_specific_title_aliases": alias_evidence[
                "official_primary_specific"
            ],
            "primary_short_act_title_aliases_non_dispositive": (
                alias_evidence["primary_short_act"]
            ),
            "short_act_title_requires_independent_evidence": True,
            "generic_act_language_can_never_establish_relevance": True,
            "discovery_title_domain_anchors": domain_anchors(
                discovery_title
            ),
            "official_title_domain_anchors": domain_anchors(
                official_title
            ),
            "official_summary_domain_anchors": domain_anchors(
                official_summary
            ),
            "official_title_specificity_valid": (
                REVIEW.official_evidence_is_specific(official_title)
            ),
            "official_summary_specificity_valid": (
                REVIEW.official_evidence_is_specific(official_summary)
            ),
            "official_title": metadata.get("official_title"),
            "official_title_type": metadata.get("official_title_type"),
            "official_summary": metadata.get("summary"),
            "official_source_url": metadata.get("official_source_url"),
            "official_source_sha256": metadata.get(
                "official_source_sha256"
            ),
            "parsed_evidence_sha256": metadata.get(
                "parsed_evidence_sha256"
            ),
            "official_evidence_available": bool(metadata),
            "unsupported_amendment": unsupported is not None,
            "unsupported_amendment_evidence": unsupported,
            "official_text_evidence_state": "official_text_unavailable",
            "live_identifier_query_hits": [],
            "candidate_category_edge_origins": [],
            "trusted_article_action_match_count": 0,
            "trusted_article_strong_corroboration_count": 0,
            # The only disposition-like field is explicitly unresolved.  This
            # file is never accepted as a membership review ledger.
            "disposition": "unresolved",
            "ratification_state": "NOT_RATIFIED",
            "human_review_required": True,
            "proposal_only": True,
            "proposed_disposition": None,
            "usable_as_membership_review_ledger": False,
            "decision_import_allowed": False,
            "scoring_input_allowed": False,
            "raw_scoring_category_is_query_provenance_only": True,
            "candidate_retained": True,
            "exclusion_performed": False,
            "membership_disposition_authority": False,
            "ratification_authority": False,
            "scoring_authority": False,
            "scoring_weight": 0,
        }
        REVIEW.add_review_sort_fields(row)
        independent_official_nexus = bool(
            row["official_title_identifier_matches"]
            or row["official_summary_identifier_matches"]
            or row["official_primary_specific_title_aliases"]
        )
        recall_trigger_signals = []
        if named_outline:
            recall_trigger_signals.append("explicit_named_outline_bill")
        if row["official_title_identifier_matches"]:
            recall_trigger_signals.append(
                "official_title_identifier_phrase"
            )
        if row["official_summary_identifier_matches"]:
            recall_trigger_signals.append(
                "official_summary_identifier_phrase"
            )
        if row["official_primary_specific_title_aliases"]:
            recall_trigger_signals.append(
                "official_primary_specific_title_alias"
            )
        if row["discovery_primary_specific_title_aliases"]:
            recall_trigger_signals.append(
                "congress_search_primary_specific_title_alias"
            )
        row["recall_trigger_signals"] = recall_trigger_signals
        row["recall_trigger_signal_count"] = len(recall_trigger_signals)
        row["recall_only_miss_visibility_preserved"] = bool(
            coverage_probe
            and recall_trigger_signals
            and parent_category_membership is None
        )
        row[
            "title_probe_parent_category_confirmation_visibility_preserved"
        ] = title_probe_parent_confirmation
        row["probe_membership_retained_exactly_once"] = coverage_probe
        row["probe_membership_is_non_dispositive_confirmation_only"] = (
            title_probe_parent_confirmation
        )
        ambiguous_collision = bool(
            ambiguous_alias_hit or row["matched_short_act_title_aliases"]
        )
        if title_probe_parent_confirmation:
            row.update(
                {
                    "review_priority": 2,
                    "review_bucket": (
                        "title_probe_parent_category_"
                        "confirmation_collision"
                    ),
                    "likely_false_positive_reason": None,
                    "non_dispositive_confirmation_note": (
                        "The isolated title-probe edge is preserved, "
                        "but the same Congress-qualified candidate is "
                        "already present in the selected parent-category "
                        "query. This edge is confirmation/collision "
                        "provenance only and is not a category-miss signal."
                    ),
                }
            )
        elif coverage_probe and recall_trigger_signals:
            row.update(
                {
                    "review_priority": 1,
                    "review_bucket": (
                        "recall_only_possible_category_miss"
                    ),
                    "likely_false_positive_reason": (
                        "Coverage-probe membership remains "
                        "non-dispositive and requires category review."
                    ),
                }
            )
            row["non_dispositive_confirmation_note"] = None
        elif ambiguous_collision and independent_official_nexus:
            row.update(
                {
                    "review_priority": 2,
                    "review_bucket": (
                        "ambiguous_short_act_collision_with_"
                        "independent_official_nexus"
                    ),
                    "likely_false_positive_reason": (
                        "An ambiguous short-Act collision coexists with "
                        "an independent official title/summary signal; "
                        "neither signal is dispositive."
                    ),
                }
            )
            row["non_dispositive_confirmation_note"] = None
        else:
            row["non_dispositive_confirmation_note"] = None
        row["proposal_class"] = proposal_class(row["review_bucket"])
        row["specificity_logic_source"] = (
            "fec/legislation/review_legislation_candidate_memberships.py"
        )
        proposals.append(row)
    proposals.sort(
        key=lambda row: (
            row["review_priority"],
            row["review_bucket"],
            str(row["review_target_query_id"]),
            canonical_sort_key(row["canonical_key"]),
            row["raw_query_id"],
        )
    )
    return proposals


def validate_proposal_authority_contract(
    row: dict[str, Any], *, label: str
) -> None:
    false_fields = (
        "usable_as_membership_review_ledger",
        "decision_import_allowed",
        "scoring_input_allowed",
        "exclusion_performed",
        "membership_disposition_authority",
        "ratification_authority",
        "scoring_authority",
    )
    if not (
        row.get("disposition") == "unresolved"
        and row.get("proposed_disposition") is None
        and row.get("ratification_state") == "NOT_RATIFIED"
        and row.get("human_review_required") is True
        and row.get("proposal_only") is True
        and row.get("candidate_retained") is True
        and row.get("raw_scoring_category_is_query_provenance_only")
        is True
        and row.get("scoring_weight") == 0
        and all(row.get(field) is False for field in false_fields)
    ):
        raise ContractError(
            f"Non-authoritative proposal contract drift: {label}"
        )


def validate_unsupported_authority_contract(
    row: dict[str, Any], *, label: str
) -> None:
    false_fields = (
        "usable_as_membership_review_ledger",
        "decision_import_allowed",
        "scoring_input_allowed",
        "exclusion_performed",
        "membership_disposition_authority",
        "ratification_authority",
        "scoring_authority",
    )
    if not (
        row.get("proposed_disposition") is None
        and row.get("proposal_only") is True
        and row.get("candidate_retained") is True
        and row.get("scoring_weight") == 0
        and all(row.get(field) is False for field in false_fields)
    ):
        raise ContractError(
            f"Non-authoritative unsupported contract drift: {label}"
        )


def validate_existing(out_dir: Path, expected_id: str) -> dict[str, Any]:
    manifest_path = out_dir / "manifest.json"
    manifest = read_object(manifest_path)
    forbidden_false = (
        "candidate_membership_review_overwritten",
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
        "downstream_consumption_allowed",
        "decision_import_allowed",
        "scoring_input_allowed",
        "acceptance_passed",
    )
    if (
        manifest.get("prefill_id") != expected_id
        or manifest.get("schema")
        != "legislation-partial-review-prefill-manifest-v1"
        or manifest.get("status") != "PASS_NON_RATIFIED_PARTIAL"
        or manifest.get("review_prefill_only") is not True
        or manifest.get("human_ratification_required") is not True
        or manifest.get("absence_is_neutral") is not True
        or manifest.get("scoring_weight") != 0
        or not all(
            manifest.get(field) is False for field in forbidden_false
        )
    ):
        raise ContractError(f"Existing immutable prefill identity drift: {out_dir}")
    outputs = manifest.get("outputs") or {}
    expected_outputs = {
        "selected_records",
        "selected_memberships",
        "membership_review_proposals",
        "unsupported_amendments",
    }
    if set(outputs) != expected_outputs:
        raise ContractError(
            f"Existing immutable prefill output inventory drift: {out_dir}"
        )
    for label, item in outputs.items():
        require_file_item(item, f"existing prefill output {label}")
    proposal_count = 0
    for proposal_count, row in enumerate(
        iter_ndjson(
            require_file_item(
                outputs["membership_review_proposals"],
                "existing prefill membership proposals",
            )
        ),
        1,
    ):
        validate_proposal_authority_contract(
            row, label=f"proposal row {proposal_count}"
        )
    unsupported_count = 0
    for unsupported_count, row in enumerate(
        iter_ndjson(
            require_file_item(
                outputs["unsupported_amendments"],
                "existing prefill unsupported amendments",
            )
        ),
        1,
    ):
        validate_unsupported_authority_contract(
            row, label=f"unsupported row {unsupported_count}"
        )
    if (
        proposal_count != manifest.get("proposal_count")
        or proposal_count
        != outputs["membership_review_proposals"].get("record_count")
        or unsupported_count != manifest.get("unsupported_amendment_count")
        or unsupported_count
        != outputs["unsupported_amendments"].get("record_count")
    ):
        raise ContractError(
            f"Existing immutable prefill output count drift: {out_dir}"
        )
    return manifest


def build_prefill(
    *,
    manifest_path: Path,
    registry_path: Path,
    sidecar_manifest_path: Path,
    out_root: Path,
    current_path: Path | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    manifest_path = manifest_path.resolve()
    registry_path = registry_path.resolve()
    sidecar_manifest_path = sidecar_manifest_path.resolve()
    out_root = out_root.resolve()
    registry = read_object(registry_path)
    (
        strict_manifest,
        records,
        memberships,
        query_receipts,
    ) = validate_partial_and_build_union(
        manifest_path, registry_path, registry
    )
    sidecar, metadata_by_key, unsupported_by_key = load_sidecar(
        sidecar_manifest_path
    )
    selected_keys = {row["canonical_key"] for row in records}
    official_partition = set(metadata_by_key) | set(unsupported_by_key)
    missing_sidecar_keys = selected_keys - official_partition
    if missing_sidecar_keys:
        raise ContractError(
            "Selected strict records are missing from BILLSTATUS sidecar: "
            f"{sorted(missing_sidecar_keys, key=canonical_sort_key)[:5]}"
        )

    producer = Path(__file__).resolve()
    specificity_producer = Path(REVIEW.__file__).resolve()
    bindings = {
        "schema": "legislation-partial-review-prefill-v1",
        "selected_partial_manifest_sha256": sha256_file(manifest_path),
        "registry_sha256": sha256_file(registry_path),
        "billstatus_sidecar_manifest_sha256": sha256_file(
            sidecar_manifest_path
        ),
        "producer_sha256": sha256_file(producer),
        "specificity_logic_producer_sha256": sha256_file(
            specificity_producer
        ),
    }
    prefill_id = stable_json_sha256(bindings)
    out_dir = out_root / prefill_id
    if out_dir.exists():
        result = validate_existing(out_dir, prefill_id)
        created = False
    else:
        proposals = build_proposals(
            records,
            memberships,
            registry,
            metadata_by_key,
            unsupported_by_key,
        )
        for index, row in enumerate(proposals, 1):
            validate_proposal_authority_contract(
                row, label=f"generated proposal row {index}"
            )
        memberships_by_key: dict[str, list[str]] = defaultdict(list)
        for row in memberships:
            memberships_by_key[row["canonical_key"]].append(row["query_id"])
        unsupported_rows = []
        for key in sorted(unsupported_by_key, key=canonical_sort_key):
            evidence = unsupported_by_key[key]
            query_ids = sorted(set(memberships_by_key.get(key, [])))
            unsupported_rows.append(
                {
                    **evidence,
                    "current_selected_manifest_membership_count": len(
                        query_ids
                    ),
                    "current_selected_manifest_query_ids": query_ids,
                    "current_selected_manifest_record_present": (
                        key in selected_keys
                    ),
                    "historical_only_relative_to_current_partial": (
                        key not in selected_keys
                    ),
                    "proposal_only": True,
                    "proposed_disposition": None,
                    "usable_as_membership_review_ledger": False,
                    "decision_import_allowed": False,
                    "scoring_input_allowed": False,
                    "exclusion_performed": False,
                    "membership_disposition_authority": False,
                    "ratification_authority": False,
                    "scoring_authority": False,
                    "scoring_weight": 0,
                }
            )
        for index, row in enumerate(unsupported_rows, 1):
            validate_unsupported_authority_contract(
                row, label=f"generated unsupported row {index}"
            )

        tmp_dir = out_root / f".{prefill_id}.tmp.{os.getpid()}"
        tmp_dir.mkdir(parents=True, exist_ok=False)
        records_path = tmp_dir / "selected_records.ndjson"
        memberships_path = tmp_dir / "selected_memberships.ndjson"
        proposals_path = tmp_dir / "membership_review_proposals.ndjson"
        unsupported_path = tmp_dir / "unsupported_amendments.ndjson"
        record_count = write_ndjson(records_path, records)
        membership_count = write_ndjson(memberships_path, memberships)
        proposal_count = write_ndjson(proposals_path, proposals)
        unsupported_count = write_ndjson(
            unsupported_path, unsupported_rows
        )
        outputs = {
            "selected_records": file_item(
                records_path, record_count=record_count
            ),
            "selected_memberships": file_item(
                memberships_path, record_count=membership_count
            ),
            "membership_review_proposals": file_item(
                proposals_path, record_count=proposal_count
            ),
            "unsupported_amendments": file_item(
                unsupported_path, record_count=unsupported_count
            ),
        }
        # Paths in the immutable manifest refer to the final directory.
        for item in outputs.values():
            item["path"] = str((out_dir / Path(item["path"]).name).resolve())
        buckets = Counter(row["review_bucket"] for row in proposals)
        proposal_classes = Counter(
            row["proposal_class"] for row in proposals
        )
        result = {
            "schema": "legislation-partial-review-prefill-manifest-v1",
            "status": "PASS_NON_RATIFIED_PARTIAL",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "prefill_id": prefill_id,
            "bindings": bindings,
            "primary_discovery": PRIMARY,
            "quorum_used": False,
            "partial_discovery_only": True,
            "stale_complete_manifest_authority_used": False,
            "completed_query_count": len(strict_manifest["queries"]),
            "required_query_count": len(
                strict_manifest["selected_query_ids"]
            ),
            "query_artifacts": query_receipts,
            "selected_record_count": len(records),
            "selected_membership_count": len(memberships),
            "proposal_count": len(proposals),
            "all_selected_memberships_have_proposals": (
                len(proposals) == len(memberships)
            ),
            "proposal_bucket_counts": dict(sorted(buckets.items())),
            "proposal_class_counts": dict(
                sorted(proposal_classes.items())
            ),
            "primary_specific_title_alias_membership_count": sum(
                bool(row["matched_specific_title_aliases"])
                for row in proposals
            ),
            "reference_only_specific_title_alias_membership_count": sum(
                bool(
                    row[
                        "matched_specific_title_alias_references_"
                        "non_dispositive"
                    ]
                )
                for row in proposals
            ),
            "recall_only_miss_visible_membership_count": sum(
                row["recall_only_miss_visibility_preserved"]
                for row in proposals
            ),
            "title_probe_parent_category_confirmation_collision_count": (
                buckets[
                    "title_probe_parent_category_confirmation_collision"
                ]
            ),
            "ambiguous_collision_with_independent_official_nexus_count": (
                buckets[
                    "ambiguous_short_act_collision_with_independent_"
                    "official_nexus"
                ]
            ),
            "official_metadata_join_count": sum(
                row["official_evidence_available"] for row in proposals
            ),
            "unsupported_selected_membership_count": sum(
                row["unsupported_amendment"] for row in proposals
            ),
            "unsupported_amendment_count": len(unsupported_rows),
            "unsupported_amendments_in_current_partial_count": sum(
                row["current_selected_manifest_record_present"]
                for row in unsupported_rows
            ),
            "unsupported_amendments_historical_only_count": sum(
                row["historical_only_relative_to_current_partial"]
                for row in unsupported_rows
            ),
            "all_sidecar_unsupported_amendments_visible": (
                len(unsupported_rows) == len(unsupported_by_key)
            ),
            "record_logical_sha256": stable_json_sha256(records),
            "membership_logical_sha256": stable_json_sha256(memberships),
            "inputs": {
                "selected_partial_manifest": file_item(manifest_path),
                "registry": file_item(registry_path),
                "billstatus_sidecar_manifest": file_item(
                    sidecar_manifest_path
                ),
                "billstatus_sidecar_outputs": sidecar["outputs"],
                "specificity_logic_producer": file_item(
                    specificity_producer
                ),
            },
            "outputs": outputs,
            "candidate_membership_review_overwritten": False,
            "candidate_creation_performed": False,
            "candidate_exclusion_performed": False,
            "semantic_exclusion_performed": False,
            "ratified_decision_performed": False,
            "automatic_scoring_performed": False,
            "sync_or_promotion_performed": False,
            "production_write_performed": False,
            "absence_is_neutral": True,
            "review_prefill_only": True,
            "human_ratification_required": True,
            "downstream_consumption_allowed": False,
            "decision_import_allowed": False,
            "scoring_input_allowed": False,
            "membership_disposition_authority": False,
            "ratification_authority": False,
            "scoring_authority": False,
            "scoring_weight": 0,
            "acceptance_passed": False,
        }
        write_json(tmp_dir / "manifest.json", result)
        out_root.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_dir, out_dir)
        created = True

    if current_path is not None:
        current_path = current_path.resolve()
        current_path.relative_to(ROOT)
        pointer = {
            "schema": "legislation-partial-review-prefill-pointer-v1",
            "prefill_id": prefill_id,
            "prefill_dir": str(out_dir),
            "manifest": file_item(out_dir / "manifest.json"),
            "selected_partial_manifest": file_item(manifest_path),
            "review_prefill_only": True,
            "human_ratification_required": True,
            "downstream_consumption_allowed": False,
            "decision_import_allowed": False,
            "scoring_input_allowed": False,
            "membership_disposition_authority": False,
            "ratification_authority": False,
            "scoring_authority": False,
            "sync_or_promotion_performed": False,
        }
        tmp_pointer = current_path.with_name(f".{current_path.name}.{os.getpid()}.tmp")
        try:
            write_json(tmp_pointer, pointer)
            os.replace(tmp_pointer, current_path)
        finally:
            tmp_pointer.unlink(missing_ok=True)
    return out_dir, result, created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument(
        "--sidecar-manifest",
        type=Path,
        help="Explicit immutable sidecar manifest; otherwise use pointer.",
    )
    parser.add_argument(
        "--sidecar-pointer", type=Path, default=DEFAULT_SIDECAR_POINTER
    )
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--no-current-pointer", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = (
        args.manifest.resolve()
        if args.manifest
        else selected_manifest_from_checkpoint(args.checkpoint)
    )
    sidecar = (
        args.sidecar_manifest.resolve()
        if args.sidecar_manifest
        else sidecar_manifest_from_pointer(args.sidecar_pointer)
    )
    out_dir, report, created = build_prefill(
        manifest_path=manifest,
        registry_path=args.registry,
        sidecar_manifest_path=sidecar,
        out_root=args.out_root,
        current_path=None if args.no_current_pointer else args.current,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "created": created,
                "prefill_id": report["prefill_id"],
                "out_dir": str(out_dir),
                "completed_query_count": report["completed_query_count"],
                "required_query_count": report["required_query_count"],
                "selected_record_count": report["selected_record_count"],
                "selected_membership_count": report[
                    "selected_membership_count"
                ],
                "proposal_bucket_counts": report[
                    "proposal_bucket_counts"
                ],
                "unsupported_amendment_count": report[
                    "unsupported_amendment_count"
                ],
                "acceptance_passed": report["acceptance_passed"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

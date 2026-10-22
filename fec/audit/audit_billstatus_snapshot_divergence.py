#!/usr/bin/env python3
"""Audit, without network access, divergence in BILLSTATUS snapshot inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
import sys

FEC_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(FEC_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(FEC_BOOTSTRAP))

from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS
try:
    from lane_file_io import atomic_write_bytes, iter_ndjson_raw_lines
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes, iter_ndjson_raw_lines


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "billstatus-snapshot-divergence-audit-v1"
HISTORICAL_ACCEPTANCE_SCHEMA = (
    "historical-completed-membership-acceptance-v1"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AuditError(ValueError):
    """Raised when local evidence cannot support a lossless audit."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def require_sha256(value: Any, message: str) -> str:
    normalized = str(value or "").strip().lower()
    require(bool(SHA256_RE.fullmatch(normalized)), message)
    return normalized


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON object required: {path}")
    return value


def iter_ndjson(path: Path) -> Iterable[tuple[int, str, dict[str, Any]]]:
    # ``iter_ndjson_raw_lines`` strips a leading UTF-8 BOM so a PowerShell
    # ``Out-File -Encoding utf8`` copy parses, and so the per-row receipt hashes
    # the JSON payload rather than the encoding marker.
    for line_number, raw in iter_ndjson_raw_lines(path):
        if not raw.strip():
            continue
        value = json.loads(raw.decode("utf-8"))
        require(isinstance(value, dict), f"NDJSON object required: {path}")
        yield (
            line_number,
            BILLSTATUS.sha256_bytes(raw.rstrip(b"\r\n")),
            value,
        )


def ndjson_logical_array_receipt(path: Path) -> tuple[int, str]:
    """Hash NDJSON rows as the collector hashes JSON.stringify(rows)."""
    digest = hashlib.sha256()
    digest.update(b"[")
    count = 0
    for _line_number, raw in iter_ndjson_raw_lines(path):
        encoded = raw.rstrip(b"\r\n")
        if not encoded.strip():
            continue
        value = json.loads(encoded.decode("utf-8"))
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if count:
            digest.update(b",")
        digest.update(encoded)
        count += 1
    digest.update(b"]")
    return count, digest.hexdigest()


def strict_key_evidence(
    strict_manifest_path: Path,
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    strict = strict_manifest_path.resolve()
    manifest = read_object(strict)
    require(
        manifest.get("primary_instrument") == BILLSTATUS.PRIMARY_DISCOVERY,
        "strict manifest primary instrument drift",
    )
    require(manifest.get("quorum_used") is False, "Quorum is forbidden")
    selected = manifest.get("selected_query_ids")
    queries = manifest.get("queries")
    require(isinstance(selected, list), "strict selected-query list missing")
    require(isinstance(queries, list), "strict completed-query list missing")
    selected_set = set(selected)
    by_key: dict[str, list[str]] = defaultdict(list)
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in queries:
        require(isinstance(entry, dict), "strict query entry is malformed")
        query_id = entry.get("query_id")
        query_file = entry.get("query_file")
        require(
            isinstance(query_id, str)
            and query_id in selected_set
            and query_id not in seen,
            "strict completed-query identity drift",
        )
        require(isinstance(query_file, str) and query_file, "query file missing")
        path = (strict.parent / query_file).resolve()
        try:
            path.relative_to(strict.parent)
        except ValueError as error:
            raise AuditError(f"query path escapes strict run: {query_id}") from error
        artifact = read_object(path)
        records = artifact.get("records")
        require(
            artifact.get("schema") == "congress-advanced-search-query-v1"
            and artifact.get("query_id") == query_id
            and artifact.get("registry_sha256")
            == manifest.get("registry_sha256")
            and isinstance(records, list),
            f"strict query artifact identity drift: {query_id}",
        )
        logical_hash = BILLSTATUS.stable_json_sha256(records)
        require(
            logical_hash
            == artifact.get("records_sha256")
            == entry.get("records_sha256"),
            f"strict query records hash drift: {query_id}",
        )
        require(
            len(records)
            == artifact.get("result_total")
            == artifact.get("unique_record_count")
            == entry.get("result_total")
            == entry.get("unique_record_count"),
            f"strict query record count drift: {query_id}",
        )
        validation = artifact.get("validation")
        require(
            isinstance(validation, dict)
            and validation.get("exact_result_total") is True
            and validation.get("unique_result_total") is True
            and validation.get("every_page_exact") is True,
            f"strict query exact reconciliation failed: {query_id}",
        )
        local_keys: set[str] = set()
        for record in records:
            require(isinstance(record, dict), f"query record malformed: {query_id}")
            key = record.get("canonical_key")
            require(
                isinstance(key, str) and key and key not in local_keys,
                f"query key invalid or duplicate: {query_id}",
            )
            local_keys.add(key)
            by_key[key].append(query_id)
        receipts.append(
            {
                "query_id": query_id,
                **BILLSTATUS.file_receipt(path),
                "record_count": len(records),
                "records_sha256": logical_hash,
            }
        )
        seen.add(query_id)
    return dict(by_key), receipts


def manifest_query_versions(
    manifest: dict[str, Any],
    *,
    manifest_sha256: str,
    scope: str,
    require_complete: bool,
) -> dict[str, dict[str, Any]]:
    require(
        manifest.get("primary_instrument") == BILLSTATUS.PRIMARY_DISCOVERY,
        f"{scope} manifest primary instrument drift",
    )
    require(manifest.get("quorum_used") is False, "Quorum is forbidden")
    if require_complete:
        require(
            manifest.get("status") == "complete",
            f"{scope} manifest is not complete",
        )
    registry_sha256 = require_sha256(
        manifest.get("registry_sha256"),
        f"{scope} manifest registry hash is missing",
    )
    selected = manifest.get("selected_query_ids")
    queries = manifest.get("queries")
    require(isinstance(selected, list), f"{scope} selected-query list missing")
    require(isinstance(queries, list), f"{scope} query list missing")
    selected_ids = {
        str(query_id)
        for query_id in selected
        if isinstance(query_id, str) and query_id
    }
    require(
        len(selected_ids) == len(selected),
        f"{scope} selected-query identities are invalid or duplicate",
    )
    versions: dict[str, dict[str, Any]] = {}
    for entry in queries:
        require(isinstance(entry, dict), f"{scope} query entry is malformed")
        query_id = entry.get("query_id")
        require(
            isinstance(query_id, str)
            and query_id in selected_ids
            and query_id not in versions,
            f"{scope} completed-query identity drift",
        )
        records_sha256 = require_sha256(
            entry.get("records_sha256"),
            f"{scope} query records hash missing: {query_id}",
        )
        result_total = entry.get("result_total")
        unique_record_count = entry.get("unique_record_count")
        require(
            isinstance(result_total, int)
            and not isinstance(result_total, bool)
            and isinstance(unique_record_count, int)
            and not isinstance(unique_record_count, bool)
            and result_total == unique_record_count
            and result_total >= 0,
            f"{scope} query total is not exact: {query_id}",
        )
        versions[query_id] = {
            "scope": scope,
            "manifest_sha256": manifest_sha256,
            "registry_sha256": registry_sha256,
            "query_id": query_id,
            "query_records_sha256": records_sha256,
            "query_result_total": result_total,
            "query_unique_record_count": unique_record_count,
            "query_file": entry.get("query_file"),
            "query_completed": True,
        }
    if require_complete:
        require(
            set(versions) == selected_ids,
            f"{scope} selected/completed query partition is incomplete",
        )
    else:
        require(
            set(versions) <= selected_ids,
            f"{scope} completed queries escape the selected set",
        )
    return versions


def build_historical_acceptance_rows(
    unproven: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create one lossless, non-scoring acceptance row per old membership."""

    rows: list[dict[str, Any]] = []
    for candidate in unproven:
        key = str(candidate.get("canonical_key") or "")
        memberships = candidate.get("membership_evidence") or []
        reviews = candidate.get("membership_review_evidence") or []
        exclusions = (
            candidate.get("reviewed_false_positive_exclusion_evidence") or []
        )
        require(memberships, f"historical candidate has no membership: {key}")
        for membership in memberships:
            query_id = str(membership.get("query_id") or "")
            matching_reviews = [
                row for row in reviews if row.get("query_id") == query_id
            ]
            require(
                len(matching_reviews) == 1,
                "historical membership must have exactly one review row: "
                f"{key}/{query_id}",
            )
            review = matching_reviews[0]
            disposition = review.get("disposition")
            require(
                disposition in {"unresolved", "exclude_false_positive", "include"},
                f"historical membership has invalid review disposition: "
                f"{key}/{query_id}",
            )
            matching_exclusions = [
                row for row in exclusions if row.get("query_id") == query_id
            ]
            if disposition == "exclude_false_positive":
                require(
                    len(matching_exclusions) == 1,
                    "historical exclusion lacks one evidence receipt: "
                    f"{key}/{query_id}",
                )
            else:
                require(
                    not matching_exclusions,
                    "historical non-exclusion has exclusion evidence: "
                    f"{key}/{query_id}",
                )
            ratified = review.get("official_relevance_ratified") is True
            if ratified:
                require(
                    disposition == "include"
                    and review.get("decision_source")
                    in {"explicit_review_decision", "human_review"}
                    and str(review.get("official_decision_source_url") or "")
                    .startswith("https://www.congress.gov/")
                    and bool(
                        SHA256_RE.fullmatch(
                            str(
                                review.get(
                                    "official_decision_evidence_sha256"
                                )
                                or ""
                            ).lower()
                        )
                    )
                    and bool(str(review.get("reviewer") or "").strip())
                    and bool(str(review.get("reviewed_at") or "").strip()),
                    "historical inclusion ratification is not backed by "
                    f"specific official evidence: {key}/{query_id}",
                )
            current_scoring_eligible = (
                disposition == "include" and ratified
            )
            acceptance_state = {
                "unresolved": (
                    "historical_visible_unresolved_non_scoring"
                ),
                "exclude_false_positive": (
                    "historical_visible_official_exclusion_non_scoring"
                ),
                "include": (
                    "historical_visible_officially_ratified_scoring"
                    if current_scoring_eligible
                    else "historical_visible_include_unratified_non_scoring"
                ),
            }[str(disposition)]
            provenance = membership.get("membership_query_provenance")
            require(
                isinstance(provenance, dict)
                and provenance.get("historical_membership_only") is True
                and provenance.get("current_membership_present") is False,
                f"historical/current query provenance missing: "
                f"{key}/{query_id}",
            )
            rows.append(
                {
                    "schema": HISTORICAL_ACCEPTANCE_SCHEMA,
                    "canonical_key": key,
                    "query_id": query_id,
                    "historical_membership_only": True,
                    "candidate_retained": True,
                    "review_disposition": disposition,
                    "acceptance_state": acceptance_state,
                    "official_relevance_ratified": ratified,
                    "official_relevance_ratification_required_for_scoring": True,
                    "current_scoring_evidence_eligible": (
                        current_scoring_eligible
                    ),
                    "membership_query_provenance": provenance,
                    "historical_membership_receipt": {
                        "line": membership.get("line"),
                        "line_sha256": membership.get("line_sha256"),
                        "source_page": membership.get("source_page"),
                        "source_rank": membership.get("source_rank"),
                        "result_url": membership.get("result_url"),
                    },
                    "review_receipt": {
                        key: review.get(key)
                        for key in (
                            "line",
                            "line_sha256",
                            "triage",
                            "disposition",
                            "decision_source",
                            "candidate_retained_in_discovery_corpus",
                            "review_bucket",
                            "official_decision_source_url",
                            "official_decision_evidence_sha256",
                            "reviewer",
                            "reviewed_at",
                        )
                    },
                    "exclusion_receipt": (
                        matching_exclusions[0]
                        if matching_exclusions
                        else None
                    ),
                }
            )
    rows.sort(key=lambda row: (row["canonical_key"], row["query_id"]))
    natural_keys = [
        (row["canonical_key"], row["query_id"]) for row in rows
    ]
    require(
        len(natural_keys) == len(set(natural_keys)),
        "historical acceptance membership keys are duplicate",
    )
    return rows


def evidence_index(
    path: Path | None,
    keys: set[str],
    *,
    fields: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if path is None or not path.is_file():
        return {}
    for line, line_hash, row in iter_ndjson(path):
        key = row.get("canonical_key")
        if key not in keys:
            continue
        found[str(key)].append(
            {
                "line": line,
                "line_sha256": line_hash,
                **{field: row.get(field) for field in fields},
            }
        )
    return dict(found)


def raw_page_evidence(
    run_dir: Path,
    key: str,
    memberships: list[dict[str, Any]],
    cache: dict[Path, tuple[dict[str, Any], set[str]]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    checked: set[tuple[str, int]] = set()
    for membership in memberships:
        query_id = membership.get("query_id")
        page_number = membership.get("source_page")
        if not (
            isinstance(query_id, str)
            and isinstance(page_number, int)
            and page_number > 0
        ):
            continue
        identity = (query_id, page_number)
        if identity in checked:
            continue
        checked.add(identity)
        page_path = (
            run_dir
            / "raw"
            / "pages"
            / query_id
            / f"page_{page_number:03d}.json"
        ).resolve()
        row: dict[str, Any] = {
            "query_id": query_id,
            "source_page": page_number,
            "path": str(page_path),
            "file_present": page_path.is_file(),
            "candidate_present_in_current_page": False,
        }
        if page_path.is_file():
            if page_path not in cache:
                page = read_object(page_path)
                records = page.get("records")
                require(isinstance(records, list), f"raw page records missing: {page_path}")
                keys = {
                    str(record.get("canonical_key"))
                    for record in records
                    if isinstance(record, dict)
                }
                cache[page_path] = (BILLSTATUS.file_receipt(page_path), keys)
            receipt, page_keys = cache[page_path]
            row.update(receipt)
            row["candidate_present_in_current_page"] = key in page_keys
        evidence.append(row)
    return evidence


def build_audit(
    *,
    records_path: Path,
    strict_manifest_path: Path,
    memberships_path: Path | None = None,
    review_ledger_path: Path | None = None,
    false_positive_exclusions_path: Path | None = None,
    gap_ledger_path: Path | None = None,
    aggregate_manifest_path: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    records_with_receipts = list(iter_ndjson(records_path))
    records = [row for _, _, row in records_with_receipts]
    keys = [str(row.get("canonical_key")) for row in records]
    require(
        all(key and key != "None" for key in keys)
        and len(keys) == len(set(keys)),
        "aggregate candidate keys are missing or duplicate",
    )
    strict_by_key, query_receipts = strict_key_evidence(strict_manifest_path)
    strict_manifest = read_object(strict_manifest_path)
    strict_manifest_receipt = BILLSTATUS.file_receipt(strict_manifest_path)
    current_query_versions = manifest_query_versions(
        strict_manifest,
        manifest_sha256=strict_manifest_receipt["sha256"],
        scope="current_selected_strict_manifest",
        require_complete=False,
    )
    actual_set = set(keys)
    proven_set = actual_set & set(strict_by_key)
    unproven_set = actual_set - set(strict_by_key)
    aggregate_membership_query_ids = {
        str(query_id)
        for record in records
        for field in (
            "matched_scoring_categories",
            "matched_monitor_categories",
            "matched_coverage_probes",
        )
        for query_id in (record.get(field) or [])
    }

    memberships = evidence_index(
        memberships_path,
        unproven_set,
        fields=(
            "query_id",
            "record_kind",
            "scoring_category",
            "source_page",
            "source_rank",
            "result_url",
        ),
    )
    review = evidence_index(
        review_ledger_path,
        unproven_set,
        fields=(
            "query_id",
            "triage",
            "disposition",
            "decision_source",
            "candidate_retained_in_discovery_corpus",
            "review_bucket",
            "official_relevance_ratified",
            "official_decision_source_url",
            "official_decision_evidence_sha256",
            "reviewer",
            "reviewed_at",
        ),
    )
    false_positive_exclusions = evidence_index(
        false_positive_exclusions_path,
        unproven_set,
        fields=(
            "query_id",
            "disposition",
            "decision_source",
            "reason",
            "reason_code",
            "official_decision_source_url",
            "reviewer",
            "reviewed_at",
        ),
    )
    gaps = evidence_index(
        gap_ledger_path,
        unproven_set,
        fields=("gap_kind", "disposition", "explanation"),
    )

    aggregate_receipt: dict[str, Any] | None = None
    historical_query_versions: dict[str, dict[str, Any]] = {}
    aggregate_union_current = False
    aggregate_memberships_current = False
    if aggregate_manifest_path and aggregate_manifest_path.is_file():
        aggregate = read_object(aggregate_manifest_path)
        aggregate_manifest_file_receipt = BILLSTATUS.file_receipt(
            aggregate_manifest_path
        )
        historical_query_versions = manifest_query_versions(
            aggregate,
            manifest_sha256=aggregate_manifest_file_receipt["sha256"],
            scope="historical_completed_manifest",
            require_complete=True,
        )
        union = aggregate.get("union")
        if isinstance(union, dict):
            aggregate_rows_hash = BILLSTATUS.stable_json_sha256(records)
            aggregate_union_current = (
                union.get("unique_record_count") == len(records)
                and union.get("records_sha256") == aggregate_rows_hash
                and Path(str(union.get("records_file") or "")).name
                == records_path.name
            )
            membership_count: int | None = None
            membership_hash: str | None = None
            if memberships_path and memberships_path.is_file():
                membership_count, membership_hash = (
                    ndjson_logical_array_receipt(memberships_path)
                )
                aggregate_memberships_current = (
                    union.get("membership_count") == membership_count
                    and union.get("memberships_sha256") == membership_hash
                    and Path(str(union.get("memberships_file") or "")).name
                    == memberships_path.name
                )
            aggregate_receipt = {
                **aggregate_manifest_file_receipt,
                "status": aggregate.get("status"),
                "primary_instrument": aggregate.get("primary_instrument"),
                "quorum_used": aggregate.get("quorum_used"),
                "registry_sha256": aggregate.get("registry_sha256"),
                "declared_record_count": union.get("unique_record_count"),
                "declared_records_sha256": union.get("records_sha256"),
                "computed_records_sha256": aggregate_rows_hash,
                "union_receipt_matches_current_corpus": aggregate_union_current,
                "declared_membership_count": union.get("membership_count"),
                "declared_memberships_sha256": union.get(
                    "memberships_sha256"
                ),
                "computed_membership_count": membership_count,
                "computed_memberships_sha256": membership_hash,
                "membership_receipt_matches_current_ledger": (
                    aggregate_memberships_current
                ),
            }
    cumulative_aggregate_current = (
        aggregate_union_current and aggregate_memberships_current
    )

    proven: list[dict[str, Any]] = []
    unproven: list[dict[str, Any]] = []
    page_cache: dict[Path, tuple[dict[str, Any], set[str]]] = {}
    for line, line_hash, record in records_with_receipts:
        key = str(record["canonical_key"])
        common = {
            "canonical_key": key,
            "aggregate_line": line,
            "aggregate_line_sha256": line_hash,
            "matched_scoring_categories": list(
                record.get("matched_scoring_categories") or []
            ),
            "matched_monitor_categories": list(
                record.get("matched_monitor_categories") or []
            ),
            "matched_coverage_probes": list(
                record.get("matched_coverage_probes") or []
            ),
            "candidate_retained": True,
            "semantic_exclusion_authorized": False,
        }
        if key in proven_set:
            proven.append(
                {
                    **common,
                    "strict_manifest_query_ids": sorted(strict_by_key[key]),
                    "disposition": "proven_by_selected_strict_query_artifact",
                }
            )
            continue
        membership_rows = memberships.get(key, [])
        enriched_membership_rows: list[dict[str, Any]] = []
        for membership in membership_rows:
            query_id = str(membership.get("query_id") or "")
            historical_version = historical_query_versions.get(query_id)
            current_version = current_query_versions.get(query_id)
            require(
                historical_version is not None,
                f"historical membership query version missing: "
                f"{key}/{query_id}",
            )
            require(
                current_version is not None,
                f"current query version missing for historical membership: "
                f"{key}/{query_id}",
            )
            enriched_membership_rows.append(
                {
                    **membership,
                    "membership_query_provenance": {
                        "query_id": query_id,
                        "historical_membership_only": True,
                        "historical_query_version": historical_version,
                        "current_query_version": current_version,
                        "current_membership_present": False,
                        "query_version_changed": (
                            historical_version["registry_sha256"]
                            != current_version["registry_sha256"]
                            or historical_version[
                                "query_records_sha256"
                            ]
                            != current_version["query_records_sha256"]
                        ),
                    },
                }
            )
        membership_rows = enriched_membership_rows
        review_rows = review.get(key, [])
        exclusion_rows = false_positive_exclusions.get(key, [])
        page_rows = raw_page_evidence(
            strict_manifest_path.resolve().parent,
            key,
            membership_rows,
            page_cache,
        )
        gap_rows = gaps.get(key, [])
        unproven.append(
            {
                **common,
                "strict_manifest_query_ids": [],
                "membership_evidence": membership_rows,
                "membership_review_evidence": review_rows,
                "reviewed_false_positive_exclusion_evidence": exclusion_rows,
                "coverage_gap_evidence": gap_rows,
                "raw_page_evidence": page_rows,
                "aggregate_manifest_union_receipt_applies": aggregate_union_current,
                "aggregate_manifest_membership_receipt_applies": (
                    aggregate_memberships_current
                ),
                "evidence_summary": {
                    "membership_found": bool(membership_rows),
                    "membership_review_found": bool(review_rows),
                    "reviewed_false_positive_exclusion_found": bool(
                        exclusion_rows
                    ),
                    "coverage_gap_found": bool(gap_rows),
                    "current_raw_page_hit_found": any(
                        row["candidate_present_in_current_page"]
                        for row in page_rows
                    ),
                    "aggregate_manifest_union_receipt_found": (
                        aggregate_union_current
                    ),
                    "aggregate_manifest_membership_receipt_found": (
                        aggregate_memberships_current
                    ),
                },
                "disposition": (
                    "historical_snapshot_proven_retained_current_query_"
                    "divergence_unresolved"
                    if cumulative_aggregate_current
                    else "unresolved_without_historical_aggregate_receipt"
                ),
            }
        )

    require(
        len(proven) + len(unproven) == len(records),
        "candidate audit partition is not lossless",
    )
    historical_acceptance = build_historical_acceptance_rows(unproven)
    expected_historical_membership_count = sum(
        len(row.get("membership_evidence") or []) for row in unproven
    )
    historical_acceptance_keys = {
        row["canonical_key"] for row in historical_acceptance
    }
    unresolved_non_scoring_count = sum(
        row["acceptance_state"]
        == "historical_visible_unresolved_non_scoring"
        for row in historical_acceptance
    )
    official_exclusion_non_scoring_count = sum(
        row["acceptance_state"]
        == "historical_visible_official_exclusion_non_scoring"
        for row in historical_acceptance
    )
    officially_ratified_scoring_count = sum(
        row["current_scoring_evidence_eligible"]
        for row in historical_acceptance
    )
    unratified_include_non_scoring_count = sum(
        row["acceptance_state"]
        == "historical_visible_include_unratified_non_scoring"
        for row in historical_acceptance
    )
    historical_acceptance_passed = (
        len(historical_acceptance)
        == expected_historical_membership_count
        and historical_acceptance_keys
        == {row["canonical_key"] for row in unproven}
        and all(row["candidate_retained"] for row in historical_acceptance)
        and all(
            not row["current_scoring_evidence_eligible"]
            or row["official_relevance_ratified"]
            for row in historical_acceptance
        )
    )
    require(
        historical_acceptance_passed,
        "historical membership acceptance partition did not close",
    )
    review_ledger_receipt = (
        BILLSTATUS.file_receipt(review_ledger_path)
        if review_ledger_path and review_ledger_path.is_file()
        else None
    )
    inputs = {
        "advanced_search_records": BILLSTATUS.file_receipt(records_path),
        "selected_strict_manifest": strict_manifest_receipt,
        "strict_query_artifacts": query_receipts,
        "memberships": (
            BILLSTATUS.file_receipt(memberships_path)
            if memberships_path and memberships_path.is_file()
            else None
        ),
        "membership_review": review_ledger_receipt,
        "reviewed_false_positive_exclusions": (
            BILLSTATUS.file_receipt(false_positive_exclusions_path)
            if (
                false_positive_exclusions_path
                and false_positive_exclusions_path.is_file()
            )
            else None
        ),
        "coverage_gap_ledger": (
            BILLSTATUS.file_receipt(gap_ledger_path)
            if gap_ledger_path and gap_ledger_path.is_file()
            else None
        ),
        "aggregate_manifest": aggregate_receipt,
    }
    manifest = {
        "schema": SCHEMA,
        "status": (
            "CUMULATIVE_PROVEN_WITH_CURRENT_QUERY_DIVERGENCE"
            if unproven and cumulative_aggregate_current
            else ("UNRESOLVED_DIVERGENCE" if unproven else "CLOSED")
        ),
        "snapshot_only": True,
        "primary_discovery": BILLSTATUS.PRIMARY_DISCOVERY,
        "quorum_used": False,
        "network_executed": False,
        "candidate_exclusions_performed": False,
        "candidate_deletions_performed": False,
        "sync_or_promotion_performed": False,
        "authoritative_for_discovery": False,
        "authoritative_for_semantic_exclusion": False,
        "inputs": inputs,
        "partition": {
            "all": len(records),
            "proven_by_selected_strict_query_artifacts": len(proven),
            "unproven_by_selected_strict_query_artifacts": len(unproven),
            "historical_aggregate_proven_and_retained": (
                len(unproven) if cumulative_aggregate_current else 0
            ),
            "unresolved_without_historical_aggregate_receipt": (
                0 if cumulative_aggregate_current else len(unproven)
            ),
            "closed": len(proven) + len(unproven) == len(records),
        },
        "selected_query_labels_absent_from_aggregate_membership_arrays": sorted(
            {
                receipt["query_id"] for receipt in query_receipts
            }
            - aggregate_membership_query_ids
        ),
        "historical_only_evidence_summary": {
            "memberships_by_query": dict(
                sorted(
                    {
                        query_id: sum(
                            1
                            for row in unproven
                            for evidence in row["membership_evidence"]
                            if evidence.get("query_id") == query_id
                        )
                        for query_id in {
                            str(evidence.get("query_id"))
                            for row in unproven
                            for evidence in row["membership_evidence"]
                        }
                    }.items()
                )
            ),
            "review_dispositions": dict(
                sorted(
                    {
                        disposition: sum(
                            1
                            for row in unproven
                            if any(
                                evidence.get("disposition") == disposition
                                for evidence in row[
                                    "membership_review_evidence"
                                ]
                            )
                        )
                        for disposition in {
                            str(evidence.get("disposition"))
                            for row in unproven
                            for evidence in row["membership_review_evidence"]
                        }
                    }.items()
                )
            ),
            "reviewed_false_positive_candidate_keys": [
                row["canonical_key"]
                for row in unproven
                if row["reviewed_false_positive_exclusion_evidence"]
            ],
        },
        "historical_membership_acceptance_gate": {
            "schema": HISTORICAL_ACCEPTANCE_SCHEMA,
            "passed": historical_acceptance_passed,
            "lossless_partition_closed": True,
            "candidate_count": len(historical_acceptance_keys),
            "membership_count": len(historical_acceptance),
            "historical_visible_count": len(historical_acceptance),
            "unresolved_non_scoring_count": unresolved_non_scoring_count,
            "official_exclusion_non_scoring_count": (
                official_exclusion_non_scoring_count
            ),
            "officially_ratified_scoring_count": (
                officially_ratified_scoring_count
            ),
            "unratified_include_non_scoring_count": (
                unratified_include_non_scoring_count
            ),
            "scoring_requires_official_relevance_ratification": True,
            "historical_membership_can_score_without_ratification": False,
            "historical_manifest_sha256": (
                aggregate_receipt.get("sha256")
                if aggregate_receipt
                else None
            ),
            "current_manifest_sha256": strict_manifest_receipt["sha256"],
            "historical_registry_sha256": (
                aggregate_receipt.get("registry_sha256")
                if aggregate_receipt
                else None
            ),
            "current_registry_sha256": strict_manifest.get(
                "registry_sha256"
            ),
            "review_ledger_sha256": (
                review_ledger_receipt["sha256"]
                if review_ledger_receipt
                else None
            ),
        },
        "root_cause_classification": {
            "classification": (
                "narrowed_live_query_snapshot_with_lossless_historical_"
                "candidate_retention"
            ),
            "historical_query_definitions_include_removed_ambiguous_aliases": (
                True
            ),
            "selected_probe_memberships_not_yet_rebuilt_into_aggregate": (
                bool(
                    {
                        receipt["query_id"] for receipt in query_receipts
                    }
                    - aggregate_membership_query_ids
                )
            ),
            "inference_from_hash_bound_local_artifacts": True,
        },
        "unresolved_disposition": (
            "Retain every candidate. Historical aggregate proof covers the "
            "selected-query-unproven partition; the current query divergence "
            "and absent probe membership labels require a cumulative rebuild. "
            "Neither condition is exclusion evidence."
        ),
    }
    return manifest, proven, unproven


def atomic_write(path: Path, body: bytes) -> None:
    # Was ``path.with_name(path.name + ".tmp")``: a fixed scratch name that two
    # concurrent audits writing the same output silently clobbered, and that
    # OneDrive syncs and conflict-copies.  ``atomic_write_bytes`` is unique per
    # process and per call, fsync'd before publication, and retries the publish
    # through the OneDrive/AV lock window.
    atomic_write_bytes(path, body)


def write_audit(
    out_dir: Path,
    manifest: dict[str, Any],
    proven: list[dict[str, Any]],
    unproven: list[dict[str, Any]],
) -> Path:
    proven_path = out_dir / "proven_candidates.ndjson"
    unproven_path = out_dir / "unproven_candidates.ndjson"
    historical_acceptance_path = (
        out_dir / "historical_membership_acceptance.ndjson"
    )
    audit_path = out_dir / "divergence_audit.json"
    historical_acceptance = build_historical_acceptance_rows(unproven)
    atomic_write(
        proven_path,
        b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
            for row in proven
        ),
    )
    atomic_write(
        unproven_path,
        b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
            for row in unproven
        ),
    )
    atomic_write(
        historical_acceptance_path,
        b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
            for row in historical_acceptance
        ),
    )
    final = {
        **manifest,
        "outputs": {
            "proven_candidates": {
                **BILLSTATUS.file_receipt(proven_path),
                "record_count": len(proven),
            },
            "unproven_candidates": {
                **BILLSTATUS.file_receipt(unproven_path),
                "record_count": len(unproven),
            },
            "historical_membership_acceptance": {
                **BILLSTATUS.file_receipt(historical_acceptance_path),
                "record_count": len(historical_acceptance),
            },
        },
    }
    atomic_write(
        audit_path,
        json.dumps(final, indent=2, ensure_ascii=False).encode("utf-8") + b"\n",
    )
    return audit_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--strict-manifest", type=Path, required=True)
    parser.add_argument("--memberships", type=Path)
    parser.add_argument("--review-ledger", type=Path)
    parser.add_argument("--false-positive-exclusions", type=Path)
    parser.add_argument("--gap-ledger", type=Path)
    parser.add_argument("--aggregate-manifest", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest, proven, unproven = build_audit(
        records_path=args.records,
        strict_manifest_path=args.strict_manifest,
        memberships_path=args.memberships,
        review_ledger_path=args.review_ledger,
        false_positive_exclusions_path=args.false_positive_exclusions,
        gap_ledger_path=args.gap_ledger,
        aggregate_manifest_path=args.aggregate_manifest,
    )
    audit_path = write_audit(args.out_dir, manifest, proven, unproven)
    print(
        json.dumps(
            {
                "audit": str(audit_path),
                **manifest["partition"],
                "network_executed": False,
                "sync_or_promotion_performed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

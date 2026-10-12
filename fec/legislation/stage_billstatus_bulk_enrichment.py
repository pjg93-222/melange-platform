#!/usr/bin/env python3
"""Plan and validate staged GovInfo BILLSTATUS enrichment.

This module never performs network requests. Congress.gov Advanced Search
remains the only discovery source; BILLSTATUS records may only enrich keys
already present in that lossless discovery corpus.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

try:
    from lane_file_io import iter_ndjson_raw_lines
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import iter_ndjson_raw_lines


ROOT = Path(__file__).resolve().parents[2]
PRIMARY_DISCOVERY = "Congress.gov Advanced Search - Legislation"
OFFICIAL_BASE = "https://www.govinfo.gov/bulkdata/BILLSTATUS"
SITEMAP_INDEX = (
    "https://www.govinfo.gov/sitemap/bulkdata/BILLSTATUS/sitemapindex.xml"
)
CONGRESSES = (116, 117, 118, 119)
TOP_LEVEL_TYPES = (
    "hr",
    "hres",
    "hjres",
    "hconres",
    "s",
    "sres",
    "sjres",
    "sconres",
)
NESTED_ONLY_TYPES = ("hamdt", "samdt")
DISCOVERY_TYPES = TOP_LEVEL_TYPES + NESTED_ONLY_TYPES
FORMAT_VERSION_ALLOWLIST = ("3.0.0",)
MAX_XML_BYTES = 64 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """Raised when a staged source fails the enrichment contract."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def read_ndjson_with_provenance(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # ``iter_ndjson_raw_lines`` strips a leading UTF-8 BOM from line 1 so a
    # PowerShell-written copy parses, and so ``_input_line_sha256`` hashes the
    # JSON payload rather than the encoding marker.
    for line_number, raw in iter_ndjson_raw_lines(path):
        if not raw.strip():
            continue
        row = json.loads(raw.decode("utf-8"))
        row["_input_line"] = line_number
        row["_input_line_sha256"] = sha256_bytes(raw.rstrip(b"\r\n"))
        rows.append(row)
    return rows


def stable_json_sha256(value: Any) -> str:
    """Match the collector's SHA-256(JSON.stringify(value)) receipts."""
    return sha256_bytes(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    )


def ndjson_logical_array_receipt(path: Path) -> tuple[int, str]:
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


def strict_manifest_union_snapshot(
    strict_manifest_path: Path,
    discovery_rows: list[dict[str, Any]],
    *,
    cumulative_manifest_path: Path | None = None,
    cumulative_memberships_path: Path | None = None,
) -> dict[str, Any]:
    """Prove exact or cumulative closure around the selected query snapshot."""
    strict = strict_manifest_path.resolve()
    manifest = json.loads(strict.read_text(encoding="utf-8"))
    _require(isinstance(manifest, dict), "strict manifest is not an object")
    _require(
        manifest.get("primary_instrument") == PRIMARY_DISCOVERY,
        "strict manifest discovery instrument drift",
    )
    _require(
        manifest.get("quorum_used") is False,
        "strict manifest used forbidden Quorum",
    )
    selected = manifest.get("selected_query_ids")
    queries = manifest.get("queries")
    _require(
        isinstance(selected, list)
        and all(isinstance(value, str) and value for value in selected)
        and len(selected) == len(set(selected)),
        "strict manifest selected-query set is invalid",
    )
    _require(
        isinstance(queries, list) and queries,
        "strict manifest has no completed queries",
    )
    selected_set = set(selected)
    completed: set[str] = set()
    union: dict[str, dict[str, Any]] = {}
    query_receipts: list[dict[str, Any]] = []
    run_dir = strict.parent
    for manifest_query in queries:
        _require(
            isinstance(manifest_query, dict),
            "strict manifest query is not an object",
        )
        query_id = manifest_query.get("query_id")
        raw_query_file = manifest_query.get("query_file")
        _require(
            isinstance(query_id, str)
            and query_id in selected_set
            and query_id not in completed,
            "strict manifest completed-query set is invalid",
        )
        _require(
            isinstance(raw_query_file, str) and raw_query_file,
            f"strict query file is missing: {query_id}",
        )
        query_path = (run_dir / raw_query_file).resolve()
        try:
            query_path.relative_to(run_dir)
        except ValueError as error:
            raise ContractError(
                f"strict query path escapes run directory: {query_id}"
            ) from error
        _require(query_path.is_file(), f"strict query file is missing: {query_id}")
        artifact = json.loads(query_path.read_text(encoding="utf-8"))
        _require(
            isinstance(artifact, dict)
            and artifact.get("schema")
            == "congress-advanced-search-query-v1"
            and artifact.get("query_id") == query_id
            and artifact.get("registry_sha256")
            == manifest.get("registry_sha256"),
            f"strict query identity drift: {query_id}",
        )
        records = artifact.get("records")
        _require(
            isinstance(records, list),
            f"strict query records are missing: {query_id}",
        )
        record_keys = [
            row.get("canonical_key") if isinstance(row, dict) else None
            for row in records
        ]
        _require(
            all(isinstance(key, str) and key for key in record_keys)
            and len(record_keys) == len(set(record_keys)),
            f"strict query record keys are invalid: {query_id}",
        )
        logical_hash = stable_json_sha256(records)
        validation = artifact.get("validation")
        count = len(records)
        _require(
            artifact.get("records_sha256")
            == manifest_query.get("records_sha256")
            == logical_hash
            and artifact.get("result_total")
            == artifact.get("unique_record_count")
            == manifest_query.get("result_total")
            == manifest_query.get("unique_record_count")
            == count
            and isinstance(validation, dict)
            and validation.get("exact_result_total") is True
            and validation.get("unique_result_total") is True
            and validation.get("every_page_exact") is True,
            f"strict query reconciliation drift: {query_id}",
        )
        bucket = (
            "matched_coverage_probes"
            if artifact.get("record_kind") == "coverage_probe"
            else (
                "matched_scoring_categories"
                if artifact.get("scoring_category") is True
                else "matched_monitor_categories"
            )
        )
        for source in records:
            assert isinstance(source, dict)
            key = str(source["canonical_key"])
            if key not in union:
                row = dict(source)
                row.pop("source_query_id", None)
                row.pop("source_page", None)
                row.pop("source_rank", None)
                row["matched_scoring_categories"] = []
                row["matched_monitor_categories"] = []
                row["matched_coverage_probes"] = []
                union[key] = row
            union[key][bucket].append(query_id)
        query_receipts.append(
            {
                "query_id": query_id,
                **file_receipt(query_path),
                "record_count": count,
                "records_sha256": logical_hash,
            }
        )
        completed.add(query_id)

    for row in union.values():
        for bucket in (
            "matched_scoring_categories",
            "matched_monitor_categories",
            "matched_coverage_probes",
        ):
            row[bucket] = sorted(set(row[bucket]))
    actual_rows = [
        {
            key: value
            for key, value in row.items()
            if key not in {"_input_line", "_input_line_sha256"}
        }
        for row in discovery_rows
    ]
    actual_keys = [str(row.get("canonical_key")) for row in actual_rows]
    expected_keys = sorted(
        union,
        key=lambda key: (
            int(key.split(":")[0]),
            key.split(":")[1],
            int(key.split(":")[2]),
        ),
    )
    exact_union = actual_keys == expected_keys and all(
        actual_rows[index] == union[key]
        for index, key in enumerate(expected_keys)
    )
    cumulative_receipt: dict[str, Any] | None = None
    historical_only_count = 0
    if not exact_union:
        _require(
            cumulative_manifest_path is not None
            and cumulative_memberships_path is not None,
            "discovery candidates are not the exact strict-manifest query union",
        )
        cumulative = cumulative_manifest_path.resolve()
        memberships = cumulative_memberships_path.resolve()
        _require(
            cumulative.is_file() and memberships.is_file(),
            "cumulative aggregate evidence is missing",
        )
        aggregate = json.loads(cumulative.read_text(encoding="utf-8"))
        _require(isinstance(aggregate, dict), "cumulative manifest is invalid")
        aggregate_union = aggregate.get("union")
        _require(
            isinstance(aggregate_union, dict),
            "cumulative manifest union is missing",
        )
        aggregate_rows_hash = stable_json_sha256(actual_rows)
        raw_records_file = aggregate_union.get("records_file")
        _require(
            isinstance(raw_records_file, str) and raw_records_file,
            "cumulative aggregate records path is missing",
        )
        aggregate_records_path = (
            cumulative.parent / raw_records_file
        ).resolve()
        try:
            aggregate_records_path.relative_to(cumulative.parent)
        except ValueError as error:
            raise ContractError(
                "cumulative aggregate records path escapes run"
            ) from error
        _require(
            aggregate_records_path.is_file(),
            "cumulative aggregate records file is missing",
        )
        aggregate_record_count, aggregate_file_logical_hash = (
            ndjson_logical_array_receipt(aggregate_records_path)
        )
        membership_count, membership_hash = ndjson_logical_array_receipt(
            memberships
        )
        _require(
            aggregate_union.get("unique_record_count") == len(actual_rows)
            and aggregate_union.get("records_sha256")
            == aggregate_rows_hash
            == aggregate_file_logical_hash
            and aggregate_record_count == len(actual_rows),
            "cumulative aggregate candidate receipt drift",
        )
        _require(
            aggregate_union.get("membership_count") == membership_count
            and aggregate_union.get("memberships_sha256") == membership_hash
            and Path(
                str(aggregate_union.get("memberships_file") or "")
            ).name
            == memberships.name,
            "cumulative aggregate membership receipt drift",
        )
        _require(
            set(expected_keys).issubset(actual_keys),
            "selected strict query union has candidates absent from cumulative corpus",
        )
        historical_only_count = len(set(actual_keys) - set(expected_keys))
        cumulative_receipt = {
            "manifest": file_receipt(cumulative),
            "records": file_receipt(aggregate_records_path),
            "memberships": file_receipt(memberships),
            "record_count": len(actual_rows),
            "records_sha256": aggregate_rows_hash,
            "membership_count": membership_count,
            "memberships_sha256": membership_hash,
        }
    line_receipts = [
        {
            "canonical_key": row["canonical_key"],
            "line": row["_input_line"],
            "line_sha256": row["_input_line_sha256"],
        }
        for row in discovery_rows
    ]
    return {
        "closure_mode": (
            "exact_selected_strict_query_union"
            if exact_union
            else "cumulative_historical_aggregate_plus_selected_snapshot"
        ),
        "completed_query_count": len(queries),
        "query_artifacts": query_receipts,
        "record_count": len(actual_rows),
        "selected_strict_candidate_count": len(expected_keys),
        "historical_snapshot_only_retained_count": historical_only_count,
        "cumulative_aggregate": cumulative_receipt,
        "canonical_keys_sha256": stable_json_sha256(actual_keys),
        "aggregate_rows_sha256": stable_json_sha256(actual_rows),
        "advanced_search_line_receipts_sha256": stable_json_sha256(
            line_receipts
        ),
    }


def canonical_key(congress: int, measure_type: str, number: int) -> str:
    return f"{congress}:{measure_type.lower()}:{number}"


def expected_record_url(congress: int, measure_type: str, number: int) -> str:
    return (
        f"{OFFICIAL_BASE}/{congress}/{measure_type}/"
        f"BILLSTATUS-{congress}{measure_type}{number}.xml"
    )


def expected_sitemap_url(congress: int, measure_type: str) -> str:
    return (
        "https://www.govinfo.gov/sitemap/bulkdata/BILLSTATUS/"
        f"{congress}{measure_type}/sitemap.xml"
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _iso8601(value: Any, field: str) -> str:
    text = str(value or "").strip()
    _require(bool(text), f"missing {field}")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"invalid {field}: {text}") from error
    return text


def _text(parent: ET.Element, *paths: str) -> str | None:
    for path in paths:
        node = parent.find(path)
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    return None


def _member(item: ET.Element, *, sponsor_container: bool) -> dict[str, Any]:
    original_text = _text(item, "isOriginalCosponsor")
    if original_text is None:
        original = None
    elif original_text.lower() == "true":
        original = True
    elif original_text.lower() == "false":
        original = False
    else:
        raise ContractError(
            f"invalid isOriginalCosponsor value: {original_text}"
        )
    withdrawn = _text(item, "sponsorshipWithdrawnDate")
    if sponsor_container:
        relationship_role = "Sponsor"
        role_source = "bill/sponsors/item"
    elif original is True:
        relationship_role = "Original Cosponsor"
        role_source = "bill/cosponsors/item/isOriginalCosponsor=true"
    elif original is False:
        relationship_role = "Cosponsor"
        role_source = "bill/cosponsors/item/isOriginalCosponsor=false"
    else:
        relationship_role = None
        role_source = "bill/cosponsors/item/isOriginalCosponsor=missing"
    return {
        "bioguide_id": _text(item, "bioguideId", "identifiers/bioguideId"),
        "full_name": _text(item, "fullName"),
        "first_name": _text(item, "firstName"),
        "middle_name": _text(item, "middleName"),
        "last_name": _text(item, "lastName"),
        "party": _text(item, "party"),
        "state": _text(item, "state"),
        "district": _text(item, "district"),
        "sponsorship_date": _text(item, "sponsorshipDate"),
        "original_cosponsor": original,
        "withdrawn_date": withdrawn,
        "relationship_status": "withdrawn" if withdrawn else "current",
        "relationship_role": relationship_role,
        "role_provenance": role_source,
        "role_provenance_complete": relationship_role is not None,
    }


def parse_billstatus_xml(
    path: Path,
    *,
    source_url: str,
    sitemap_lastmod: str,
    expected_sha256: str,
) -> dict[str, Any]:
    """Parse one hash-bound v3 BILLSTATUS record and preserve provenance."""
    raw = path.read_bytes()
    _require(len(raw) <= MAX_XML_BYTES, f"oversize XML: {path}")
    upper = raw.upper()
    _require(b"<!DOCTYPE" not in upper, "DOCTYPE is forbidden")
    _require(b"<!ENTITY" not in upper, "ENTITY is forbidden")
    actual_sha256 = sha256_bytes(raw)
    _require(SHA256_RE.fullmatch(expected_sha256) is not None, "bad SHA-256")
    _require(actual_sha256 == expected_sha256, f"SHA-256 mismatch: {path}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ContractError(f"malformed XML: {path}") from error
    _require(root.tag == "billStatus", f"unexpected root: {root.tag}")
    version = _text(root, "version")
    _require(
        version in FORMAT_VERSION_ALLOWLIST,
        f"unapproved BILLSTATUS format version: {version}",
    )
    bill = root.find("bill")
    _require(bill is not None, "missing bill element")
    assert bill is not None
    congress_text = _text(bill, "congress")
    type_text = _text(bill, "type")
    number_text = _text(bill, "number")
    _require(
        congress_text is not None and congress_text.isdigit(),
        "invalid bill congress",
    )
    _require(
        number_text is not None and number_text.isdigit(),
        "invalid bill number",
    )
    congress = int(congress_text)
    number = int(number_text)
    measure_type = str(type_text or "").lower()
    _require(congress in CONGRESSES, f"out-of-scope congress: {congress}")
    _require(
        measure_type in TOP_LEVEL_TYPES,
        f"unsupported top-level type: {measure_type}",
    )
    key = canonical_key(congress, measure_type, number)
    _require(
        source_url == expected_record_url(congress, measure_type, number),
        f"source URL does not match XML identity: {source_url}",
    )
    parsed_url = urlparse(source_url)
    _require(
        parsed_url.scheme == "https"
        and parsed_url.netloc == "www.govinfo.gov",
        f"non-official source URL: {source_url}",
    )
    update_date = _iso8601(_text(bill, "updateDate"), "bill.updateDate")
    update_including_text = _text(bill, "updateDateIncludingText")
    if update_including_text:
        update_including_text = _iso8601(
            update_including_text, "bill.updateDateIncludingText"
        )
    sitemap_lastmod = _iso8601(sitemap_lastmod, "sitemap.lastmod")

    titles = [
        {
            "title": _text(item, "title"),
            "title_type": _text(item, "titleType"),
            "bill_text_version_code": _text(item, "billTextVersionCode"),
            "bill_text_version_name": _text(item, "billTextVersionName"),
        }
        for item in bill.findall("./titles/item")
    ]
    summaries = bill.findall("./summaries/summary")
    summary_rows = [
        {
            "action_date": _text(item, "actionDate"),
            "action_description": _text(item, "actionDesc"),
            "version_code": _text(item, "versionCode"),
            "update_date": _text(item, "updateDate"),
            "text": _text(item, "text"),
        }
        for item in summaries
    ]
    sponsor_rows = [
        _member(item, sponsor_container=True)
        for item in bill.findall("./sponsors/item")
    ]
    cosponsor_rows = [
        _member(item, sponsor_container=False)
        for item in bill.findall("./cosponsors/item")
    ]
    action_rows: list[dict[str, Any]] = []
    recorded_votes: list[dict[str, Any]] = []
    for action_index, item in enumerate(bill.findall("./actions/item")):
        action = {
            "action_index": action_index,
            "action_date": _text(item, "actionDate"),
            "action_time": _text(item, "actionTime"),
            "action_code": _text(item, "actionCode"),
            "action_type": _text(item, "type"),
            "text": _text(item, "text"),
            "source_system_code": _text(item, "sourceSystem/code"),
            "source_system_name": _text(item, "sourceSystem/name"),
        }
        action_rows.append(action)
        for vote in item.findall("./recordedVotes/recordedVote"):
            vote_congress = _text(vote, "congress")
            _require(
                vote_congress is not None
                and vote_congress.isdigit()
                and int(vote_congress) == congress,
                f"recorded vote Congress conflicts with {key}",
            )
            recorded_votes.append(
                {
                    "action_index": action_index,
                    "chamber": _text(vote, "chamber"),
                    "congress": vote_congress,
                    "date": _text(vote, "date"),
                    "roll_number": _text(vote, "rollNumber"),
                    "session_number": _text(vote, "sessionNumber"),
                    "url": _text(vote, "url"),
                }
            )
    text_versions = [
        {
            "date": _text(item, "date"),
            "type": _text(item, "type"),
            "urls": [
                node.text.strip()
                for node in item.findall("./formats/item/url")
                if node.text and node.text.strip()
            ],
        }
        for item in bill.findall("./textVersions/item")
    ]
    return {
        "schema": "govinfo-billstatus-enrichment-record-v1",
        "canonical_key": key,
        "identity": {
            "congress": congress,
            "type": measure_type,
            "number": number,
        },
        "format_version": version,
        "record_update_date": update_date,
        "record_update_date_including_text": update_including_text,
        "titles": titles,
        "summaries": summary_rows,
        "sponsors": sponsor_rows,
        "cosponsors": cosponsor_rows,
        "actions": action_rows,
        "recorded_votes": recorded_votes,
        "text_versions": text_versions,
        "provenance": {
            "authority": "U.S. Government Publishing Office / Congress.gov",
            "source_url": source_url,
            "sitemap_lastmod": sitemap_lastmod,
            "source_sha256": actual_sha256,
            "source_bytes": len(raw),
            "retrieval_scope": "staged_enrichment_only",
        },
    }


def build_fetch_plan(
    discovery_path: Path,
    exclusion_ledger_path: Path,
    provenance_ledgers: tuple[Path, ...] = (),
    strict_manifest_path: Path | None = None,
    cumulative_manifest_path: Path | None = None,
    cumulative_memberships_path: Path | None = None,
) -> dict[str, Any]:
    """Build a no-network fetch plan from the frozen discovery corpus."""
    rows = read_ndjson_with_provenance(discovery_path)
    keys: set[str] = set()
    tasks: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for row in rows:
        congress = int(row["congress"])
        measure_type = str(row["type"]).lower()
        number = int(row["number"])
        key = canonical_key(congress, measure_type, number)
        _require(row.get("canonical_key") == key, f"bad discovery key: {key}")
        _require(key not in keys, f"duplicate discovery key: {key}")
        _require(congress in CONGRESSES, f"out-of-scope discovery key: {key}")
        _require(
            measure_type in DISCOVERY_TYPES,
            f"unexpected discovery type: {measure_type}",
        )
        keys.add(key)
        row_provenance = {
            "advanced_search_line": row["_input_line"],
            "advanced_search_line_sha256": row["_input_line_sha256"],
        }
        if measure_type in NESTED_ONLY_TYPES:
            unsupported.append(
                {
                    "canonical_key": key,
                    "status": "unsupported_nested_amendment",
                    "candidate_retained": True,
                    **row_provenance,
                }
            )
        else:
            tasks.append(
                {
                    "canonical_key": key,
                    "url": expected_record_url(
                        congress, measure_type, number
                    ),
                    **row_provenance,
                }
            )
    matrix = [
        {
            "congress": congress,
            "type": measure_type,
            "sitemap_url": expected_sitemap_url(congress, measure_type),
        }
        for congress in CONGRESSES
        for measure_type in TOP_LEVEL_TYPES
    ]
    plan = {
        "schema": "govinfo-billstatus-enrichment-fetch-plan-v1",
        "primary_discovery": PRIMARY_DISCOVERY,
        "quorum_used": False,
        "candidate_creation_authorized": False,
        "semantic_exclusion_authorized": False,
        "latest_action_exhaustive_evidence_authorized": False,
        "production_write_authorized": False,
        "network_executed": False,
        "official_base": OFFICIAL_BASE,
        "sitemap_index_url": SITEMAP_INDEX,
        "format_version_allowlist": list(FORMAT_VERSION_ALLOWLIST),
        "inputs": {
            "advanced_search_records": {
                "path": str(discovery_path),
                "sha256": sha256_file(discovery_path),
                "record_count": len(rows),
            },
            "exclusion_ledger": {
                "path": str(exclusion_ledger_path),
                "sha256": sha256_file(exclusion_ledger_path),
            },
            "exclusion_provenance": [
                {
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
                for path in provenance_ledgers
            ],
        },
        "type_sitemaps": matrix,
        "record_tasks": tasks,
        "unsupported_candidates": unsupported,
        "candidate_partition": {
            "all": len(rows),
            "top_level_enrichable": len(tasks),
            "nested_only_amendments": len(unsupported),
        },
        "acceptance_rule": (
            "A later validation receipt must account for every discovery "
            "candidate, preserve both frozen input hashes, contain no "
            "side-loaded key, and pass every XML identity/hash/schema gate."
        ),
    }
    if strict_manifest_path is not None:
        manifest_union = strict_manifest_union_snapshot(
            strict_manifest_path,
            rows,
            cumulative_manifest_path=cumulative_manifest_path,
            cumulative_memberships_path=cumulative_memberships_path,
        )
        plan["snapshot_contract"] = {
            "snapshot_only": True,
            "authoritative_for_discovery": False,
            "authoritative_for_semantic_exclusion": False,
            "authoritative_for_sync_or_promotion": False,
            "strict_manifest": file_receipt(strict_manifest_path),
            "strict_manifest_query_union": manifest_union,
            "stale_when": [
                "strict_manifest_path_or_sha256_changes",
                "strict_manifest_query_artifact_or_union_changes",
                "cumulative_aggregate_or_membership_receipt_changes",
                "advanced_search_records_sha256_or_count_changes",
                "exclusion_provenance_sha256_changes",
            ],
        }
    return plan


def validate_fetch_plan(plan: dict[str, Any]) -> None:
    _require(
        plan.get("schema")
        == "govinfo-billstatus-enrichment-fetch-plan-v1",
        "bad plan schema",
    )
    _require(plan.get("primary_discovery") == PRIMARY_DISCOVERY, "discovery drift")
    _require(plan.get("official_base") == OFFICIAL_BASE, "official base drift")
    _require(
        plan.get("sitemap_index_url") == SITEMAP_INDEX,
        "sitemap index drift",
    )
    _require(
        plan.get("format_version_allowlist")
        == list(FORMAT_VERSION_ALLOWLIST),
        "format version allowlist drift",
    )
    _require(plan.get("quorum_used") is False, "Quorum is forbidden")
    _require(
        plan.get("candidate_creation_authorized") is False,
        "candidate creation must be forbidden",
    )
    _require(
        plan.get("semantic_exclusion_authorized") is False,
        "semantic exclusion must be forbidden",
    )
    _require(
        plan.get("latest_action_exhaustive_evidence_authorized") is False,
        "generic latest_action is not exhaustive action evidence",
    )
    _require(
        plan.get("production_write_authorized") is False,
        "production writes must be forbidden",
    )
    _require(plan.get("network_executed") is False, "plan is not non-fetching")
    inputs = plan.get("inputs", {})
    records_input = inputs.get("advanced_search_records", {})
    exclusion_input = inputs.get("exclusion_ledger", {})
    records_path = Path(str(records_input.get("path", "")))
    exclusion_path = Path(str(exclusion_input.get("path", "")))
    _require(records_path.is_file(), "discovery input is missing")
    _require(exclusion_path.is_file(), "exclusion ledger is missing")
    _require(
        records_input.get("sha256") == sha256_file(records_path),
        "discovery corpus hash drift",
    )
    _require(
        exclusion_input.get("sha256") == sha256_file(exclusion_path),
        "exclusion ledger hash drift",
    )
    for entry in inputs.get("exclusion_provenance", []):
        path = Path(str(entry.get("path", "")))
        _require(path.is_file(), "exclusion provenance ledger is missing")
        _require(
            entry.get("sha256") == sha256_file(path),
            f"exclusion provenance hash drift: {path}",
        )
        _require(
            entry.get("bytes") == path.stat().st_size,
            f"exclusion provenance size drift: {path}",
        )
    discovery_rows = read_ndjson_with_provenance(records_path)
    _require(
        records_input.get("record_count") == len(discovery_rows),
        "discovery record count drift",
    )
    discovered = {
        str(row["canonical_key"]): row for row in discovery_rows
    }
    _require(
        len(discovered) == len(discovery_rows),
        "duplicate key in frozen discovery corpus",
    )
    matrix_rows = plan.get("type_sitemaps", [])
    _require(len(matrix_rows) == 32, "type sitemap matrix is not exact 32/32")
    matrix = {
        (int(row["congress"]), str(row["type"]))
        for row in matrix_rows
    }
    expected = {
        (congress, measure_type)
        for congress in CONGRESSES
        for measure_type in TOP_LEVEL_TYPES
    }
    _require(matrix == expected, "type sitemap matrix is not exact 32/32")
    tasks = plan.get("record_tasks", [])
    unsupported = plan.get("unsupported_candidates", [])
    all_keys = [row["canonical_key"] for row in tasks + unsupported]
    _require(len(all_keys) == len(set(all_keys)), "duplicate planned key")
    _require(
        set(all_keys) == set(discovered),
        "planned keys differ from frozen discovery corpus",
    )
    for row in tasks + unsupported:
        source = discovered[row["canonical_key"]]
        _require(
            row.get("advanced_search_line") == source["_input_line"]
            and row.get("advanced_search_line_sha256")
            == source["_input_line_sha256"],
            f"discovery provenance drift: {row['canonical_key']}",
        )
    partition = plan.get("candidate_partition", {})
    _require(
        partition.get("all") == len(all_keys),
        "candidate count changed in plan",
    )
    _require(
        partition.get("top_level_enrichable") == len(tasks),
        "top-level partition count mismatch",
    )
    _require(
        partition.get("nested_only_amendments") == len(unsupported),
        "amendment partition count mismatch",
    )
    for task in tasks:
        congress, measure_type, number = task["canonical_key"].split(":")
        _require(
            task["url"]
            == expected_record_url(
                int(congress), measure_type, int(number)
            ),
            f"task URL drift: {task['canonical_key']}",
        )
    for row in unsupported:
        _require(
            row.get("candidate_retained") is True
            and row.get("status") == "unsupported_nested_amendment",
            f"amendment not retained: {row.get('canonical_key')}",
        )


def validate_snapshot_plan(
    plan: dict[str, Any], *, expected_strict_manifest: Path
) -> None:
    """Validate a snapshot plan against the selected strict manifest."""
    validate_fetch_plan(plan)
    snapshot = plan.get("snapshot_contract")
    _require(isinstance(snapshot, dict), "snapshot contract is missing")
    assert isinstance(snapshot, dict)
    _require(snapshot.get("snapshot_only") is True, "plan is not snapshot-only")
    _require(
        snapshot.get("authoritative_for_discovery") is False,
        "BILLSTATUS cannot be discovery authority",
    )
    _require(
        snapshot.get("authoritative_for_semantic_exclusion") is False,
        "BILLSTATUS cannot authorize semantic exclusions",
    )
    _require(
        snapshot.get("authoritative_for_sync_or_promotion") is False,
        "snapshot cannot authorize sync or promotion",
    )
    receipt = snapshot.get("strict_manifest")
    _require(isinstance(receipt, dict), "strict manifest receipt is missing")
    assert isinstance(receipt, dict)
    strict = expected_strict_manifest.resolve()
    _require(strict.is_file(), "selected strict manifest is missing")
    _require(
        Path(str(receipt.get("path", ""))).resolve() == strict,
        "selected strict manifest path advanced",
    )
    _require(
        receipt.get("sha256") == sha256_file(strict),
        "selected strict manifest hash advanced",
    )
    _require(
        receipt.get("bytes") == strict.stat().st_size,
        "selected strict manifest size advanced",
    )
    current_union = strict_manifest_union_snapshot(
        strict,
        read_ndjson_with_provenance(
            Path(
                str(
                    plan.get("inputs", {})
                    .get("advanced_search_records", {})
                    .get("path", "")
                )
            )
        ),
        cumulative_manifest_path=(
            Path(
                str(
                    (
                        snapshot.get("strict_manifest_query_union") or {}
                    )
                    .get("cumulative_aggregate", {})
                    .get("manifest", {})
                    .get("path", "")
                )
            )
            if (
                (snapshot.get("strict_manifest_query_union") or {}).get(
                    "cumulative_aggregate"
                )
            )
            else None
        ),
        cumulative_memberships_path=(
            Path(
                str(
                    (
                        snapshot.get("strict_manifest_query_union") or {}
                    )
                    .get("cumulative_aggregate", {})
                    .get("memberships", {})
                    .get("path", "")
                )
            )
            if (
                (snapshot.get("strict_manifest_query_union") or {}).get(
                    "cumulative_aggregate"
                )
            )
            else None
        ),
    )
    _require(
        snapshot.get("strict_manifest_query_union") == current_union,
        "strict-manifest query union snapshot advanced",
    )
    _require(
        snapshot.get("stale_when")
        == [
            "strict_manifest_path_or_sha256_changes",
            "strict_manifest_query_artifact_or_union_changes",
            "cumulative_aggregate_or_membership_receipt_changes",
            "advanced_search_records_sha256_or_count_changes",
            "exclusion_provenance_sha256_changes",
        ],
        "snapshot staleness contract drift",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery-records", type=Path)
    parser.add_argument("--exclusion-ledger", type=Path)
    parser.add_argument(
        "--provenance-ledger", type=Path, action="append", default=[]
    )
    parser.add_argument("--strict-manifest", type=Path)
    parser.add_argument("--cumulative-manifest", type=Path)
    parser.add_argument("--cumulative-memberships", type=Path)
    parser.add_argument("--plan-out", type=Path)
    return parser.parse_args()


def validate_staging_plan_output(path: Path) -> Path:
    """Require a new JSON artifact under an explicitly staged/audited path."""
    resolved = path.expanduser().resolve()
    if resolved == ROOT.resolve() or resolved.suffix.casefold() != ".json":
        raise ContractError(
            "plan output must be a JSON file, not the workspace root"
        )
    if not any(
        re.search(r"(?:audit|stag)", part, re.IGNORECASE)
        for part in resolved.parent.parts
    ):
        raise ContractError(
            "plan output path must explicitly identify audit/staging scope"
        )
    if resolved.exists():
        raise ContractError(
            f"immutable staging plan already exists: {resolved}"
        )
    if resolved.parent.exists() and not resolved.parent.is_dir():
        raise ContractError(
            f"plan output parent is not a directory: {resolved.parent}"
        )
    return resolved


def main() -> int:
    args = parse_args()
    if not (
        args.discovery_records and args.exclusion_ledger and args.plan_out
    ):
        raise SystemExit(
            "--discovery-records, --exclusion-ledger, and --plan-out "
            "are all required"
        )
    plan_out = validate_staging_plan_output(args.plan_out)
    plan = build_fetch_plan(
        args.discovery_records,
        args.exclusion_ledger,
        tuple(args.provenance_ledger),
        args.strict_manifest,
        args.cumulative_manifest,
        args.cumulative_memberships,
    )
    if args.strict_manifest is None:
        validate_fetch_plan(plan)
    else:
        validate_snapshot_plan(
            plan, expected_strict_manifest=args.strict_manifest
        )
    plan_out.parent.mkdir(parents=True, exist_ok=True)
    with plan_out.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "plan": str(plan_out),
                "network_executed": False,
                **plan["candidate_partition"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

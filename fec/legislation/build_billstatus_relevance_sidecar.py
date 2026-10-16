#!/usr/bin/env python3
"""Build a staged-only structured relevance sidecar from BILLSTATUS XML.

The sidecar is deliberately non-authoritative.  It echoes the complete
Congress.gov Advanced Search candidate partition, structures selected official
BILLSTATUS sections for the already-planned top-level measures, and keeps every
unsupported amendment candidate unresolved.  It never performs discovery,
network access, semantic decisions, scoring, ratification, sync, or promotion.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, BinaryIO, Iterable
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from legislation import refresh_billstatus_snapshot_evidence as SNAPSHOT_REFRESH
from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS

try:
    from lane_file_io import atomic_write_bytes, iter_ndjson_raw_lines
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes, iter_ndjson_raw_lines


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "govinfo-billstatus-structured-relevance-sidecar-v1"
MEASURE_SCHEMA = "govinfo-billstatus-structured-relevance-measure-v1"
AMENDMENT_SCHEMA = "congress-advanced-search-unresolved-amendment-v1"
GAP_SCHEMA = "govinfo-billstatus-structured-relevance-section-gap-v1"
POINTER_SCHEMA = "govinfo-billstatus-structured-relevance-pointer-v1"
MAX_XML_BYTES = 64 * 1024 * 1024
SHA256_HEX_LENGTH = 64

# These paths are data extraction contracts, not relevance rules.  Values are
# preserved as source-order XML trees so new or repeated official fields cannot
# be silently dropped.
SECTION_SPECS: tuple[tuple[str, str, str | None], ...] = (
    ("policy_area", "policyArea", None),
    (
        "legislative_subjects",
        "subjects/legislativeSubjects",
        "./item",
    ),
    ("committees", "committees", "./item"),
    ("related_bills", "relatedBills", "./item"),
    ("amendments", "amendments", "./amendment"),
    ("laws", "laws", "./item"),
    ("committee_reports", "committeeReports", "./committeeReport"),
    ("cbo_cost_estimates", "cboCostEstimates", "./item"),
)
EXTRACTION_CONTRACT = {
    "version": 1,
    "representation": (
        "source-order element tree with tag, attributes, substantive text, "
        "substantive tail text, and children"
    ),
    "sections": {
        name: {
            "source_xpath": f"bill/{path}",
            "item_selector": selector,
        }
        for name, path, selector in SECTION_SPECS
    },
}


class ContractError(RuntimeError):
    """Raised when staged evidence does not satisfy the sidecar contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
    )


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"invalid JSON object: {path}") from error
    require(isinstance(value, dict), f"JSON object required: {path}")
    return value


def atomic_write(path: Path, body: bytes) -> None:
    # Was ``path.with_name(path.name + ".tmp")``: a fixed scratch name that two
    # concurrent sidecar builds writing the same output silently clobbered, and
    # that OneDrive syncs and conflict-copies.  ``atomic_write_bytes`` is unique
    # per process and per call, fsync'd before publication, and retries the
    # publish through the OneDrive/AV lock window.
    atomic_write_bytes(path, body)


def file_item(path: Path, *, record_count: int | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    item: dict[str, Any] = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    if record_count is not None:
        item["record_count"] = record_count
    return item


def output_item(path: Path, *, record_count: int) -> dict[str, Any]:
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "record_count": record_count,
    }


def count_ndjson(path: Path) -> int:
    count = 0
    for _line_number, raw in iter_ndjson_raw_lines(path):
        if raw.strip():
            json.loads(raw.decode("utf-8"))
            count += 1
    return count


def resolve_input_path(raw_path: Any) -> Path:
    require(
        isinstance(raw_path, str) and raw_path.strip(),
        "input receipt path is missing",
    )
    path = Path(raw_path)
    return (path if path.is_absolute() else ROOT / path).resolve()


def validate_file_receipt(
    item: dict[str, Any],
    label: str,
    *,
    require_record_count: bool = False,
) -> tuple[Path, dict[str, Any]]:
    require(isinstance(item, dict), f"{label} receipt is missing")
    path = resolve_input_path(item.get("path"))
    require(path.is_file(), f"{label} file is missing: {path}")
    actual_hash = sha256_file(path)
    require(
        item.get("sha256") == actual_hash,
        f"{label} SHA-256 mismatch: {path}",
    )
    if "bytes" in item:
        require(
            item.get("bytes") == path.stat().st_size,
            f"{label} byte-count mismatch: {path}",
        )
    if require_record_count:
        require(
            isinstance(item.get("record_count"), int)
            and item["record_count"] >= 0,
            f"{label} record count is missing",
        )
    normalized = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": actual_hash,
    }
    if "record_count" in item:
        normalized["record_count"] = item["record_count"]
    return path, normalized


def canonical_key_parts(key: str) -> tuple[int, str, int]:
    parts = key.split(":")
    require(len(parts) == 3, f"invalid canonical key: {key}")
    congress_text, measure_type, number_text = parts
    require(
        congress_text.isdigit() and number_text.isdigit(),
        f"invalid canonical key: {key}",
    )
    return int(congress_text), measure_type.lower(), int(number_text)


def key_sort(key: str) -> tuple[int, str, int]:
    return canonical_key_parts(key)


def expected_url(key: str) -> str:
    congress, measure_type, number = canonical_key_parts(key)
    return BILLSTATUS.expected_record_url(congress, measure_type, number)


def validate_official_url(url: Any, key: str) -> str:
    require(isinstance(url, str), f"source URL missing: {key}")
    parsed = urlparse(url)
    require(
        parsed.scheme == "https"
        and parsed.hostname == "www.govinfo.gov"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
        and url == expected_url(key),
        f"source URL/XML plan identity drift: {key}",
    )
    return url


def load_candidates(
    path: Path,
    receipt: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[int, tuple[str, str]]]:
    by_key: dict[str, dict[str, Any]] = {}
    by_line: dict[int, tuple[str, str]] = {}
    count = 0
    for line_number, raw in iter_ndjson_raw_lines(path):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContractError(
                f"invalid candidate NDJSON line {line_number}: {path}"
            ) from error
        require(
            isinstance(row, dict)
            and isinstance(row.get("canonical_key"), str),
            f"candidate identity missing at line {line_number}",
        )
        key = str(row["canonical_key"])
        require(key not in by_key, f"duplicate discovery candidate: {key}")
        line_hash = sha256_bytes(raw.rstrip(b"\r\n"))
        by_key[key] = row
        by_line[line_number] = (key, line_hash)
        count += 1
    require(
        receipt.get("record_count") == count,
        "advanced-search candidate count drift",
    )
    return by_key, by_line


def validate_line_binding(
    row: dict[str, Any],
    *,
    expected_key: str,
    by_line: dict[int, tuple[str, str]],
) -> None:
    line_number = row.get("advanced_search_line")
    line_hash = row.get("advanced_search_line_sha256")
    require(
        isinstance(line_number, int)
        and line_number in by_line
        and by_line[line_number] == (expected_key, line_hash),
        f"advanced-search line provenance drift: {expected_key}",
    )


def validate_worker_and_plan(
    worker_path: Path,
) -> dict[str, Any]:
    worker_path = worker_path.resolve()
    worker = read_object(worker_path)
    require(
        worker.get("schema") == "govinfo-billstatus-enrichment-run-v1"
        and worker.get("status") == "COMPLETE",
        "worker is not a complete staged BILLSTATUS run",
    )
    require(worker.get("official_source_only") is True, "worker source drift")
    require(worker.get("failures") == [], "worker failures are not empty")
    for field in (
        "production_write_performed",
        "sync_or_promotion_performed",
        "candidate_creation_performed",
        "semantic_exclusion_performed",
    ):
        require(worker.get(field) is False, f"unsafe worker flag: {field}")

    plan_item = worker.get("plan")
    plan_path, normalized_plan_item = validate_file_receipt(
        plan_item, "snapshot plan"
    )
    plan = read_object(plan_path)
    require(
        plan.get("schema") == "govinfo-billstatus-enrichment-fetch-plan-v1"
        and plan.get("primary_discovery")
        == "Congress.gov Advanced Search - Legislation"
        and plan.get("quorum_used") is False,
        "snapshot plan discovery contract drift",
    )
    for field in (
        "candidate_creation_authorized",
        "semantic_exclusion_authorized",
        "production_write_authorized",
    ):
        require(
            plan.get(field) in (False, None),
            f"unsafe snapshot-plan authority: {field}",
        )

    plan_inputs = plan.get("inputs")
    require(isinstance(plan_inputs, dict), "snapshot-plan inputs missing")
    candidate_path, candidate_item = validate_file_receipt(
        plan_inputs.get("advanced_search_records"),
        "advanced-search candidates",
        require_record_count=True,
    )
    candidates, candidates_by_line = load_candidates(
        candidate_path, candidate_item
    )

    exclusion_path, exclusion_item = validate_file_receipt(
        plan_inputs.get("exclusion_ledger"), "exclusion ledger"
    )
    del exclusion_path
    raw_provenance = plan_inputs.get("exclusion_provenance")
    require(
        isinstance(raw_provenance, list),
        "exclusion-provenance receipts missing",
    )
    exclusion_provenance: list[dict[str, Any]] = []
    for index, item in enumerate(raw_provenance):
        _path, normalized = validate_file_receipt(
            item, f"exclusion provenance {index}"
        )
        exclusion_provenance.append(normalized)

    tasks = plan.get("record_tasks")
    unsupported = plan.get("unsupported_candidates")
    partition = plan.get("candidate_partition")
    require(
        isinstance(tasks, list)
        and isinstance(unsupported, list)
        and isinstance(partition, dict),
        "snapshot candidate partition missing",
    )
    task_map: dict[str, dict[str, Any]] = {}
    for task in tasks:
        require(isinstance(task, dict), "invalid top-level task")
        key = str(task.get("canonical_key") or "")
        congress, measure_type, _number = canonical_key_parts(key)
        require(
            congress in BILLSTATUS.CONGRESSES
            and measure_type in BILLSTATUS.TOP_LEVEL_TYPES,
            f"out-of-scope top-level task: {key}",
        )
        require(key not in task_map, f"duplicate top-level task: {key}")
        require(task.get("url") == expected_url(key), f"task URL drift: {key}")
        validate_line_binding(
            task, expected_key=key, by_line=candidates_by_line
        )
        task_map[key] = task

    unsupported_map: dict[str, dict[str, Any]] = {}
    for row in unsupported:
        require(isinstance(row, dict), "invalid unsupported candidate")
        key = str(row.get("canonical_key") or "")
        congress, measure_type, _number = canonical_key_parts(key)
        require(
            congress in BILLSTATUS.CONGRESSES
            and measure_type in BILLSTATUS.NESTED_ONLY_TYPES
            and row.get("status") == "unsupported_nested_amendment"
            and row.get("candidate_retained") is True,
            f"amendment candidate was not retained unresolved: {key}",
        )
        require(
            key not in task_map and key not in unsupported_map,
            f"duplicate/overlapping candidate partition: {key}",
        )
        validate_line_binding(
            row, expected_key=key, by_line=candidates_by_line
        )
        unsupported_map[key] = row

    all_partition_keys = set(task_map) | set(unsupported_map)
    require(
        all_partition_keys == set(candidates),
        "candidate partition is not lossless",
    )
    require(
        partition.get("all") == len(candidates)
        and partition.get("top_level_enrichable") == len(task_map)
        and partition.get("nested_only_amendments") == len(unsupported_map),
        "candidate partition counts drift",
    )

    receipts = worker.get("record_receipts")
    require(isinstance(receipts, list), "worker record receipts missing")
    receipt_map: dict[str, dict[str, Any]] = {}
    run_dir = worker_path.parent
    raw_root = (run_dir / "raw" / "sha256").resolve()
    receipt_root = (run_dir / "receipts").resolve()
    for receipt in receipts:
        require(isinstance(receipt, dict), "invalid worker record receipt")
        key = str(receipt.get("canonical_key") or "")
        require(
            key in task_map and key not in receipt_map,
            f"unexpected/duplicate worker receipt: {key}",
        )
        validate_official_url(receipt.get("source_url"), key)
        require(
            receipt.get("candidate_creation_performed") is False
            and receipt.get("semantic_exclusion_performed") is False,
            f"unsafe record receipt: {key}",
        )
        raw_hash = receipt.get("raw_sha256")
        require(
            isinstance(raw_hash, str)
            and len(raw_hash) == SHA256_HEX_LENGTH
            and all(character in "0123456789abcdef" for character in raw_hash),
            f"invalid raw hash: {key}",
        )
        raw_path = resolve_input_path(receipt.get("raw_path"))
        try:
            raw_path.relative_to(raw_root)
        except ValueError as error:
            raise ContractError(f"raw path escapes staged run: {key}") from error
        require(
            raw_path.name == f"{raw_hash}.xml"
            and raw_path.is_file()
            and receipt.get("raw_bytes") == raw_path.stat().st_size,
            f"raw receipt path/size drift: {key}",
        )
        receipt_path = (
            receipt_root / f"{key.replace(':', '_')}.json"
        ).resolve()
        try:
            receipt_path.relative_to(receipt_root)
        except ValueError as error:
            raise ContractError(
                f"record receipt path escapes staged run: {key}"
            ) from error
        require(
            receipt_path.is_file()
            and read_object(receipt_path) == receipt,
            f"record receipt file drift: {key}",
        )
        validate_line_binding(
            receipt, expected_key=key, by_line=candidates_by_line
        )
        receipt_map[key] = {
            **receipt,
            "_raw_path": raw_path,
            "_receipt_item": file_item(receipt_path),
        }
    require(
        set(receipt_map) == set(task_map),
        "planned top-level key set does not equal worker receipt key set",
    )
    worker_partition = worker.get("partition") or {}
    require(
        worker_partition.get("top_level_planned") == len(task_map)
        and worker_partition.get("top_level_completed") == len(task_map)
        and worker_partition.get("top_level_failed") == 0
        and worker_partition.get("remaining_top_level") == 0
        and worker_partition.get(
            "nested_only_amendments_retained_unresolved"
        )
        == len(unsupported_map),
        "worker closure partition drift",
    )

    return {
        "worker": worker,
        "worker_path": worker_path,
        "worker_item": file_item(worker_path),
        "plan": plan,
        "plan_path": plan_path,
        "plan_item": normalized_plan_item,
        "candidate_path": candidate_path,
        "candidate_item": candidate_item,
        "candidates": candidates,
        "tasks": task_map,
        "unsupported": unsupported_map,
        "receipts": receipt_map,
        "exclusion_ledger": exclusion_item,
        "exclusion_provenance": exclusion_provenance,
    }


def substantive(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


def element_tree(node: ET.Element) -> dict[str, Any]:
    value: dict[str, Any] = {"tag": node.tag}
    if node.attrib:
        value["attributes"] = dict(sorted(node.attrib.items()))
    text = substantive(node.text)
    if text is not None:
        value["text"] = text
    children = [element_tree(child) for child in list(node)]
    if children:
        value["children"] = children
    tail = substantive(node.tail)
    if tail is not None:
        value["tail_text"] = tail
    return value


def leaf_text_count(node: ET.Element) -> int:
    return sum(
        1
        for descendant in node.iter()
        if len(descendant) == 0 and substantive(descendant.text) is not None
    )


def parse_measure(
    *,
    key: str,
    task: dict[str, Any],
    receipt: dict[str, Any],
    candidate: dict[str, Any],
    worker_item: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, int]],
]:
    raw_path = receipt["_raw_path"]
    raw = raw_path.read_bytes()
    require(len(raw) <= MAX_XML_BYTES, f"oversize XML: {key}")
    upper = raw.upper()
    require(b"<!DOCTYPE" not in upper, f"DOCTYPE is forbidden: {key}")
    require(b"<!ENTITY" not in upper, f"ENTITY is forbidden: {key}")
    actual_hash = sha256_bytes(raw)
    require(
        actual_hash == receipt.get("raw_sha256"),
        f"raw XML SHA-256 mismatch: {key}",
    )
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ContractError(f"malformed raw XML: {key}") from error
    require(root.tag == "billStatus", f"unexpected XML root: {key}")
    version = BILLSTATUS._text(root, "version")
    require(
        version in BILLSTATUS.FORMAT_VERSION_ALLOWLIST,
        f"unapproved BILLSTATUS version: {key}",
    )
    bill = root.find("bill")
    require(bill is not None, f"missing bill element: {key}")
    assert bill is not None
    congress, measure_type, number = canonical_key_parts(key)
    require(
        BILLSTATUS._text(bill, "congress") == str(congress)
        and str(BILLSTATUS._text(bill, "type") or "").lower() == measure_type
        and BILLSTATUS._text(bill, "number") == str(number),
        f"XML bill identity drift: {key}",
    )
    validate_official_url(receipt.get("source_url"), key)

    sections: dict[str, dict[str, Any]] = {}
    missing_rows: list[dict[str, Any]] = []
    empty_rows: list[dict[str, Any]] = []
    section_counts: dict[str, dict[str, int]] = {}
    source_hash = str(receipt["raw_sha256"])
    source_path = str(raw_path)
    for section_name, relative_path, item_selector in SECTION_SPECS:
        source_xpath = f"bill/{relative_path}"
        section = bill.find(relative_path)
        if section is None:
            status = "missing"
            item_count = 0
            leaf_count = 0
            sections[section_name] = {
                "status": status,
                "source_xpath": source_xpath,
                "source_record_sha256": source_hash,
                "data": None,
            }
            missing_rows.append(
                {
                    "schema": GAP_SCHEMA,
                    "canonical_key": key,
                    "section": section_name,
                    "source_xpath": source_xpath,
                    "gap_kind": "source_section_missing",
                    "source_record": {
                        "path": source_path,
                        "sha256": source_hash,
                        "bytes": receipt["raw_bytes"],
                    },
                    "candidate_retained": True,
                    "absence_is_neutral": True,
                    "automatic_exclusion_authorized": False,
                    "automatic_scoring_authorized": False,
                }
            )
        else:
            leaf_count = leaf_text_count(section)
            is_empty = len(section) == 0 and leaf_count == 0
            status = "present_empty" if is_empty else "present"
            item_count = (
                1
                if item_selector is None and not is_empty
                else (
                    0
                    if item_selector is None
                    else len(section.findall(item_selector))
                )
            )
            fragment = ET.tostring(section, encoding="utf-8")
            sections[section_name] = {
                "status": status,
                "source_xpath": source_xpath,
                "source_record_sha256": source_hash,
                "parsed_fragment_sha256": sha256_bytes(fragment),
                "direct_child_count": len(section),
                "item_count": item_count,
                "leaf_text_count": leaf_count,
                "data": element_tree(section),
            }
            if is_empty:
                empty_rows.append(
                    {
                        "schema": GAP_SCHEMA,
                        "canonical_key": key,
                        "section": section_name,
                        "source_xpath": source_xpath,
                        "gap_kind": "source_section_present_empty",
                        "source_record": {
                            "path": source_path,
                            "sha256": source_hash,
                            "bytes": receipt["raw_bytes"],
                        },
                        "candidate_retained": True,
                        "absence_is_neutral": True,
                        "automatic_exclusion_authorized": False,
                        "automatic_scoring_authorized": False,
                    }
                )
        section_counts[section_name] = {
            "present": int(status == "present"),
            "present_empty": int(status == "present_empty"),
            "missing": int(status == "missing"),
            "item_count": item_count,
            "leaf_text_count": leaf_count,
        }

    row = {
        "schema": MEASURE_SCHEMA,
        "canonical_key": key,
        "identity": {
            "congress": congress,
            "type": measure_type,
            "number": number,
        },
        "advanced_search_candidate": candidate,
        "sections": sections,
        "missing_sections": [
            row["section"] for row in missing_rows
        ],
        "empty_sections": [row["section"] for row in empty_rows],
        "provenance": {
            "authority": "U.S. Government Publishing Office / Congress.gov",
            "source_url": receipt["source_url"],
            "sitemap_lastmod": receipt.get("sitemap_lastmod"),
            "raw_source": {
                "path": source_path,
                "bytes": receipt["raw_bytes"],
                "sha256": source_hash,
            },
            "record_receipt": receipt["_receipt_item"],
            "worker_manifest": worker_item,
            "advanced_search_line": task["advanced_search_line"],
            "advanced_search_line_sha256": task[
                "advanced_search_line_sha256"
            ],
        },
        "candidate_created": False,
        "semantic_decision": None,
        "automatic_scoring_authorized": False,
        "ratification_authority": False,
    }
    return row, missing_rows, empty_rows, section_counts


def write_row(handle: BinaryIO, value: dict[str, Any]) -> None:
    handle.write(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        + b"\n"
    )


def verify_all_raw_hashes(chain: dict[str, Any]) -> None:
    for key in sorted(chain["receipts"], key=key_sort):
        receipt = chain["receipts"][key]
        require(
            sha256_file(receipt["_raw_path"]) == receipt["raw_sha256"],
            f"raw XML SHA-256 mismatch: {key}",
        )


def manifest_content_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "build_key": manifest["build_key"],
        "outputs": manifest["outputs"],
        "section_counts": manifest["section_counts"],
        "coverage": manifest["coverage"],
    }


def validate_existing_sidecar(
    out_dir: Path,
    *,
    expected_sidecar_id: str,
    expected_build_key: str | None = None,
) -> dict[str, Any]:
    manifest_path = out_dir / "manifest.json"
    manifest = read_object(manifest_path)
    require(
        manifest.get("schema") == SCHEMA
        and manifest.get("status") == "COMPLETE_STAGED_ONLY"
        and manifest.get("sidecar_id") == expected_sidecar_id,
        "existing structured-relevance sidecar identity drift",
    )
    if expected_build_key is not None:
        require(
            manifest.get("build_key") == expected_build_key,
            "existing structured-relevance build-key drift",
        )
    for field in (
        "network_executed",
        "production_write_performed",
        "sync_or_promotion_performed",
        "candidate_creation_performed",
        "semantic_inclusion_performed",
        "semantic_exclusion_performed",
        "automatic_scoring_performed",
        "ratification_performed",
    ):
        require(manifest.get(field) is False, f"unsafe sidecar flag: {field}")
    outputs = manifest.get("outputs")
    require(isinstance(outputs, dict), "sidecar output receipts missing")
    for label, item in outputs.items():
        require(
            isinstance(item, dict)
            and isinstance(item.get("file"), str)
            and "/" not in item["file"]
            and "\\" not in item["file"],
            f"invalid sidecar output receipt: {label}",
        )
        path = (out_dir / item["file"]).resolve()
        try:
            path.relative_to(out_dir.resolve())
        except ValueError as error:
            raise ContractError(
                f"sidecar output escapes directory: {label}"
            ) from error
        require(
            path.is_file()
            and item.get("bytes") == path.stat().st_size
            and item.get("sha256") == sha256_file(path)
            and item.get("record_count") == count_ndjson(path),
            f"sidecar output receipt drift: {label}",
        )
    require(
        canonical_json_sha256(manifest_content_identity(manifest))
        == expected_sidecar_id,
        "sidecar content address drift",
    )
    return manifest


def _billstatus_audit_root() -> Path:
    return (
        ROOT
        / "audits"
        / "legfix5"
        / "billstatus_bulk_enrichment"
    ).resolve()


def _checkpoint_plan_receipt(
    item: Any,
    label: str,
) -> tuple[Path, dict[str, Any]]:
    require(isinstance(item, dict), f"{label} receipt is missing")
    require(
        isinstance(item.get("size"), int) and item["size"] >= 0,
        f"{label} size is missing",
    )
    normalized = dict(item)
    normalized["bytes"] = item["size"]
    path, receipt = validate_file_receipt(normalized, label)
    return path, receipt


def current_snapshot_plan_path() -> Path:
    """Resolve the current immutable plan, failing closed on receipt drift."""
    billstatus_root = _billstatus_audit_root()
    checkpoint_path = (
        ROOT
        / "audits"
        / "legfix5"
        / "shadow_recompute"
        / "presync_current"
        / "checkpoint.json"
    ).resolve()
    legacy_path = (billstatus_root / "current_snapshot_plan.json").resolve()
    if not checkpoint_path.exists():
        require(
            legacy_path.is_file(),
            "current BILLSTATUS plan and presync checkpoint are missing",
        )
        return legacy_path
    require(
        checkpoint_path.is_file(),
        "presync checkpoint path is not a file",
    )
    checkpoint = read_object(checkpoint_path)
    require(
        checkpoint.get("schema")
        == "legislation-presync-audit-checkpoint-v1"
        and checkpoint.get("staged_only") is True
        and checkpoint.get("production_or_mainframe_written") is False,
        "presync checkpoint safety contract drift",
    )
    coverage = checkpoint.get("coverage")
    require(isinstance(coverage, dict), "presync coverage receipt is missing")
    snapshot = coverage.get("billstatus_snapshot_evidence")
    bundle_ref = coverage.get("billstatus_snapshot_bundle")
    require(
        isinstance(snapshot, dict)
        and snapshot.get("status") == "CURRENT"
        and snapshot.get("snapshot_only") is True
        and snapshot.get("network_executed") is False
        and snapshot.get("quorum_used") is False
        and snapshot.get("authoritative_for_discovery") is False
        and snapshot.get("authoritative_for_semantic_exclusion") is False
        and snapshot.get("authoritative_for_sync_or_promotion") is False
        and snapshot.get("issues") == [],
        "presync BILLSTATUS snapshot receipt is not current and safe",
    )
    require(
        isinstance(bundle_ref, dict)
        and bundle_ref.get("status") == "CURRENT"
        and bundle_ref.get("network_executed") is False
        and bundle_ref.get("sync_or_promotion_performed") is False
        and bundle_ref.get("issues") == [],
        "presync BILLSTATUS bundle receipt is not current and safe",
    )

    plan_path, plan_item = _checkpoint_plan_receipt(
        snapshot.get("plan"),
        "presync current BILLSTATUS plan",
    )
    require(
        snapshot.get("plan_sha256") == plan_item["sha256"],
        "presync current BILLSTATUS plan hash binding drift",
    )
    strict_path, strict_item = _checkpoint_plan_receipt(
        snapshot.get("strict_manifest"),
        "presync strict discovery manifest",
    )

    bundle_id = bundle_ref.get("bundle_id")
    require(
        isinstance(bundle_id, str)
        and len(bundle_id) == SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in bundle_id),
        "presync BILLSTATUS bundle id is invalid",
    )
    bundle_dir = resolve_input_path(bundle_ref.get("bundle_dir"))
    expected_bundle_root = (billstatus_root / "snapshots").resolve()
    require(
        bundle_dir.parent == expected_bundle_root
        and bundle_dir.name == bundle_id
        and bundle_dir.is_dir(),
        "presync BILLSTATUS bundle directory drift",
    )
    bundle_receipt_path = resolve_input_path(
        bundle_ref.get("bundle_receipt")
    )
    require(
        bundle_receipt_path == bundle_dir / "bundle_receipt.json"
        and bundle_receipt_path.is_file()
        and resolve_input_path(bundle_ref.get("snapshot_plan"))
        == plan_path
        == bundle_dir / "snapshot_plan.json",
        "presync BILLSTATUS bundle path binding drift",
    )

    bundle = read_object(bundle_receipt_path)
    require(
        bundle.get("schema") == SNAPSHOT_REFRESH.SCHEMA
        and bundle.get("bundle_id") == bundle_id
        and bundle.get("immutable") is True
        and bundle.get("snapshot_only") is True
        and bundle.get("network_executed") is False
        and bundle.get("candidate_exclusions_performed") is False
        and bundle.get("candidate_deletions_performed") is False
        and bundle.get("sync_or_promotion_performed") is False,
        "immutable BILLSTATUS bundle safety contract drift",
    )
    bundle_plan = (bundle.get("outputs") or {}).get("snapshot_plan")
    bundle_plan_path, bundle_plan_item = validate_file_receipt(
        bundle_plan,
        "immutable bundle snapshot plan",
    )
    require(
        bundle_plan_path == plan_path and bundle_plan_item == plan_item,
        "immutable bundle plan receipt drift",
    )

    plan = read_object(plan_path)
    plan_strict = (
        (plan.get("snapshot_contract") or {}).get("strict_manifest") or {}
    )
    require(
        plan.get("schema")
        == "govinfo-billstatus-enrichment-fetch-plan-v1"
        and plan.get("primary_discovery")
        == "Congress.gov Advanced Search - Legislation"
        and plan.get("quorum_used") is False
        and plan.get("network_executed") is False
        and plan.get("production_write_authorized") is False
        and Path(str(plan_strict.get("path") or "")).resolve() == strict_path
        and plan_strict.get("bytes") == strict_item["bytes"]
        and plan_strict.get("sha256") == strict_item["sha256"],
        "current immutable BILLSTATUS plan safety/input drift",
    )

    bundle_inputs = bundle.get("inputs")
    require(
        isinstance(bundle_inputs, dict),
        "immutable BILLSTATUS bundle inputs are missing",
    )
    exclusion_provenance = bundle_inputs.get("exclusion_provenance")
    require(
        isinstance(exclusion_provenance, list),
        "immutable BILLSTATUS exclusion provenance is missing",
    )
    expected_bundle_id, expected_inputs = SNAPSHOT_REFRESH.input_address(
        strict_manifest_path=strict_path,
        records_path=resolve_input_path(
            (bundle_inputs.get("advanced_search_records") or {}).get("path")
        ),
        memberships_path=resolve_input_path(
            (bundle_inputs.get("advanced_search_memberships") or {}).get(
                "path"
            )
        ),
        exclusion_ledger_path=resolve_input_path(
            (bundle_inputs.get("exclusion_ledger") or {}).get("path")
        ),
        exclusion_provenance_paths=tuple(
            resolve_input_path((item or {}).get("path"))
            for item in exclusion_provenance
        ),
        cumulative_manifest_path=resolve_input_path(
            (bundle_inputs.get("cumulative_manifest") or {}).get("path")
        ),
        registry_path=resolve_input_path(
            (bundle_inputs.get("query_registry") or {}).get("path")
        ),
        review_ledger_path=resolve_input_path(
            (bundle_inputs.get("membership_review") or {}).get("path")
        ),
        gap_ledger_path=resolve_input_path(
            (bundle_inputs.get("coverage_gap_ledger") or {}).get("path")
        ),
    )
    require(
        expected_bundle_id == bundle_id
        and expected_inputs == bundle_inputs,
        "immutable BILLSTATUS bundle content address drift",
    )
    SNAPSHOT_REFRESH.validate_bundle(
        bundle_dir,
        expected_bundle_id=bundle_id,
        expected_inputs=expected_inputs,
        strict_manifest_path=strict_path,
    )
    return plan_path


def default_worker_path() -> Path:
    plan_path = current_snapshot_plan_path()
    current = read_object(plan_path)
    current_tasks = {
        str(row.get("canonical_key") or "")
        for row in current.get("record_tasks") or []
        if isinstance(row, dict)
    }
    current_unsupported = {
        str(row.get("canonical_key") or "")
        for row in current.get("unsupported_candidates") or []
        if isinstance(row, dict)
    }
    current_candidate_hash = (
        (current.get("inputs") or {})
        .get("advanced_search_records", {})
        .get("sha256")
    )
    candidates: list[Path] = []
    run_root = _billstatus_audit_root() / "fetch_runs"
    for path in run_root.glob("*/run_manifest.json"):
        try:
            worker = read_object(path)
        except ContractError:
            continue
        if worker.get("status") != "COMPLETE":
            continue
        worker_plan_path = resolve_input_path(
            (worker.get("plan") or {}).get("path")
        )
        if not worker_plan_path.is_file():
            continue
        worker_plan = read_object(worker_plan_path)
        worker_tasks = {
            str(row.get("canonical_key") or "")
            for row in worker_plan.get("record_tasks") or []
            if isinstance(row, dict)
        }
        worker_unsupported = {
            str(row.get("canonical_key") or "")
            for row in worker_plan.get("unsupported_candidates") or []
            if isinstance(row, dict)
        }
        worker_candidate_hash = (
            (worker_plan.get("inputs") or {})
            .get("advanced_search_records", {})
            .get("sha256")
        )
        if (
            worker_tasks == current_tasks
            and worker_unsupported == current_unsupported
            and worker_candidate_hash == current_candidate_hash
        ):
            candidates.append(path.resolve())
    require(
        len(candidates) == 1,
        f"expected one complete current BILLSTATUS worker, found {len(candidates)}",
    )
    return candidates[0]


def input_build_key(chain: dict[str, Any], producer_item: dict[str, Any]) -> str:
    return canonical_json_sha256(
        {
            "schema": SCHEMA,
            "extraction_contract": EXTRACTION_CONTRACT,
            "producer_sha256": producer_item["sha256"],
            "worker_manifest_sha256": chain["worker_item"]["sha256"],
            "snapshot_plan_sha256": chain["plan_item"]["sha256"],
            "advanced_search_candidates_sha256": chain["candidate_item"][
                "sha256"
            ],
            "exclusion_ledger_sha256": chain["exclusion_ledger"]["sha256"],
            "exclusion_provenance_sha256": [
                item["sha256"] for item in chain["exclusion_provenance"]
            ],
        }
    )


def maybe_reuse_pointer(
    *,
    pointer_path: Path | None,
    out_root: Path,
    build_key: str,
    chain: dict[str, Any],
) -> tuple[Path, dict[str, Any]] | None:
    if pointer_path is None or not pointer_path.is_file():
        return None
    pointer = read_object(pointer_path)
    require(
        pointer.get("schema") == POINTER_SCHEMA,
        "structured-relevance pointer schema drift",
    )
    if pointer.get("build_key") != build_key:
        return None
    sidecar_id = pointer.get("sidecar_id")
    require(
        isinstance(sidecar_id, str) and len(sidecar_id) == SHA256_HEX_LENGTH,
        "structured-relevance pointer identity drift",
    )
    out_dir = (out_root / sidecar_id).resolve()
    require(
        str(out_dir) == str(Path(pointer.get("sidecar_dir", "")).resolve()),
        "structured-relevance pointer directory drift",
    )
    # A reused output must still prove that the staged official XML has not
    # changed underneath its unchanged manifest.
    verify_all_raw_hashes(chain)
    manifest = validate_existing_sidecar(
        out_dir,
        expected_sidecar_id=sidecar_id,
        expected_build_key=build_key,
    )
    return out_dir, manifest


def build_sidecar(
    *,
    worker_path: Path,
    out_root: Path,
    pointer_path: Path | None = None,
    enforce_workspace_audits: bool = True,
) -> tuple[Path, dict[str, Any], bool]:
    worker_path = worker_path.resolve()
    out_root = out_root.resolve()
    if pointer_path is not None:
        pointer_path = pointer_path.resolve()
    if enforce_workspace_audits:
        audit_root = (ROOT / "audits").resolve()
        require(
            out_root != audit_root and out_root.is_relative_to(audit_root),
            "structured sidecar output must remain below workspace audits",
        )

    chain = validate_worker_and_plan(worker_path)
    producer_item = file_item(Path(__file__).resolve())
    build_key = input_build_key(chain, producer_item)
    out_root.mkdir(parents=True, exist_ok=True)
    reused = maybe_reuse_pointer(
        pointer_path=pointer_path,
        out_root=out_root,
        build_key=build_key,
        chain=chain,
    )
    if reused is not None:
        out_dir, manifest = reused
        return out_dir, manifest, False

    section_totals: dict[str, Counter[str]] = {
        name: Counter()
        for name, _path, _selector in SECTION_SPECS
    }
    measure_count = 0
    missing_count = 0
    empty_count = 0
    unresolved_count = 0
    with tempfile.TemporaryDirectory(
        prefix=f".{build_key[:16]}.", dir=out_root
    ) as temporary_name:
        temporary = Path(temporary_name).resolve()
        measures_path = temporary / "measures.ndjson"
        missing_path = temporary / "missing_sections.ndjson"
        empty_path = temporary / "empty_sections.ndjson"
        unresolved_path = temporary / "unresolved_amendments.ndjson"

        with (
            measures_path.open("xb") as measures_handle,
            missing_path.open("xb") as missing_handle,
            empty_path.open("xb") as empty_handle,
        ):
            for key in sorted(chain["tasks"], key=key_sort):
                measure, missing, empty, per_section = parse_measure(
                    key=key,
                    task=chain["tasks"][key],
                    receipt=chain["receipts"][key],
                    candidate=chain["candidates"][key],
                    worker_item=chain["worker_item"],
                )
                write_row(measures_handle, measure)
                measure_count += 1
                for gap in missing:
                    write_row(missing_handle, gap)
                    missing_count += 1
                for gap in empty:
                    write_row(empty_handle, gap)
                    empty_count += 1
                for section_name, counts in per_section.items():
                    section_totals[section_name].update(counts)

        with unresolved_path.open("xb") as unresolved_handle:
            for key in sorted(chain["unsupported"], key=key_sort):
                source = chain["unsupported"][key]
                write_row(
                    unresolved_handle,
                    {
                        "schema": AMENDMENT_SCHEMA,
                        "canonical_key": key,
                        "advanced_search_candidate": chain["candidates"][key],
                        "status": "retained_unresolved",
                        "reason": (
                            "nested amendment has no supported standalone "
                            "top-level GovInfo BILLSTATUS package"
                        ),
                        "advanced_search_line": source[
                            "advanced_search_line"
                        ],
                        "advanced_search_line_sha256": source[
                            "advanced_search_line_sha256"
                        ],
                        "standalone_billstatus_record_supported": False,
                        "candidate_retained": True,
                        "candidate_created": False,
                        "relationship_resolution_performed": False,
                        "semantic_decision": None,
                        "automatic_scoring_authorized": False,
                        "ratification_authority": False,
                    },
                )
                unresolved_count += 1

        outputs = {
            "measures": output_item(
                measures_path, record_count=measure_count
            ),
            "missing_sections": output_item(
                missing_path, record_count=missing_count
            ),
            "empty_sections": output_item(
                empty_path, record_count=empty_count
            ),
            "unresolved_amendments": output_item(
                unresolved_path, record_count=unresolved_count
            ),
        }
        section_counts = {
            name: {
                "present": section_totals[name]["present"],
                "present_empty": section_totals[name]["present_empty"],
                "missing": section_totals[name]["missing"],
                "item_count": section_totals[name]["item_count"],
                "leaf_text_count": section_totals[name][
                    "leaf_text_count"
                ],
            }
            for name, _path, _selector in SECTION_SPECS
        }
        coverage = {
            "selected_candidate_count": len(chain["candidates"]),
            "top_level_planned": len(chain["tasks"]),
            "top_level_structured": measure_count,
            "top_level_failed": 0,
            "nested_amendments_retained_unresolved": unresolved_count,
            "candidate_partition_closed": (
                measure_count + unresolved_count == len(chain["candidates"])
            ),
            "raw_hashes_validated": True,
            "every_top_level_has_exactly_one_receipt": True,
            "all_amendment_candidates_unresolved": (
                unresolved_count == len(chain["unsupported"])
            ),
        }
        manifest: dict[str, Any] = {
            "schema": SCHEMA,
            "status": "COMPLETE_STAGED_ONLY",
            "build_key": build_key,
            "extraction_contract": EXTRACTION_CONTRACT,
            "inputs": {
                "worker_manifest": chain["worker_item"],
                "snapshot_plan": chain["plan_item"],
                "advanced_search_candidates": chain["candidate_item"],
                "exclusion_ledger": chain["exclusion_ledger"],
                "exclusion_provenance": chain["exclusion_provenance"],
            },
            "producer": producer_item,
            "outputs": outputs,
            "coverage": coverage,
            "section_counts": section_counts,
            "missing_section_entry_count": missing_count,
            "empty_section_entry_count": empty_count,
            "review_prefill_only": True,
            "absence_is_neutral": True,
            "network_executed": False,
            "production_write_performed": False,
            "sync_or_promotion_performed": False,
            "candidate_creation_performed": False,
            "semantic_inclusion_performed": False,
            "semantic_exclusion_performed": False,
            "automatic_scoring_performed": False,
            "ratification_performed": False,
            "scoring_authority": False,
            "ratification_authority": False,
            "acceptance_passed": False,
            "acceptance_blocker": (
                "nested amendment candidates remain unresolved and no "
                "semantic review authority is asserted"
            ),
        }
        sidecar_id = canonical_json_sha256(
            manifest_content_identity(manifest)
        )
        manifest["sidecar_id"] = sidecar_id
        manifest_path = temporary / "manifest.json"
        manifest_path.write_bytes(pretty_json_bytes(manifest))
        out_dir = (out_root / sidecar_id).resolve()
        if out_dir.exists():
            existing = validate_existing_sidecar(
                out_dir,
                expected_sidecar_id=sidecar_id,
                expected_build_key=build_key,
            )
            manifest = existing
        else:
            os.replace(temporary, out_dir)

    if pointer_path is not None:
        pointer = {
            "schema": POINTER_SCHEMA,
            "build_key": build_key,
            "sidecar_id": manifest["sidecar_id"],
            "sidecar_dir": str(out_dir),
            "manifest": file_item(out_dir / "manifest.json"),
            "review_prefill_only": True,
            "sync_or_promotion_performed": False,
        }
        atomic_write(pointer_path, pretty_json_bytes(pointer))
    return out_dir, manifest, True


def parse_args() -> argparse.Namespace:
    default_root = (
        ROOT
        / "audits"
        / "legfix5"
        / "billstatus_bulk_enrichment"
        / "structured_relevance_sidecars"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--out-root", type=Path, default=default_root)
    parser.add_argument(
        "--pointer",
        type=Path,
        default=(
            ROOT
            / "audits"
            / "legfix5"
            / "billstatus_bulk_enrichment"
            / "structured_relevance_current.json"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    worker_path = (
        args.worker.resolve()
        if args.worker is not None
        else default_worker_path()
    )
    out_dir, manifest, created = build_sidecar(
        worker_path=worker_path,
        out_root=args.out_root,
        pointer_path=args.pointer,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "created": created,
                "sidecar_id": manifest["sidecar_id"],
                "sidecar_dir": str(out_dir),
                "coverage": manifest["coverage"],
                "missing_section_entry_count": manifest[
                    "missing_section_entry_count"
                ],
                "empty_section_entry_count": manifest[
                    "empty_section_entry_count"
                ],
                "network_executed": False,
                "sync_or_promotion_performed": False,
                "acceptance_passed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

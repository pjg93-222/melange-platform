# -*- coding: utf-8 -*-
"""Read-only reconciliation of the existing LDA sweep corpus.

The established LDA collector remains the only collector.  This module audits
its page manifests and inventories filing UUIDs without producing a competing
raw store.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


class LdaReconciliationError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sweep_tree_inventory(
    sweeps_root: str | Path, *, expected_years: Iterable[int]
) -> dict[str, Any]:
    """Seal the exact legacy sweep bytes so a new generation cannot mutate them."""

    root = Path(sweeps_root)
    years = tuple(sorted(set(expected_years)))
    present = {
        int(path.name) for path in root.iterdir()
        if path.is_dir() and path.name.isdigit()
    }
    if present != set(years):
        raise LdaReconciliationError(
            f"LDA inventory years mismatch: expected={list(years)} present={sorted(present)}"
        )
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    per_year: list[dict[str, Any]] = []
    for year in years:
        directory = root / str(year)
        files = sorted(
            [directory / "sweep_manifest.json", *directory.glob("page_*.json")],
            key=lambda path: path.name,
        )
        year_digest = hashlib.sha256()
        year_bytes = 0
        for path in files:
            if not path.is_file():
                raise LdaReconciliationError(f"missing LDA inventory file: {path}")
            size = path.stat().st_size
            file_sha = _sha256_file(path)
            descriptor = f"{year}/{path.name}\0{size}\0{file_sha}\n".encode("utf-8")
            digest.update(descriptor)
            year_digest.update(descriptor)
            file_count += 1
            total_bytes += size
            year_bytes += size
        per_year.append({
            "year": year,
            "files": len(files),
            "bytes": year_bytes,
            "inventory_sha256": year_digest.hexdigest(),
        })
    return {
        "years": list(years),
        "files": file_count,
        "bytes": total_bytes,
        "inventory_sha256": digest.hexdigest(),
        "per_year": per_year,
    }


def md5_file(path: Path) -> str:
    digest = hashlib.md5()  # Existing LDA sweep manifests use MD5 as their wire contract.
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_sweep_year(year_directory: str | Path) -> tuple[dict[str, Any], set[str]]:
    directory = Path(year_directory)
    manifest_path = directory / "sweep_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    advertised = manifest.get("md5")
    if not isinstance(advertised, dict) or not advertised:
        raise LdaReconciliationError(f"{directory.name}: empty sweep manifest")
    if manifest.get("complete") is not True:
        raise LdaReconciliationError(f"{directory.name}: sweep is not marked complete")

    try:
        directory_year = int(directory.name)
    except ValueError as exc:
        raise LdaReconciliationError(f"{directory.name}: year directory is not numeric") from exc
    manifest_year_matches = manifest.get("filing_year") == directory_year
    manifest_page_count_matches = manifest.get("page_count") == len(advertised)

    page_files = {path.name: path for path in directory.glob("page_*.json")}
    advertised_names = set(advertised)
    missing = sorted(advertised_names - set(page_files))
    extra = sorted(set(page_files) - advertised_names)
    mismatched: list[str] = []
    filing_count = 0
    first_locations: dict[str, tuple[str, int]] = {}
    duplicate_filing_ids = 0
    identical_duplicate_occurrences = 0
    conflicting_duplicate_occurrences = 0
    filing_year_mismatches = 0
    source_counts: list[int] = []
    for name in sorted(advertised_names & set(page_files)):
        path = page_files[name]
        if md5_file(path) != advertised[name]:
            mismatched.append(name)
            continue
        page = json.loads(path.read_text(encoding="utf-8"))
        source_count = page.get("count")
        if not isinstance(source_count, int) or isinstance(source_count, bool) or source_count < 0:
            raise LdaReconciliationError(f"{directory.name}/{name}: invalid API count")
        source_counts.append(source_count)
        results = page.get("results")
        if not isinstance(results, list):
            raise LdaReconciliationError(f"{directory.name}/{name}: results is not a list")
        filing_count += len(results)
        for index, filing in enumerate(results):
            if not isinstance(filing, dict) or not filing.get("filing_uuid"):
                raise LdaReconciliationError(
                    f"{directory.name}/{name}:{index}: missing filing_uuid"
                )
            filing_uuid = str(filing["filing_uuid"])
            if filing.get("filing_year") != directory_year:
                filing_year_mismatches += 1
            if filing_uuid in first_locations:
                duplicate_filing_ids += 1
                first_name, first_index = first_locations[filing_uuid]
                first_page = json.loads(page_files[first_name].read_text(encoding="utf-8"))
                first_filing = first_page["results"][first_index]
                if first_filing == filing:
                    identical_duplicate_occurrences += 1
                else:
                    conflicting_duplicate_occurrences += 1
            else:
                first_locations[filing_uuid] = (name, index)

    source_count_first = source_counts[0] if source_counts else None
    source_count_final = source_counts[-1] if source_counts else None
    source_count_values = sorted(set(source_counts))
    unique_filing_count = len(first_locations)
    unique_shortfall = (
        source_count_final - unique_filing_count
        if isinstance(source_count_final, int)
        else None
    )
    page_integrity = not missing and not extra and not mismatched
    source_inventory_consistent = (
        len(source_count_values) == 1
        and unique_shortfall == 0
        and duplicate_filing_ids == 0
        and filing_year_mismatches == 0
    )

    receipt = {
        "filing_year": manifest.get("filing_year"),
        "directory_year": directory_year,
        "manifest_year_matches": manifest_year_matches,
        "manifest_page_count_matches": manifest_page_count_matches,
        "manifest_pages": len(advertised_names),
        "disk_pages": len(page_files),
        "missing_pages": missing,
        "extra_pages": extra,
        "hash_mismatch_pages": mismatched,
        "filing_objects": filing_count,
        "unique_filing_uuids": unique_filing_count,
        "duplicate_filing_uuid_occurrences": duplicate_filing_ids,
        "identical_duplicate_occurrences": identical_duplicate_occurrences,
        "conflicting_duplicate_occurrences": conflicting_duplicate_occurrences,
        "filing_year_mismatches": filing_year_mismatches,
        "source_count_first": source_count_first,
        "source_count_final": source_count_final,
        "source_count_min": min(source_counts) if source_counts else None,
        "source_count_max": max(source_counts) if source_counts else None,
        "source_count_distinct_values": source_count_values,
        "source_count_drift": len(source_count_values) > 1,
        "unique_shortfall_vs_final_count": unique_shortfall,
        "page_integrity_pass": page_integrity,
        "source_inventory_pass": source_inventory_consistent,
        "pass": (
            page_integrity
            and manifest_year_matches
            and manifest_page_count_matches
            and source_inventory_consistent
        ),
    }
    return receipt, set(first_locations)


def audit_sweep_year(year_directory: str | Path) -> dict[str, Any]:
    receipt, _ = _audit_sweep_year(year_directory)
    return receipt


def audit_sweeps(sweeps_root: str | Path, *, expected_years: Iterable[int]) -> dict[str, Any]:
    root = Path(sweeps_root)
    expected = tuple(sorted(set(expected_years)))
    if not expected:
        raise LdaReconciliationError("expected_years must be nonempty")
    present = {
        int(directory.name)
        for directory in root.iterdir()
        if directory.is_dir()
        and directory.name.isdigit()
        and (directory / "sweep_manifest.json").is_file()
    }
    if present != set(expected):
        raise LdaReconciliationError(
            f"LDA year inventory mismatch: expected={list(expected)} present={sorted(present)}"
        )
    years: list[dict[str, Any]] = []
    global_ids: set[str] = set()
    cross_year_duplicate_occurrences = 0
    for year in expected:
        directory = root / str(year)
        receipt, filing_ids = _audit_sweep_year(directory)
        cross_year_duplicate_occurrences += len(global_ids & filing_ids)
        global_ids.update(filing_ids)
        years.append(receipt)
    if not years:
        raise LdaReconciliationError("no LDA sweep manifests found")
    return {
        "years": years,
        "manifest_pages": sum(row["manifest_pages"] for row in years),
        "disk_pages": sum(row["disk_pages"] for row in years),
        "filing_objects": sum(row["filing_objects"] for row in years),
        "unique_filing_uuids": len(global_ids),
        "unique_filing_uuids_within_year_sum": sum(row["unique_filing_uuids"] for row in years),
        "cross_year_duplicate_uuid_occurrences": cross_year_duplicate_occurrences,
        "duplicate_filing_uuid_occurrences": sum(
            row["duplicate_filing_uuid_occurrences"] for row in years
        ),
        "source_count_first_sum": sum(row["source_count_first"] for row in years),
        "source_count_final_sum": sum(row["source_count_final"] for row in years),
        "unique_shortfall_vs_final_count": sum(
            row["unique_shortfall_vs_final_count"] for row in years
        ),
        "pass": all(row["pass"] for row in years) and cross_year_duplicate_occurrences == 0,
    }


def lda_reconciliation_receipt(
    audit: dict[str, Any], *, generated_at: str, source_root_label: str
) -> dict[str, Any]:
    """Convert an audit result into the registered immutable receipt shape."""

    return {
        "schema_id": "staffer-network-lda-reconciliation-receipt-v1",
        "generated_at": generated_at,
        "source_root": source_root_label,
        "manifest_pages": audit["manifest_pages"],
        "disk_pages": audit["disk_pages"],
        "filing_objects": audit["filing_objects"],
        "unique_filing_uuids": audit["unique_filing_uuids"],
        "duplicate_filing_uuid_occurrences": audit["duplicate_filing_uuid_occurrences"],
        "identical_duplicate_occurrences": sum(
            row["identical_duplicate_occurrences"] for row in audit["years"]
        ),
        "conflicting_duplicate_occurrences": sum(
            row["conflicting_duplicate_occurrences"] for row in audit["years"]
        ),
        "cross_year_duplicate_uuid_occurrences": audit["cross_year_duplicate_uuid_occurrences"],
        "source_count_first_sum": audit["source_count_first_sum"],
        "source_count_final_sum": audit["source_count_final_sum"],
        "unique_shortfall_vs_first_count": audit["source_count_first_sum"] - audit["unique_filing_uuids"],
        "unique_shortfall_vs_final_count": audit["unique_shortfall_vs_final_count"],
        "page_integrity_pass": all(row["page_integrity_pass"] for row in audit["years"]),
        "c5_pass": audit["pass"],
        "per_year": [
            {
                "year": row["directory_year"],
                "pages": row["manifest_pages"],
                "filing_objects": row["filing_objects"],
                "unique_filing_uuids": row["unique_filing_uuids"],
                "duplicates": row["duplicate_filing_uuid_occurrences"],
                "source_count_first": row["source_count_first"],
                "source_count_final": row["source_count_final"],
                "source_count_drift": row["source_count_drift"],
                "pass": row["pass"],
            }
            for row in audit["years"]
        ],
        "disposition": (
            "PASS"
            if audit["pass"]
            else "QUARANTINE_SOURCE: page/hash integrity may pass, but duplicate UUIDs, "
            "source-count drift, or unique coverage remain unreconciled."
        ),
    }

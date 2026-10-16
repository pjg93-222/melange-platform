#!/usr/bin/env python3
"""Merge historical recall-only hits into the lossless category corpus."""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from lane_file_io import read_ndjson_text
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import read_ndjson_text


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = (
    ROOT / "audits" / "legfix5" / "congress_advanced_pages_20260725"
)
DEFAULT_HISTORICAL = (
    ROOT / "audits" / "legfix5" / "historical_probe_pages_20260725"
)
DEFAULT_HISTORICAL_REGISTRY = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_historical_probe_registry.json"
)
DEFAULT_OUT = (
    ROOT / "audits" / "legfix5" / "congress_advanced_final_20260725"
)
TRUSTED_RECALL_QUERY_ID = "trusted_source_recall"
TRUSTED_RECALL_LABEL = "Archive and Trusted-Article Recall"
VISIBLE_CANDIDATE_DISPOSITIONS = frozenset({"included_unreviewed_candidate"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-run", type=Path, default=DEFAULT_BASE)
    parser.add_argument(
        "--historical-run", type=Path, default=DEFAULT_HISTORICAL
    )
    parser.add_argument(
        "--historical-registry",
        type=Path,
        default=DEFAULT_HISTORICAL_REGISTRY,
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in read_ndjson_text(path).splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(body + ("\n" if body else ""), encoding="utf-8")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def file_receipt(path: Path, *, root: Path) -> dict[str, Any]:
    body = path.read_bytes()
    return {
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    values = Counter(str(row[field]) for row in rows)
    return dict(sorted(values.items(), key=lambda item: item[0]))


def main() -> int:
    args = parse_args()
    base_dir = args.base_run.resolve()
    historical_dir = args.historical_run.resolve()
    out_dir = args.out.resolve()
    if not str(out_dir).startswith(str(ROOT)):
        raise ValueError("Output must stay within the repository root")
    base_manifest = read_json(base_dir / "manifest.json")
    historical_manifest = read_json(historical_dir / "manifest.json")
    historical_registry = read_json(args.historical_registry.resolve())
    if base_manifest["status"] != "complete":
        raise RuntimeError("Base category run is not complete")
    if historical_manifest["status"] != "complete":
        raise RuntimeError("Historical probe run is not complete")

    base_records = read_ndjson(
        base_dir / base_manifest["union"]["records_file"]
    )
    base_memberships = read_ndjson(
        base_dir / base_manifest["union"]["memberships_file"]
    )
    historical_records = read_ndjson(
        historical_dir / historical_manifest["union"]["records_file"]
    )
    historical_by_key = {
        row["canonical_key"]: row for row in historical_records
    }
    expected_by_probe = {
        row["id"]: set(row["expected_canonical_keys"])
        for row in historical_registry["categories"]
    }
    source_kinds_by_key: dict[str, set[str]] = {}
    trusted_article_references_by_key: dict[
        str, list[dict[str, Any]]
    ] = {}
    for probe in historical_registry["categories"]:
        for term in probe["query_terms"]:
            canonical_key = term["canonical_key"]
            source_kinds_by_key.setdefault(canonical_key, set()).update(
                term.get("source_kinds", [])
            )
            trusted_article_references_by_key.setdefault(
                canonical_key, []
            ).extend(term.get("trusted_article_references", []))
    all_expected = set().union(*expected_by_probe.values())
    probe_query_meta = {
        row["query_id"]: row for row in historical_manifest["queries"]
    }
    per_probe_failures: list[dict[str, Any]] = []
    for query_id, expected in expected_by_probe.items():
        artifact = read_json(
            historical_dir / probe_query_meta[query_id]["query_file"]
        )
        actual = {row["canonical_key"] for row in artifact["records"]}
        if actual != expected:
            per_probe_failures.append(
                {
                    "query_id": query_id,
                    "expected_count": len(expected),
                    "actual_count": len(actual),
                    "missing": sorted(expected - actual),
                    "unexpected": sorted(actual - expected),
                }
            )
    if per_probe_failures:
        failure_path = out_dir / "historical_probe_failures.json"
        write_json(failure_path, per_probe_failures)
        raise RuntimeError(
            f"Historical probe exact-cite validation failed; see {failure_path}"
        )
    if set(historical_by_key) != all_expected:
        raise RuntimeError(
            "Historical probe union does not equal the archive key universe"
        )

    record_map = {
        row["canonical_key"]: copy.deepcopy(row) for row in base_records
    }
    base_keys = set(record_map)
    recovery_only_keys = sorted(all_expected - base_keys)
    for canonical_key in sorted(all_expected):
        if canonical_key in record_map:
            record = record_map[canonical_key]
            probes = set(record.get("matched_coverage_probes", []))
            probes.add(TRUSTED_RECALL_QUERY_ID)
            record["matched_coverage_probes"] = sorted(probes)
            continue
        source = copy.deepcopy(historical_by_key[canonical_key])
        source["matched_scoring_categories"] = []
        source["matched_monitor_categories"] = []
        source["matched_coverage_probes"] = [TRUSTED_RECALL_QUERY_ID]
        source["candidate_status"] = (
            "included_trusted_source_recall_unclassified_candidate"
        )
        record_map[canonical_key] = source
    records = sorted(
        record_map.values(),
        key=lambda row: (
            row["congress"],
            row["type"],
            row["number"],
        ),
    )

    membership_map = {
        (row["query_id"], row["canonical_key"]): row
        for row in base_memberships
    }
    for canonical_key in sorted(all_expected):
        record = record_map[canonical_key]
        membership = {
            "query_id": TRUSTED_RECALL_QUERY_ID,
            "label": TRUSTED_RECALL_LABEL,
            "scoring_category": False,
            "record_kind": "coverage_probe",
            "canonical_key": canonical_key,
            "source_page": None,
            "source_rank": None,
            "result_url": record["result_url"],
        }
        membership_map[(membership["query_id"], canonical_key)] = membership
    memberships = sorted(
        membership_map.values(),
        key=lambda row: (row["query_id"], row["canonical_key"]),
    )

    base_gaps = read_ndjson(base_dir / "coverage_gap_ledger.ndjson")
    recovery_gaps = [
        {
            "gap_kind": "trusted_source_recall_only",
            "canonical_key": key,
            "display_number": record_map[key]["display_number"],
            "title": record_map[key].get("title")
            or record_map[key].get("purpose"),
            "result_url": record_map[key]["result_url"],
            "trusted_recall_source_kinds": sorted(
                source_kinds_by_key.get(key, set())
            ),
            "trusted_article_references": (
                trusted_article_references_by_key.get(key, [])
            ),
            "disposition": "included_unreviewed_candidate",
            "explanation": (
                "Present in the historical archive and/or frozen trusted "
                "press corpus and recovered by an exact Congress.gov "
                "congressId+cite Advanced Search probe, but absent from every "
                "outline category query."
            ),
        }
        for key in recovery_only_keys
    ]
    gaps = [*base_gaps, *recovery_gaps]
    unresolved_gap_count = sum(
        row.get("disposition") not in VISIBLE_CANDIDATE_DISPOSITIONS
        for row in gaps
    )
    if unresolved_gap_count:
        raise RuntimeError(
            f"{unresolved_gap_count} coverage gap(s) carry an unresolved "
            "disposition; refusing to write a merged manifest with zero gaps"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    records_path = out_dir / "congress_advanced_records.ndjson"
    memberships_path = out_dir / "congress_advanced_memberships.ndjson"
    gaps_path = out_dir / "coverage_gap_ledger.ndjson"
    exclusions_path = out_dir / "exclusion_ledger.ndjson"
    scope_path = out_dir / "scope_and_exclusion_contract.json"
    write_ndjson(records_path, records)
    write_ndjson(memberships_path, memberships)
    write_ndjson(gaps_path, gaps)
    write_ndjson(exclusions_path, [])
    scope = read_json(base_dir / "scope_and_exclusion_contract.json")
    scope["trusted_source_recall"] = (
        "Every Congress-qualified historical archive ID and every in-scope "
        "bill citation in the frozen trusted press corpus was re-queried "
        "using Congress.gov congressId+cite Advanced Search syntax. "
        "Recall-only records are included as unclassified candidates."
    )
    write_json(scope_path, scope)

    historical_query_artifact = {
        "schema": "congress-advanced-search-query-v1",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "primary_instrument": "Congress.gov Advanced Search - Legislation",
        "query_id": TRUSTED_RECALL_QUERY_ID,
        "label": TRUSTED_RECALL_LABEL,
        "scoring_category": False,
        "record_kind": "coverage_probe",
        "result_total": len(all_expected),
        "page_count": sum(
            row["page_count"] for row in historical_manifest["queries"]
        ),
        "unique_record_count": len(all_expected),
        "counts_by_congress": counts(historical_records, "congress"),
        "counts_by_type": counts(historical_records, "type"),
        "validation": {
            "exact_result_total": True,
            "unique_result_total": True,
            "every_page_exact": True,
            "every_expected_cite_exact": True,
        },
        "source_probe_manifest": str(
            (historical_dir / "manifest.json").relative_to(ROOT)
        ).replace("\\", "/"),
        "records": historical_records,
        "records_sha256": stable_hash(historical_records),
    }
    query_path = out_dir / "queries" / "trusted_source_recall.json"
    write_json(query_path, historical_query_artifact)
    synthetic_query = {
        key: value
        for key, value in historical_query_artifact.items()
        if key not in {"records", "schema", "completed_at", "source_probe_manifest"}
    }
    synthetic_query["query_file"] = str(query_path.relative_to(out_dir)).replace(
        "\\", "/"
    )

    manifest = copy.deepcopy(base_manifest)
    manifest["schema"] = "acu-congress-advanced-merged-manifest-v1"
    manifest["status"] = "complete"
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["base_manifest"] = str(
        (base_dir / "manifest.json").relative_to(ROOT)
    ).replace("\\", "/")
    manifest["historical_probe_manifest"] = str(
        (historical_dir / "manifest.json").relative_to(ROOT)
    ).replace("\\", "/")
    manifest["selected_query_ids"] = [
        *base_manifest["selected_query_ids"],
        TRUSTED_RECALL_QUERY_ID,
    ]
    manifest["queries"] = [*base_manifest["queries"], synthetic_query]
    manifest["coverage_gap_count"] = unresolved_gap_count
    manifest["observed_recovered_gap_count"] = len(gaps)
    manifest["coverage_evidence"] = {
        "missed_candidates_ledger": file_receipt(gaps_path, root=out_dir),
        "exclusion_ledger": file_receipt(exclusions_path, root=out_dir),
        "scope_and_exclusion_contract": file_receipt(
            scope_path, root=out_dir
        ),
        "unresolved_gap_count": unresolved_gap_count,
        "recovered_candidates_remain_visible": True,
        "semantic_exclusions_are_never_automatic": True,
    }
    manifest["union"] = {
        "unique_record_count": len(records),
        "membership_count": len(memberships),
        "counts_by_congress": counts(records, "congress"),
        "counts_by_type": counts(records, "type"),
        "scoring_candidate_count": sum(
            bool(row.get("matched_scoring_categories")) for row in records
        ),
        "monitor_candidate_count": sum(
            bool(row.get("matched_monitor_categories")) for row in records
        ),
        "recall_probe_count": sum(
            bool(row.get("matched_coverage_probes")) for row in records
        ),
        "records_file": records_path.name,
        "records_sha256": stable_hash(records),
        "memberships_file": memberships_path.name,
        "memberships_sha256": stable_hash(memberships),
        "coverage_visibility": {
            "trusted_source_recall_total": len(all_expected),
            "trusted_source_recovery_only_count": len(
                recovery_only_keys
            ),
            "historical_archive_total": historical_registry.get(
                "historical_archive_record_count", len(all_expected)
            ),
            "trusted_article_congress_qualified_record_count": (
                historical_registry.get(
                    "trusted_article_congress_qualified_record_count", 0
                )
            ),
            "automated_semantic_exclusion_count": 0,
            "gap_ledger_file": gaps_path.name,
            "exclusion_ledger_file": exclusions_path.name,
            "scope_contract_file": scope_path.name,
        },
    }
    write_json(out_dir / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "output": str(out_dir),
                "base_unique_records": len(base_records),
                "historical_archive_records": len(all_expected),
                "historical_recall_only_added": len(recovery_only_keys),
                "final_unique_records": len(records),
                "final_memberships": len(memberships),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Reconcile individual Congress.gov identifier probes to category unions."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATEGORY_RUN = (
    ROOT / "audits" / "legfix5" / "congress_advanced_pages_20260725"
)
DEFAULT_PROBE_RUN = (
    ROOT / "audits" / "legfix5" / "identifier_probe_pages_20260725"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--category-run", type=Path, default=DEFAULT_CATEGORY_RUN
    )
    parser.add_argument("--probe-run", type=Path, default=DEFAULT_PROBE_RUN)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def main() -> int:
    args = parse_args()
    category_run = args.category_run.resolve()
    probe_run = args.probe_run.resolve()
    out_dir = (
        args.out.resolve()
        if args.out
        else probe_run / "reconciliation"
    )
    category_manifest = read_json(category_run / "manifest.json")
    probe_manifest = read_json(probe_run / "manifest.json")
    if category_manifest["status"] != "complete":
        raise RuntimeError("Category Advanced Search run is not complete")
    if probe_manifest["status"] != "complete":
        raise RuntimeError("Identifier probe Advanced Search run is not complete")

    category_records: dict[str, set[str]] = {}
    for query in category_manifest["queries"]:
        query_artifact = read_json(category_run / query["query_file"])
        category_records[query["query_id"]] = {
            row["canonical_key"] for row in query_artifact["records"]
        }

    audit_rows: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    missing_edges: list[dict[str, Any]] = []
    for query in probe_manifest["queries"]:
        artifact = read_json(probe_run / query["query_file"])
        parent_id = artifact.get("parent_category_id")
        if not parent_id or parent_id not in category_records:
            raise ValueError(
                f"Identifier probe lacks a valid parent category: "
                f"{artifact['query_id']} -> {parent_id}"
            )
        probe_keys = {row["canonical_key"] for row in artifact["records"]}
        missing = sorted(probe_keys - category_records[parent_id])
        audit_rows.append(
            {
                "probe_id": artifact["query_id"],
                "parent_category_id": parent_id,
                "parent_category_label": artifact.get(
                    "parent_category_label"
                ),
                "identifier_text": artifact.get("identifier_text"),
                "source_paragraph": artifact.get("source_paragraph"),
                "result_total": artifact["result_total"],
                "unique_record_count": artifact["unique_record_count"],
                "zero_yield": artifact["result_total"] == 0,
                "parent_category_union_count": len(
                    category_records[parent_id]
                ),
                "missing_from_parent_count": len(missing),
                "missing_from_parent_keys": missing,
                "query_file": query["query_file"],
                "exact_page_reconciliation": all(
                    artifact["validation"].values()
                ),
            }
        )
        for row in artifact["records"]:
            edge = {
                "canonical_key": row["canonical_key"],
                "display_number": row["display_number"],
                "title": row.get("title") or row.get("purpose"),
                "parent_category_id": parent_id,
                "probe_id": artifact["query_id"],
                "identifier_text": artifact.get("identifier_text"),
                "source_paragraph": artifact.get("source_paragraph"),
                "source_rank": row["source_rank"],
                "result_url": row["result_url"],
            }
            edges.append(edge)
            if row["canonical_key"] in missing:
                missing_edges.append(edge)

    audit_rows.sort(
        key=lambda row: (row["parent_category_id"], row["probe_id"])
    )
    edges.sort(
        key=lambda row: (
            row["parent_category_id"],
            row["probe_id"],
            row["canonical_key"],
        )
    )
    missing_edges.sort(
        key=lambda row: (
            row["parent_category_id"],
            row["probe_id"],
            row["canonical_key"],
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    edges_path = out_dir / "identifier_record_memberships.ndjson"
    missing_path = out_dir / "identifier_hits_missing_from_category.ndjson"
    write_ndjson(edges_path, edges)
    write_ndjson(missing_path, missing_edges)
    counts = Counter(row["parent_category_id"] for row in audit_rows)
    report = {
        "schema": "acu-legislation-identifier-reconciliation-v1",
        "primary_instrument": "Congress.gov Advanced Search - Legislation",
        "probe_count": len(audit_rows),
        "parent_category_count": len(counts),
        "probes_by_parent_category": dict(sorted(counts.items())),
        "zero_yield_probe_count": sum(row["zero_yield"] for row in audit_rows),
        "zero_yield_probes": [
            {
                "probe_id": row["probe_id"],
                "parent_category_id": row["parent_category_id"],
                "identifier_text": row["identifier_text"],
                "source_paragraph": row["source_paragraph"],
            }
            for row in audit_rows
            if row["zero_yield"]
        ],
        "identifier_membership_count": len(edges),
        "missing_from_parent_count": len(missing_edges),
        "exact_page_reconciliation": all(
            row["exact_page_reconciliation"] for row in audit_rows
        ),
        "containment_passed": not missing_edges,
        "acceptance_passed": (
            len(audit_rows) == 210
            and all(row["exact_page_reconciliation"] for row in audit_rows)
            and not missing_edges
        ),
        "audit_rows": audit_rows,
        "identifier_memberships_file": str(
            edges_path.relative_to(ROOT)
        ).replace("\\", "/"),
        "missing_hits_file": str(missing_path.relative_to(ROOT)).replace(
            "\\", "/"
        ),
    }
    write_json(out_dir / "identifier_reconciliation_report.json", report)
    print(
        json.dumps(
            {
                "probe_count": report["probe_count"],
                "zero_yield_probe_count": report["zero_yield_probe_count"],
                "identifier_membership_count": len(edges),
                "missing_from_parent_count": len(missing_edges),
                "acceptance": (
                    "PASS" if report["acceptance_passed"] else "FAIL"
                ),
            },
            indent=2,
        )
    )
    return 0 if report["acceptance_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

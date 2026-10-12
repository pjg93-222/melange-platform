#!/usr/bin/env python3
"""Build Congress.gov Advanced Search probes for every historical archive ID."""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from legislation.cross_reference_legislation_press_articles import load_trusted_articles


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = ROOT / "congress_bills_archive_20260624_115822.xlsx"
DEFAULT_REGISTRY = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_historical_probe_registry.json"
)
DEFAULT_TRUSTED_MANIFEST = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_trusted_press_article_manifest.json"
)
TYPE_PREFIXES = [
    ("H.Con.Res.", "hconres"),
    ("S.Con.Res.", "sconres"),
    ("H.J.Res.", "hjres"),
    ("S.J.Res.", "sjres"),
    ("H.Amdt.", "hamdt"),
    ("S.Amdt.", "samdt"),
    ("H.Res.", "hres"),
    ("S.Res.", "sres"),
    ("H.R.", "hr"),
    ("S.", "s"),
]


def legislation_key(congress: Any, display_number: Any) -> tuple[str, str] | None:
    try:
        congress_number = int(congress)
    except (TypeError, ValueError):
        return None
    compact = re.sub(r"\s+", "", str(display_number or ""))
    for prefix, kind in TYPE_PREFIXES:
        compact_prefix = prefix.replace(" ", "")
        if compact.lower().startswith(compact_prefix.lower()):
            number = compact[len(compact_prefix) :]
            if number.isdigit():
                canonical = f"{congress_number}:{kind}:{int(number)}"
                cite = f"{kind}{int(number)}"
                return canonical, cite
    return None


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument(
        "--trusted-manifest",
        type=Path,
        default=DEFAULT_TRUSTED_MANIFEST,
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chunk-size", type=int, default=40)
    args = parser.parse_args()
    if args.chunk_size < 1 or args.chunk_size > 100:
        raise ValueError("--chunk-size must be from 1 to 100")

    registry = json.loads(args.registry.resolve().read_text(encoding="utf-8"))
    archive = pd.read_excel(args.archive.resolve(), sheet_name="All Bills")
    known: dict[str, dict[str, Any]] = {}
    for row in archive.itertuples(index=False):
        parsed = legislation_key(row.Congress, row.Name)
        if not parsed:
            continue
        canonical, cite = parsed
        congress = int(row.Congress)
        known[canonical] = {
            "canonical_key": canonical,
            "congress": congress,
            "cite": cite,
            "display_number": str(row.Name).strip(),
            "source_kinds": ["historical_archive"],
            "trusted_article_references": [],
        }
    archive_keys = set(known)
    articles, trusted_article_source_audit = load_trusted_articles(
        json.loads(
            args.trusted_manifest.resolve().read_text(encoding="utf-8")
        ),
        registry,
    )
    trusted_article_keys: set[str] = set()
    for article in articles:
        congress = article.get("article_congress")
        if congress is None or int(congress) not in set(
            registry["collection_contract"]["congresses"]
        ):
            continue
        for citation in article.get("citations", []):
            cite = f"{citation['bill_type']}{int(citation['number'])}"
            canonical = (
                f"{int(congress)}:{citation['bill_type']}:"
                f"{int(citation['number'])}"
            )
            trusted_article_keys.add(canonical)
            if canonical not in known:
                known[canonical] = {
                    "canonical_key": canonical,
                    "congress": int(congress),
                    "cite": cite,
                    "display_number": cite,
                    "source_kinds": ["trusted_press_article"],
                    "trusted_article_references": [],
                }
            elif "trusted_press_article" not in known[canonical][
                "source_kinds"
            ]:
                known[canonical]["source_kinds"].append(
                    "trusted_press_article"
                )
            known[canonical]["trusted_article_references"].append(
                {
                    "article_id": article["article_id"],
                    "article_date": article.get("date"),
                    "article_url": article.get("canonical_article_url")
                    or article.get("url"),
                    "matched_citation_text": citation.get("matched_text"),
                }
            )
    ordered = sorted(
        known.values(),
        key=lambda row: (
            row["congress"],
            row["cite"],
            row["canonical_key"],
        ),
    )
    required_congresses = set(
        registry["collection_contract"]["congresses"]
    )
    source_counts_by_congress = Counter(
        row["congress"] for row in ordered
    )
    missing_source_congresses = sorted(
        required_congresses - set(source_counts_by_congress)
    )
    probes: list[dict[str, Any]] = []
    for index, group in enumerate(chunks(ordered, args.chunk_size), start=1):
        query_terms = [
            {
                "kind": "trusted_source_congress_qualified_cite",
                "query": (
                    f'(congressId:{row["congress"]} '
                    f'AND cite:"{row["cite"]}")'
                ),
                "canonical_key": row["canonical_key"],
                "source_kinds": row["source_kinds"],
                "trusted_article_references": row[
                    "trusted_article_references"
                ],
            }
            for row in group
        ]
        probes.append(
            {
                "id": f"trusted_source_recall_{index:03d}",
                "label": f"Archive and Trusted-Article Recall {index:03d}",
                "scoring_category": False,
                "record_kind": "trusted_source_recall_probe",
                "query_terms": query_terms,
                "advanced_search_query": " OR ".join(
                    f"({term['query']})" for term in query_terms
                ),
                "expected_canonical_keys": [
                    row["canonical_key"] for row in group
                ],
            }
        )
    output = {
        "schema": "acu-congress-trusted-source-recall-probes-v2",
        "source_archive": str(args.archive.resolve().relative_to(ROOT)).replace(
            "\\", "/"
        ),
        "source_registry": str(args.registry.resolve().relative_to(ROOT)).replace(
            "\\", "/"
        ),
        "historical_archive_record_count": len(archive_keys),
        "trusted_article_congress_qualified_record_count": len(
            trusted_article_keys
        ),
        "combined_trusted_recall_record_count": len(ordered),
        "historical_record_count": len(ordered),
        "trusted_article_manifest": str(
            args.trusted_manifest.resolve().relative_to(ROOT)
        ).replace("\\", "/"),
        "trusted_article_source_audit": trusted_article_source_audit,
        "historical_source_counts_by_congress": {
            str(congress): source_counts_by_congress.get(congress, 0)
            for congress in sorted(required_congresses)
        },
        "historical_source_missing_required_congresses": (
            missing_source_congresses
        ),
        "historical_source_is_not_six_year_coverage_authority": True,
        "historical_source_gap_note": (
            "Missing Congresses are an explicit archive-regression gap; "
            "six-year discovery coverage is established independently by the "
            "Congress.gov Advanced Search category and recall queries."
            if missing_source_congresses
            else None
        ),
        "chunk_size": args.chunk_size,
        "collection_contract": registry["collection_contract"],
        "category_count": len(probes),
        "scoring_category_count": 0,
        "coverage_probes": [],
        "categories": probes,
    }
    args.out.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.out.resolve().write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.out.resolve()),
                "historical_records": len(ordered),
                "historical_archive_records": len(archive_keys),
                "trusted_article_congress_qualified_records": len(
                    trusted_article_keys
                ),
                "probe_chunks": len(probes),
                "chunk_size": args.chunk_size,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

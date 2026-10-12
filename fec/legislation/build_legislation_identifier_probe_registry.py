#!/usr/bin/env python3
"""Expand each outline identifier into its own Congress.gov audit query."""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_identifier_probe_registry.json"
)


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned[:45] or "term"


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    registry = json.loads(args.registry.resolve().read_text(encoding="utf-8"))
    probes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for category in registry["categories"]:
        for index, identifier in enumerate(category["identifiers"], start=1):
            text = identifier["text"]
            probe_id = (
                f"idprobe_{category['id']}_{index:03d}_{slug(text)}"
            )
            if probe_id in seen_ids:
                raise ValueError(f"Duplicate probe id: {probe_id}")
            seen_ids.add(probe_id)
            probes.append(
                {
                    "id": probe_id,
                    "label": f"{category['label']} :: {text}",
                    "scoring_category": False,
                    "record_kind": "identifier_probe",
                    "parent_category_id": category["id"],
                    "parent_category_label": category["label"],
                    "identifier_index": index,
                    "source_paragraph": identifier.get("source_paragraph"),
                    "source_text": identifier.get("source_text"),
                    "identifier_text": text,
                    "query_terms": [
                        {
                            "kind": "outline_identifier",
                            "query": identifier["text"],
                        }
                    ],
                    "advanced_search_query": f"({identifier['text']})",
                }
            )

    output = {
        "schema": "acu-congress-advanced-identifier-probes-v1",
        "source_registry": str(args.registry.resolve().relative_to(ROOT)).replace(
            "\\", "/"
        ),
        "source_registry_sha256": stable_hash(registry),
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
                "identifier_probes": len(probes),
                "zero_yield_visibility": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

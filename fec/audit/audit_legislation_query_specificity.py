#!/usr/bin/env python3
"""Audit that generic/ambiguous Act aliases cannot assert category membership."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_search_registry.json"
)
DEFAULT_MANIFEST = (
    ROOT
    / "audits"
    / "legfix5"
    / "congress_advanced_pages_20260725"
    / "manifest.partial.21228.json"
)
DEFAULT_OUT = (
    ROOT
    / "audits"
    / "legfix5"
    / "query_specificity"
    / "current"
    / "query_specificity_audit.json"
)
PRIMARY = "Congress.gov Advanced Search - Legislation"
STOP = {
    "a",
    "an",
    "the",
    "of",
    "and",
    "or",
    "for",
    "to",
    "in",
    "on",
    "act",
}


def file_receipt(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def short_act_alias(value: str) -> bool:
    tokens = re.findall(r"[A-Za-z0-9]+", value.casefold())
    distinctive = {token for token in tokens if token not in STOP}
    return "act" in tokens and len(distinctive) < 3


def quoted_title(value: str) -> str:
    return '"' + value.replace('"', r"\"") + '"'


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def audit(
    registry_path: Path,
    *,
    selected_manifest_path: Path | None = None,
) -> dict[str, Any]:
    registry_path = registry_path.resolve()
    registry = load_object(registry_path)
    categories = registry.get("categories")
    probes = registry.get("coverage_probes")
    if not isinstance(categories, list) or not isinstance(probes, list):
        raise ValueError("registry lacks categories/coverage_probes arrays")

    blockers: list[dict[str, Any]] = []
    category_by_id: dict[str, dict[str, Any]] = {}
    for row in categories:
        category_id = str(row.get("id") or "")
        if not category_id or category_id in category_by_id:
            blockers.append(
                {"code": "missing_or_duplicate_category_id", "id": category_id}
            )
            continue
        category_by_id[category_id] = row

    probe_by_id: dict[str, dict[str, Any]] = {}
    alias_rows: list[dict[str, Any]] = []
    for probe in probes:
        probe_id = str(probe.get("id") or "")
        if not probe_id or probe_id in probe_by_id:
            blockers.append(
                {"code": "missing_or_duplicate_probe_id", "id": probe_id}
            )
            continue
        probe_by_id[probe_id] = probe
        title = probe.get("title_alias")
        if not isinstance(title, str) or not title.strip():
            effective_non_dispositive = bool(
                probe.get("scoring_category") is False
                and probe.get("record_kind") == "coverage_probe"
            )
            if not effective_non_dispositive:
                blockers.append(
                    {
                        "code": "non_title_probe_can_assert_membership",
                        "probe_id": probe_id,
                    }
                )
            continue

        parent_id = str(probe.get("parent_category_id") or "")
        parent = category_by_id.get(parent_id)
        computed_ambiguous = short_act_alias(title)
        expected_query = quoted_title(title)
        expected_terms = [
            {
                "kind": (
                    "ambiguous_title_alias_probe"
                    if computed_ambiguous
                    else "title_alias_probe"
                ),
                "query": expected_query,
            }
        ]
        exact_probe_contract = bool(
            probe.get("scoring_category") is False
            and probe.get("record_kind") == "coverage_probe"
            and probe.get("required_for_acceptance") is True
            and probe.get("membership_non_dispositive") is True
            and probe.get("ambiguous_short_act_alias") is computed_ambiguous
            and probe.get("query_terms") == expected_terms
            and probe.get("advanced_search_query")
            == f"({expected_query})"
        )
        if not exact_probe_contract:
            blockers.append(
                {
                    "code": "title_probe_contract_drift",
                    "probe_id": probe_id,
                    "title_alias": title,
                }
            )
        if parent is None:
            blockers.append(
                {
                    "code": "title_probe_parent_missing",
                    "probe_id": probe_id,
                    "parent_category_id": parent_id,
                }
            )
            parent_terms: list[dict[str, Any]] = []
            parent_titles: list[str] = []
        else:
            parent_terms = list(parent.get("query_terms") or [])
            parent_titles = list(parent.get("title_aliases") or [])
            if title not in parent_titles:
                blockers.append(
                    {
                        "code": "title_alias_missing_from_parent_inventory",
                        "probe_id": probe_id,
                    }
                )
        title_term_present = any(
            term.get("kind") == "known_title"
            and term.get("query") == expected_query
            for term in parent_terms
            if isinstance(term, dict)
        )
        if computed_ambiguous and title_term_present:
            blockers.append(
                {
                    "code": "ambiguous_act_leaked_into_scoring_query",
                    "probe_id": probe_id,
                    "parent_category_id": parent_id,
                }
            )
        if not computed_ambiguous and not title_term_present:
            blockers.append(
                {
                    "code": "specific_title_missing_from_parent_query",
                    "probe_id": probe_id,
                    "parent_category_id": parent_id,
                }
            )
        alias_rows.append(
            {
                "probe_id": probe_id,
                "parent_category_id": parent_id,
                "title_alias": title,
                "ambiguous_short_act_alias": computed_ambiguous,
                "membership_non_dispositive": (
                    probe.get("membership_non_dispositive") is True
                ),
                "present_in_parent_scoring_query": title_term_present,
                "exact_isolated_probe": exact_probe_contract,
            }
        )

    for category in categories:
        for term in category.get("query_terms") or []:
            if not isinstance(term, dict):
                blockers.append(
                    {
                        "code": "malformed_category_query_term",
                        "category_id": category.get("id"),
                    }
                )
                continue
            text = str(term.get("query") or "").strip().strip('"')
            if text.casefold() == "act" or (
                term.get("kind") == "known_title"
                and short_act_alias(text)
            ):
                blockers.append(
                    {
                        "code": "generic_or_ambiguous_act_in_category_query",
                        "category_id": category.get("id"),
                        "query": term.get("query"),
                    }
                )

    expected_total = len(categories) + len(probes)
    if expected_total != 62:
        blockers.append(
            {
                "code": "required_query_registry_not_62",
                "observed": expected_total,
            }
        )
    if registry.get("collection_contract", {}).get("quorum_used") is not False:
        blockers.append({"code": "quorum_not_explicitly_forbidden"})

    manifest_state: dict[str, Any] | None = None
    if selected_manifest_path is not None:
        selected_manifest_path = selected_manifest_path.resolve()
        manifest = load_object(selected_manifest_path)
        completed = manifest.get("queries")
        if not isinstance(completed, list):
            raise ValueError("selected manifest lacks queries array")
        known_ids = set(category_by_id) | set(probe_by_id)
        observed_ids = [str(row.get("query_id") or "") for row in completed]
        if (
            manifest.get("primary_instrument") != PRIMARY
            or manifest.get("quorum_used") is not False
            or manifest.get("registry_sha256")
            != file_receipt(registry_path)["sha256"]
            or len(observed_ids) != len(set(observed_ids))
            or not set(observed_ids).issubset(known_ids)
        ):
            blockers.append({"code": "selected_manifest_registry_contract_drift"})
        completed_ambiguous = 0
        for row in completed:
            query_id = str(row.get("query_id") or "")
            probe = probe_by_id.get(query_id)
            if not probe or not probe.get("ambiguous_short_act_alias"):
                continue
            completed_ambiguous += 1
            if not (
                row.get("scoring_category") is False
                and row.get("record_kind") == "coverage_probe"
                and row.get("membership_non_dispositive") is True
                and row.get("ambiguous_short_act_alias") is True
            ):
                blockers.append(
                    {
                        "code": "completed_ambiguous_probe_became_dispositive",
                        "query_id": query_id,
                    }
                )
        manifest_state = {
            **file_receipt(selected_manifest_path),
            "completed_query_count": len(completed),
            "completed_ambiguous_title_probe_count": completed_ambiguous,
        }

    ambiguous = [row for row in alias_rows if row["ambiguous_short_act_alias"]]
    specific = [row for row in alias_rows if not row["ambiguous_short_act_alias"]]
    return {
        "schema": "legislation-query-specificity-audit-v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "primary_discovery": PRIMARY,
        "quorum_used": False,
        "semantic_exclusions_performed": False,
        "inputs": {
            "registry": file_receipt(registry_path),
            "selected_strict_manifest": manifest_state,
        },
        "counts": {
            "category_queries": len(categories),
            "coverage_probes": len(probes),
            "required_queries": expected_total,
            "title_alias_probes": len(alias_rows),
            "ambiguous_short_act_probes": len(ambiguous),
            "specific_title_probes": len(specific),
            "ambiguous_aliases_in_category_queries": sum(
                row["present_in_parent_scoring_query"] for row in ambiguous
            ),
            "generic_bare_act_category_terms": sum(
                blocker["code"] == "generic_or_ambiguous_act_in_category_query"
                for blocker in blockers
            ),
        },
        "ambiguous_title_aliases": ambiguous,
        "specific_title_aliases": specific,
        "acceptance_rule": (
            "Every title alias has an isolated non-scoring exact-title probe. "
            "Ambiguous short-Act aliases never assert category membership; "
            "official title/provision review is required for promotion."
        ),
        "blockers": blockers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument(
        "--selected-manifest", type=Path, default=DEFAULT_MANIFEST
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = audit(
        args.registry,
        selected_manifest_path=args.selected_manifest,
    )
    out = args.out.resolve()
    out.relative_to(ROOT)
    out.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{out.name}.", suffix=".tmp", dir=out.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, out)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(
        json.dumps(
            {
                "status": result["status"],
                "out": str(out),
                **result["counts"],
                "blocker_count": len(result["blockers"]),
            },
            indent=2,
        )
    )
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Add official structured BILLSTATUS evidence to a partial review prefill.

The output is a content-addressed, staged-only overlay.  It has exactly one
row for every existing membership-review proposal, but it cannot change a
membership, create an inclusion/exclusion decision, ratify a proposal, score
legislation, or write to production.  Missing sections and unmatched phrases
are neutral.  Unsupported amendment candidates are copied byte-for-byte from
the hardened prefill and remain unresolved.

Identifier matching is deliberately local to a single official XML leaf.
Phrases never span fields or elements.  Each match records the exact section,
source XPath, parsed-fragment hash, raw-record hash, and element path.  Short
or generic Act-name matches are exposed as context-only evidence and can never
be counted as an independent review signal.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
from typing import Any, Iterable, Iterator

from legislation import review_legislation_candidate_memberships as REVIEW

try:
    from lane_file_io import open_ndjson
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import open_ndjson


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREFILL_POINTER = (
    ROOT / "audits" / "legfix5" / "partial_review_prefill" / "current.json"
)
DEFAULT_STRUCTURED_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "billstatus_bulk_enrichment"
    / "structured_relevance_current.json"
)
DEFAULT_REGISTRY = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_search_registry.json"
)
DEFAULT_OUT_ROOT = (
    ROOT
    / "audits"
    / "legfix5"
    / "partial_review_structured_augmentation"
    / "snapshots"
)
DEFAULT_CURRENT = (
    ROOT
    / "audits"
    / "legfix5"
    / "partial_review_structured_augmentation"
    / "current.json"
)
DEFAULT_ROLLOVER_CHECKPOINT = (
    ROOT
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "presync_current"
    / "checkpoint.json"
)

SCHEMA = "legislation-partial-review-structured-augmentation-manifest-v1"
ROW_SCHEMA = (
    "legislation-partial-review-structured-evidence-addition-v1"
)
POINTER_SCHEMA = (
    "legislation-partial-review-structured-augmentation-pointer-v1"
)
PRIMARY = "Congress.gov Advanced Search - Legislation"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"[a-z0-9]+")
PROXIMITY_RE = re.compile(r'^"([^"]+)"\s*~\s*\d+$')

SECTION_ORDER = (
    "policy_area",
    "legislative_subjects",
    "committees",
    "related_bills",
    "amendments",
    "laws",
    "committee_reports",
    "cbo_cost_estimates",
)

# Matching is restricted to semantically relevant leaf types in each source
# section.  This prevents dates, URLs, procedural labels, and arbitrary XML
# codes from becoming accidental category evidence.
SECTION_MATCH_POLICIES: dict[str, dict[str, Any]] = {
    "policy_area": {
        "allowed_leaf_tags": ("name",),
        "identifier_phrases": True,
        "title_aliases": True,
        "known_bill_identity": False,
    },
    "legislative_subjects": {
        "allowed_leaf_tags": ("name",),
        "identifier_phrases": True,
        "title_aliases": True,
        "known_bill_identity": False,
    },
    "committees": {
        "allowed_leaf_tags": ("name",),
        "identifier_phrases": True,
        "title_aliases": True,
        "known_bill_identity": False,
    },
    "related_bills": {
        "allowed_leaf_tags": ("title",),
        "identifier_phrases": True,
        "title_aliases": True,
        "known_bill_identity": True,
    },
    "amendments": {
        "allowed_leaf_tags": (
            "purpose",
            "description",
            "title",
            "text",
        ),
        "identifier_phrases": True,
        "title_aliases": True,
        "known_bill_identity": False,
    },
    "laws": {
        "allowed_leaf_tags": ("name", "title", "citation"),
        "identifier_phrases": True,
        "title_aliases": True,
        "known_bill_identity": False,
    },
    "committee_reports": {
        "allowed_leaf_tags": ("name", "title", "citation", "text"),
        "identifier_phrases": True,
        "title_aliases": True,
        "known_bill_identity": False,
    },
    "cbo_cost_estimates": {
        "allowed_leaf_tags": ("title", "description"),
        "identifier_phrases": True,
        "title_aliases": True,
        "known_bill_identity": False,
    },
}


class ContractError(RuntimeError):
    """Raised when staged input or immutable output identity drifts."""


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


def collector_json_sha256(value: Any) -> str:
    return sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"Invalid JSON object: {path}") from error
    require(isinstance(value, dict), f"JSON object required: {path}")
    return value


def iter_ndjson(path: Path) -> Iterator[dict[str, Any]]:
    try:
        handle = open_ndjson(path)
    except OSError as error:
        raise ContractError(f"Unable to open NDJSON: {path}") from error
    with handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ContractError(
                    f"Invalid NDJSON at {path}:{line_number}"
                ) from error
            require(
                isinstance(value, dict),
                f"NDJSON object required at {path}:{line_number}",
            )
            yield value


def write_row(handle: Any, value: dict[str, Any]) -> None:
    handle.write(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    handle.write("\n")


def file_item(
    path: Path,
    *,
    record_count: int | None = None,
    relative: bool = False,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if relative:
        item["file"] = path.name
    else:
        item["path"] = str(path.resolve())
    if record_count is not None:
        item["record_count"] = record_count
    return item


def validate_file_item(
    item: Any,
    *,
    label: str,
    base_dir: Path | None = None,
) -> Path:
    require(isinstance(item, dict), f"{label} receipt missing")
    raw_path = item.get("path")
    if raw_path is None and base_dir is not None:
        raw_file = item.get("file")
        require(
            isinstance(raw_file, str)
            and raw_file
            and Path(raw_file).name == raw_file,
            f"{label} relative file drift",
        )
        path = (base_dir / raw_file).resolve()
        try:
            path.relative_to(base_dir.resolve())
        except ValueError as error:
            raise ContractError(f"{label} escapes its directory") from error
    else:
        require(
            isinstance(raw_path, str) and raw_path,
            f"{label} path missing",
        )
        path = Path(raw_path).resolve()
    expected = str(item.get("sha256") or "").casefold()
    require(
        SHA256_RE.fullmatch(expected) is not None,
        f"{label} SHA-256 missing",
    )
    require(path.is_file(), f"{label} missing: {path}")
    require(
        sha256_file(path) == expected,
        f"{label} SHA-256 mismatch",
    )
    expected_bytes = item.get("bytes")
    if expected_bytes is not None:
        require(
            path.stat().st_size == expected_bytes,
            f"{label} byte-count mismatch",
        )
    return path


def manifest_from_pointer(
    pointer_path: Path,
    *,
    schema: str,
    label: str,
) -> Path:
    pointer = read_object(pointer_path.resolve())
    require(
        pointer.get("schema") == schema,
        f"{label} pointer schema drift",
    )
    require(
        pointer.get("sync_or_promotion_performed") is False,
        f"{label} pointer sync authority drift",
    )
    return validate_file_item(
        pointer.get("manifest"),
        label=f"{label} manifest",
    )


def validate_authority_flags(
    value: dict[str, Any],
    *,
    label: str,
    row: bool = False,
) -> None:
    false_fields = (
        "candidate_membership_review_overwritten",
        "candidate_creation_performed",
        "candidate_exclusion_performed",
        "semantic_inclusion_performed",
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
    if row:
        false_fields = (
            "exclusion_performed",
            "membership_disposition_authority",
            "ratification_authority",
            "scoring_authority",
            "usable_as_membership_review_ledger",
            "decision_import_allowed",
            "scoring_input_allowed",
        )
    require(
        all(value.get(field) is False for field in false_fields),
        f"{label} authority drift",
    )
    require(
        value.get("proposal_only") is True,
        f"{label} is not proposal-only",
    )
    require(
        value.get("candidate_retained") is True if row else True,
        f"{label} candidate-retention drift",
    )
    require(
        value.get("scoring_weight") == 0,
        f"{label} scoring-weight drift",
    )


def validate_prefill(
    manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Path]]:
    manifest_path = manifest_path.resolve()
    manifest = read_object(manifest_path)
    require(
        manifest.get("schema")
        == "legislation-partial-review-prefill-manifest-v1"
        and manifest.get("status") == "PASS_NON_RATIFIED_PARTIAL"
        and manifest.get("primary_discovery") == PRIMARY
        and manifest.get("quorum_used") is False
        and manifest.get("partial_discovery_only") is True
        and manifest.get("review_prefill_only") is True
        and manifest.get("human_ratification_required") is True
        and manifest.get("absence_is_neutral") is True,
        "Hardened partial-review prefill contract drift",
    )
    for field in (
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
    ):
        require(
            manifest.get(field) is False,
            f"Partial-review prefill unsafe flag: {field}",
        )
    require(
        manifest.get("scoring_weight") == 0,
        "Partial-review prefill scoring-weight drift",
    )
    outputs = manifest.get("outputs")
    require(isinstance(outputs, dict), "Prefill outputs missing")
    require(
        set(outputs)
        == {
            "selected_records",
            "selected_memberships",
            "membership_review_proposals",
            "unsupported_amendments",
        },
        "Prefill output inventory drift",
    )
    paths = {
        label: validate_file_item(item, label=f"prefill {label}")
        for label, item in outputs.items()
    }
    membership_count = outputs["selected_memberships"].get("record_count")
    proposal_count = outputs["membership_review_proposals"].get(
        "record_count"
    )
    unresolved_count = outputs["unsupported_amendments"].get("record_count")
    require(
        membership_count
        == proposal_count
        == manifest.get("selected_membership_count")
        == manifest.get("proposal_count")
        and manifest.get("all_selected_memberships_have_proposals") is True,
        "Prefill membership/proposal partition drift",
    )
    require(
        unresolved_count == manifest.get("unsupported_amendment_count")
        and manifest.get("all_sidecar_unsupported_amendments_visible")
        is True,
        "Prefill unresolved-amendment partition drift",
    )
    return manifest, paths


def validate_structured_sidecar(
    manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Path]]:
    manifest_path = manifest_path.resolve()
    manifest = read_object(manifest_path)
    require(
        manifest.get("schema")
        == "govinfo-billstatus-structured-relevance-sidecar-v1"
        and manifest.get("status") == "COMPLETE_STAGED_ONLY"
        and manifest.get("review_prefill_only") is True
        and manifest.get("absence_is_neutral") is True
        and manifest.get("network_executed") is False,
        "Structured relevance sidecar contract drift",
    )
    for field in (
        "candidate_creation_performed",
        "semantic_inclusion_performed",
        "semantic_exclusion_performed",
        "ratification_performed",
        "automatic_scoring_performed",
        "sync_or_promotion_performed",
        "production_write_performed",
        "ratification_authority",
        "scoring_authority",
        "acceptance_passed",
    ):
        require(
            manifest.get(field) is False,
            f"Structured sidecar unsafe flag: {field}",
        )
    coverage = manifest.get("coverage")
    require(
        isinstance(coverage, dict)
        and coverage.get("candidate_partition_closed") is True
        and coverage.get("all_amendment_candidates_unresolved") is True
        and coverage.get("every_top_level_has_exactly_one_receipt") is True
        and coverage.get("raw_hashes_validated") is True
        and coverage.get("top_level_failed") == 0
        and coverage.get("selected_candidate_count")
        == coverage.get("top_level_structured")
        + coverage.get("nested_amendments_retained_unresolved"),
        "Structured sidecar candidate partition drift",
    )
    outputs = manifest.get("outputs")
    require(
        isinstance(outputs, dict)
        and set(outputs)
        == {
            "measures",
            "missing_sections",
            "empty_sections",
            "unresolved_amendments",
        },
        "Structured sidecar output inventory drift",
    )
    paths = {
        label: validate_file_item(
            item,
            label=f"structured sidecar {label}",
            base_dir=manifest_path.parent,
        )
        for label, item in outputs.items()
    }
    require(
        outputs["measures"].get("record_count")
        == coverage.get("top_level_structured")
        and outputs["unresolved_amendments"].get("record_count")
        == coverage.get("nested_amendments_retained_unresolved"),
        "Structured sidecar output counts drift",
    )
    inputs = manifest.get("inputs")
    require(
        isinstance(inputs, dict)
        and set(inputs)
        == {
            "advanced_search_candidates",
            "exclusion_ledger",
            "exclusion_provenance",
            "snapshot_plan",
            "worker_manifest",
        }
        and isinstance(inputs["exclusion_provenance"], list),
        "Structured sidecar candidate/provenance input inventory drift",
    )
    for label in (
        "advanced_search_candidates",
        "exclusion_ledger",
        "snapshot_plan",
        "worker_manifest",
    ):
        validate_file_item(
            inputs[label],
            label=f"structured sidecar input {label}",
        )
    for index, item in enumerate(inputs["exclusion_provenance"], 1):
        validate_file_item(
            item,
            label=(
                "structured sidecar exclusion provenance "
                f"receipt {index}"
            ),
        )
    require(
        inputs["advanced_search_candidates"].get("record_count")
        == coverage.get("selected_candidate_count"),
        "Structured sidecar candidate input count drift",
    )
    return manifest, paths


def normalized_tokens(value: Any) -> list[str]:
    return TOKEN_RE.findall(REVIEW.normalize(value).casefold())


def contains_bounded_tokens(value: Any, phrase: Any) -> bool:
    haystack = normalized_tokens(value)
    needle = normalized_tokens(phrase)
    if not haystack or not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(
        haystack[index : index + width] == needle
        for index in range(len(haystack) - width + 1)
    )


def phrase_from_identifier(value: Any) -> str:
    raw = REVIEW.normalize(value)
    query_match = PROXIMITY_RE.fullmatch(raw)
    return REVIEW.normalize(query_match.group(1) if query_match else raw)


def phrase_specificity(
    phrase: str,
    *,
    kind: str,
) -> dict[str, Any]:
    tokens = normalized_tokens(phrase)
    distinctive = sorted(
        {
            token
            for token in tokens
            if token not in REVIEW.GENERIC_EVIDENCE_TOKENS
        }
    )
    contains_act = "act" in tokens
    short_act = (
        kind == "title_alias"
        and contains_act
        and len(distinctive) < 3
    )
    generic_act = contains_act and len(distinctive) < 2
    all_generic = bool(tokens) and not distinctive
    allowed = bool(tokens) and not all_generic
    return {
        "tokens": tokens,
        "distinctive_tokens": distinctive,
        "contains_act": contains_act,
        "short_act": short_act,
        "generic_act": generic_act,
        "all_generic": all_generic,
        "match_allowed": allowed,
        "independent_review_signal_allowed": (
            allowed and not short_act and not generic_act
        ),
        "specificity_class": (
            "rejected_all_generic"
            if all_generic or not tokens
            else (
                "context_only_short_act"
                if short_act
                else (
                    "context_only_generic_act"
                    if generic_act
                    else "bounded_specific_phrase"
                )
            )
        ),
    }


def child_paths(
    node: dict[str, Any],
    *,
    parent_path: str = "",
) -> Iterator[tuple[dict[str, Any], str]]:
    tag = str(node.get("tag") or "")
    require(bool(tag), "Structured section node lacks a tag")
    current_path = f"{parent_path}/{tag}[1]"
    yield node, current_path
    counts: Counter[str] = Counter()
    for child in node.get("children") or []:
        require(
            isinstance(child, dict),
            "Structured section child is not an object",
        )
        child_tag = str(child.get("tag") or "")
        counts[child_tag] += 1
        child_path = f"{current_path}/{child_tag}[{counts[child_tag]}]"
        yield child, child_path
        yield from descendants(child, parent_path=child_path)


def descendants(
    node: dict[str, Any],
    *,
    parent_path: str,
) -> Iterator[tuple[dict[str, Any], str]]:
    counts: Counter[str] = Counter()
    for child in node.get("children") or []:
        require(
            isinstance(child, dict),
            "Structured section child is not an object",
        )
        child_tag = str(child.get("tag") or "")
        counts[child_tag] += 1
        child_path = f"{parent_path}/{child_tag}[{counts[child_tag]}]"
        yield child, child_path
        yield from descendants(child, parent_path=child_path)


def section_leaves(
    section_name: str,
    section: dict[str, Any],
) -> list[dict[str, Any]]:
    policy = SECTION_MATCH_POLICIES[section_name]
    allowed = set(policy["allowed_leaf_tags"])
    compact_leaves = section.get("_match_leaves")
    if isinstance(compact_leaves, list):
        return compact_leaves
    data = section.get("data")
    if section.get("status") != "present" or not isinstance(data, dict):
        return []
    leaves: list[dict[str, Any]] = []
    for node, element_path in child_paths(data):
        tag = str(node.get("tag") or "")
        if tag not in allowed:
            continue
        for value_field, path_suffix in (
            ("text", "text()"),
            ("tail_text", "tail-text()"),
        ):
            text = REVIEW.normalize(node.get(value_field))
            if text:
                leaves.append(
                    {
                        "leaf_tag": tag,
                        "leaf_text": text,
                        "element_path": f"{element_path}/{path_suffix}",
                    }
                )
    return leaves


def known_bill_keys(category: dict[str, Any]) -> dict[str, dict[str, Any]]:
    keys: dict[str, dict[str, Any]] = {}
    for bill in category.get("known_bills") or []:
        if not isinstance(bill, dict):
            continue
        try:
            key = REVIEW.compact_cite_key(
                int(bill["congress"]),
                str(bill["compact_cite"]),
            )
        except (KeyError, TypeError, ValueError):
            key = None
        if key:
            keys[key] = bill
    return keys


def direct_text(node: dict[str, Any], tag: str) -> str:
    for child in node.get("children") or []:
        if child.get("tag") == tag:
            return REVIEW.normalize(child.get("text"))
    return ""


def related_bill_identity_matches(
    section: dict[str, Any],
    category: dict[str, Any],
) -> list[dict[str, Any]]:
    known = known_bill_keys(category)
    compact_items = section.get("_related_bill_items")
    if known and isinstance(compact_items, list):
        return [
            {
                "identifier_kind": "known_bill",
                "identifier_text": str(
                    known[item["canonical_key"]].get("number")
                    or known[item["canonical_key"]].get("compact_cite")
                    or item["canonical_key"]
                ),
                "source_paragraph": known[
                    item["canonical_key"]
                ].get("source_paragraph"),
                "match_rule": "exact_related_bill_canonical_identity",
                "specificity_class": "congress_qualified_known_bill",
                "independent_review_signal": True,
                "matched_leaf_tag": None,
                "matched_leaf_text": item["canonical_key"],
                "element_path": item["element_path"],
            }
            for item in compact_items
            if item["canonical_key"] in known
        ]
    data = section.get("data")
    if not known or section.get("status") != "present" or not isinstance(
        data, dict
    ):
        return []
    matches: list[dict[str, Any]] = []
    item_index = 0
    for child in data.get("children") or []:
        if child.get("tag") != "item":
            continue
        item_index += 1
        try:
            congress = int(direct_text(child, "congress"))
            number = int(direct_text(child, "number"))
            measure_type = direct_text(child, "type").casefold()
        except ValueError:
            continue
        key = f"{congress}:{measure_type}:{number}"
        if key not in known:
            continue
        matches.append(
            {
                "identifier_kind": "known_bill",
                "identifier_text": str(
                    known[key].get("number")
                    or known[key].get("compact_cite")
                    or key
                ),
                "source_paragraph": known[key].get("source_paragraph"),
                "match_rule": "exact_related_bill_canonical_identity",
                "specificity_class": "congress_qualified_known_bill",
                "independent_review_signal": True,
                "matched_leaf_tag": None,
                "matched_leaf_text": key,
                "element_path": (
                    f"/relatedBills[1]/item[{item_index}]"
                ),
            }
        )
    return matches


def category_phrases(
    category: dict[str, Any],
) -> list[dict[str, Any]]:
    phrases: list[dict[str, Any]] = []
    for identifier in category.get("identifiers") or []:
        if not isinstance(identifier, dict):
            continue
        phrase = phrase_from_identifier(identifier.get("text"))
        specificity = phrase_specificity(
            phrase,
            kind="outline_identifier",
        )
        if specificity["match_allowed"]:
            phrases.append(
                {
                    "identifier_kind": "outline_identifier",
                    "identifier_text": REVIEW.normalize(
                        identifier.get("text")
                    ),
                    "match_phrase": phrase,
                    "source_paragraph": identifier.get(
                        "source_paragraph"
                    ),
                    "specificity": specificity,
                }
            )
    for alias in category.get("title_aliases") or []:
        phrase = REVIEW.normalize(alias)
        specificity = phrase_specificity(phrase, kind="title_alias")
        if specificity["match_allowed"]:
            phrases.append(
                {
                    "identifier_kind": "title_alias",
                    "identifier_text": phrase,
                    "match_phrase": phrase,
                    "source_paragraph": None,
                    "specificity": specificity,
                }
            )
    return phrases


def evidence_for_section(
    *,
    category: dict[str, Any],
    section_name: str,
    section: dict[str, Any],
) -> list[dict[str, Any]]:
    policy = SECTION_MATCH_POLICIES[section_name]
    matches: list[dict[str, Any]] = []
    if policy["identifier_phrases"] or policy["title_aliases"]:
        for phrase in category_phrases(category):
            kind = phrase["identifier_kind"]
            if kind == "outline_identifier" and not policy[
                "identifier_phrases"
            ]:
                continue
            if kind == "title_alias" and not policy["title_aliases"]:
                continue
            for leaf in section_leaves(section_name, section):
                if not contains_bounded_tokens(
                    leaf["leaf_text"],
                    phrase["match_phrase"],
                ):
                    continue
                specificity = phrase["specificity"]
                matches.append(
                    {
                        "identifier_kind": kind,
                        "identifier_text": phrase["identifier_text"],
                        "source_paragraph": phrase["source_paragraph"],
                        "match_rule": (
                            "ordered_casefolded_bounded_token_sequence_"
                            "within_single_official_leaf"
                        ),
                        "specificity_class": specificity[
                            "specificity_class"
                        ],
                        "independent_review_signal": specificity[
                            "independent_review_signal_allowed"
                        ],
                        "matched_leaf_tag": leaf["leaf_tag"],
                        "matched_leaf_text": leaf["leaf_text"],
                        "element_path": leaf["element_path"],
                    }
                )
    if policy["known_bill_identity"]:
        matches.extend(related_bill_identity_matches(section, category))

    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for match in matches:
        identity = (
            match["identifier_kind"],
            match["identifier_text"],
            match["match_rule"],
            match["element_path"],
            match["matched_leaf_text"],
        )
        unique[identity] = match
    return [
        unique[key]
        for key in sorted(
            unique,
            key=lambda value: tuple(
                "" if item is None else str(item).casefold()
                for item in value
            ),
        )
    ]


def compact_measure(
    row: dict[str, Any],
) -> dict[str, Any]:
    require(
        row.get("schema")
        == "govinfo-billstatus-structured-relevance-measure-v1",
        "Structured measure row schema drift",
    )
    key = str(row.get("canonical_key") or "")
    sections = row.get("sections")
    require(
        key
        and isinstance(sections, dict)
        and set(sections) == set(SECTION_ORDER),
        f"Structured measure section inventory drift: {key}",
    )
    compact_sections: dict[str, dict[str, Any]] = {}
    for section_name in SECTION_ORDER:
        section = sections[section_name]
        require(
            isinstance(section, dict)
            and section.get("status")
            in {"present", "present_empty", "missing"},
            f"Structured section state drift: {key}/{section_name}",
        )
        compact_section = {
            "status": section.get("status"),
            "source_xpath": section.get("source_xpath"),
            "source_record_sha256": section.get(
                "source_record_sha256"
            ),
            "parsed_fragment_sha256": section.get(
                "parsed_fragment_sha256"
            ),
            "_match_leaves": section_leaves(section_name, section),
        }
        if (
            section_name == "related_bills"
            and section.get("status") == "present"
            and isinstance(section.get("data"), dict)
        ):
            compact_items: list[dict[str, str]] = []
            item_index = 0
            for child in section["data"].get("children") or []:
                if child.get("tag") != "item":
                    continue
                item_index += 1
                try:
                    related_congress = int(
                        direct_text(child, "congress")
                    )
                    related_number = int(direct_text(child, "number"))
                    related_type = direct_text(
                        child, "type"
                    ).casefold()
                except ValueError:
                    continue
                if not related_type:
                    continue
                compact_items.append(
                    {
                        "canonical_key": (
                            f"{related_congress}:{related_type}:"
                            f"{related_number}"
                        ),
                        "element_path": (
                            f"/relatedBills[1]/item[{item_index}]"
                        ),
                    }
                )
            compact_section["_related_bill_items"] = compact_items
        compact_sections[section_name] = compact_section
    provenance = row.get("provenance") or {}
    raw = provenance.get("raw_source") or {}
    require(
        str(raw.get("sha256") or "")
        == next(iter(compact_sections.values())).get(
            "source_record_sha256"
        ),
        f"Structured source hash drift: {key}",
    )
    return {
        "canonical_key": key,
        "sections": compact_sections,
        "official_source": {
            "authority": provenance.get("authority"),
            "source_url": provenance.get("source_url"),
            "raw_source_sha256": raw.get("sha256"),
        },
    }


def load_measures(path: Path, expected_count: int) -> dict[str, dict[str, Any]]:
    measures: dict[str, dict[str, Any]] = {}
    for row in iter_ndjson(path):
        compact = compact_measure(row)
        key = compact["canonical_key"]
        require(key not in measures, f"Duplicate structured measure: {key}")
        measures[key] = compact
    require(
        len(measures) == expected_count,
        "Structured measure count drift",
    )
    return measures


def membership_identity_from_selected(
    row: dict[str, Any],
) -> tuple[str, str]:
    return (
        str(row.get("canonical_key") or ""),
        str(row.get("query_id") or ""),
    )


def membership_identity_from_proposal(
    row: dict[str, Any],
) -> tuple[str, str]:
    return (
        str(row.get("canonical_key") or ""),
        str(row.get("raw_query_id") or ""),
    )


def validate_source_proposal(row: dict[str, Any], index: int) -> None:
    false_fields = (
        "usable_as_membership_review_ledger",
        "decision_import_allowed",
        "scoring_input_allowed",
        "exclusion_performed",
        "membership_disposition_authority",
        "ratification_authority",
        "scoring_authority",
    )
    require(
        row.get("disposition") == "unresolved"
        and row.get("proposed_disposition") is None
        and row.get("ratification_state") == "NOT_RATIFIED"
        and row.get("human_review_required") is True
        and row.get("proposal_only") is True
        and row.get("candidate_retained") is True
        and row.get("scoring_weight") == 0
        and all(row.get(field) is False for field in false_fields),
        f"Source proposal authority drift at row {index}",
    )


def validate_output_row(row: dict[str, Any], index: int) -> None:
    require(
        row.get("schema") == ROW_SCHEMA
        and row.get("evidence_addition_only") is True
        and row.get("human_review_required") is True
        and row.get("proposed_disposition") is None
        and row.get("candidate_retained") is True
        and row.get("absence_is_neutral") is True,
        f"Evidence-addition row contract drift at row {index}",
    )
    validate_authority_flags(
        row,
        label=f"evidence-addition row {index}",
        row=True,
    )
    additions = row.get("official_structured_evidence_additions")
    require(
        isinstance(additions, list),
        f"Evidence additions missing at row {index}",
    )
    for addition in additions:
        require(
            isinstance(addition, dict)
            and addition.get("section") in SECTION_ORDER
            and isinstance(addition.get("source_xpath"), str)
            and str(addition.get("source_xpath")).startswith("bill/")
            and SHA256_RE.fullmatch(
                str(addition.get("source_record_sha256") or "")
            )
            is not None
            and SHA256_RE.fullmatch(
                str(addition.get("parsed_fragment_sha256") or "")
            )
            is not None
            and isinstance(addition.get("element_path"), str)
            and str(addition.get("element_path")).startswith("/")
            and addition.get("proposal_only") is True
            and addition.get("dispositive") is False
            and addition.get("scoring_weight") == 0
            and addition.get("source_record_sha256")
            == row.get("official_source", {}).get(
                "raw_source_sha256"
            ),
            f"Section evidence authority/provenance drift at row {index}",
        )
        if addition.get("specificity_class") in {
            "context_only_short_act",
            "context_only_generic_act",
        }:
            require(
                addition.get("independent_review_signal") is False,
                f"Short/generic Act became dispositive at row {index}",
            )


def category_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    categories = registry.get("categories")
    require(isinstance(categories, list), "Registry categories missing")
    result = {
        str(row.get("id")): row
        for row in categories
        if isinstance(row, dict) and row.get("id")
    }
    require(
        len(result) == len(categories),
        "Registry category IDs missing/duplicated",
    )
    return result


def validate_rollover_binding(
    *,
    checkpoint_path: Path,
    prefill: dict[str, Any],
    prefill_manifest_path: Path | None = None,
    structured: dict[str, Any],
    structured_manifest_path: Path | None = None,
    registry_path: Path,
    prior_augmentation_manifest_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    checkpoint_path = checkpoint_path.resolve()
    checkpoint = read_object(checkpoint_path)
    binding = (
        (checkpoint.get("coverage") or {})
        .get("billstatus_enrichment_completeness_evidence", {})
        .get("semantic_rollover_binding")
    )
    require(
        isinstance(binding, dict)
        and binding.get("schema")
        == "billstatus-semantic-rollover-binding-v1"
        and binding.get("status") == "PASS"
        and binding.get("mode")
        in {"EXACT_PLAN", "SEMANTIC_EQUIVALENCE"}
        and binding.get("query_logical_hashes_equal") is True
        and binding.get("candidate_logical_hashes_equal") is True
        and binding.get("network_reuse_only") is True
        and binding.get("network_executed") is False
        and binding.get("sync_or_promotion_performed") is False,
        "Semantic rollover binding is not a zero-network PASS",
    )
    for field in (
        "authoritative_for_discovery",
        "authoritative_for_semantic_exclusion",
        "authoritative_for_scoring",
        "authoritative_for_sync_or_promotion",
    ):
        require(
            binding.get(field) is False,
            f"Semantic rollover authority drift: {field}",
        )

    current_plan_path = validate_file_item(
        binding.get("current_plan"),
        label="semantic rollover current plan",
    )
    reused_plan_path = validate_file_item(
        binding.get("reused_plan"),
        label="semantic rollover reused plan",
    )
    current_strict_path = validate_file_item(
        binding.get("current_strict_manifest"),
        label="semantic rollover current strict manifest",
    )
    reused_strict_path = validate_file_item(
        binding.get("reused_strict_manifest"),
        label="semantic rollover reused strict manifest",
    )
    require(
        binding["reused_plan"]["sha256"]
        == structured["inputs"]["snapshot_plan"]["sha256"],
        "Rollover reused plan is not the structured sidecar plan",
    )
    prefill_strict_sha = (
        (prefill.get("bindings") or {})
        .get("selected_partial_manifest_sha256")
    )
    prefill_binding_chain = None
    if binding["current_strict_manifest"]["sha256"] != prefill_strict_sha:
        require(
            prefill_manifest_path is not None
            and structured_manifest_path is not None
            and prior_augmentation_manifest_path is not None,
            "Rollover current strict manifest is not the prefill binding",
        )
        prefill_manifest_path = prefill_manifest_path.resolve()
        structured_manifest_path = structured_manifest_path.resolve()
        prior_augmentation_manifest_path = (
            prior_augmentation_manifest_path.resolve()
        )
        prior_manifest = read_object(prior_augmentation_manifest_path)
        prior_manifest = validate_existing(
            prior_augmentation_manifest_path.parent,
            expected_build_key=str(prior_manifest.get("build_key") or ""),
        )
        prior_inputs = prior_manifest.get("inputs") or {}
        for label, item, expected_path in (
            (
                "prior augmentation prefill manifest",
                prior_inputs.get("prefill_manifest"),
                prefill_manifest_path,
            ),
            (
                "prior augmentation structured manifest",
                prior_inputs.get("structured_relevance_manifest"),
                structured_manifest_path,
            ),
            (
                "prior augmentation registry",
                prior_inputs.get("registry"),
                registry_path,
            ),
        ):
            observed_path = validate_file_item(item, label=label)
            require(
                observed_path == expected_path.resolve(),
                f"{label} path drift",
            )
        require(
            prior_inputs["prefill_manifest"]["sha256"]
            == sha256_file(prefill_manifest_path)
            and prior_inputs["structured_relevance_manifest"]["sha256"]
            == sha256_file(structured_manifest_path)
            and prior_inputs["registry"]["sha256"]
            == sha256_file(registry_path),
            "Prior augmentation input receipt drift",
        )
        prior_outputs = prior_manifest.get("outputs") or {}
        prior_binding_path = validate_file_item(
            prior_outputs.get("semantic_rollover_binding"),
            label="prior augmentation semantic rollover binding",
            base_dir=prior_augmentation_manifest_path.parent,
        )
        prior_binding = read_object(prior_binding_path)
        require(
            prior_binding.get("schema")
            == "billstatus-semantic-rollover-binding-v1"
            and prior_binding.get("status") == "PASS"
            and prior_binding.get("query_logical_hashes_equal") is True
            and prior_binding.get("candidate_logical_hashes_equal") is True
            and prior_binding.get("network_reuse_only") is True
            and prior_binding.get("network_executed") is False
            and prior_binding.get("sync_or_promotion_performed") is False,
            "Prior augmentation rollover binding is not a zero-network PASS",
        )
        for field in (
            "authoritative_for_discovery",
            "authoritative_for_semantic_exclusion",
            "authoritative_for_scoring",
            "authoritative_for_sync_or_promotion",
        ):
            require(
                prior_binding.get(field) is False,
                f"Prior augmentation rollover authority drift: {field}",
            )
        prior_current_plan_path = validate_file_item(
            prior_binding.get("current_plan"),
            label="prior rollover current plan",
        )
        prior_reused_plan_path = validate_file_item(
            prior_binding.get("reused_plan"),
            label="prior rollover reused plan",
        )
        prior_current_strict_path = validate_file_item(
            prior_binding.get("current_strict_manifest"),
            label="prior rollover current strict manifest",
        )
        prior_reused_strict_path = validate_file_item(
            prior_binding.get("reused_strict_manifest"),
            label="prior rollover reused strict manifest",
        )
        prior_current_plan = read_object(prior_current_plan_path)
        prior_reused_plan = read_object(prior_reused_plan_path)
        prior_directly_binds_prefill = (
            prior_binding["current_strict_manifest"]["sha256"]
            == prefill_strict_sha
        )
        prior_chain = prior_binding.get("prefill_binding_chain")
        prior_carries_prefill_chain = False
        if not prior_directly_binds_prefill and isinstance(
            prior_chain, dict
        ):
            origin_manifest_path = validate_file_item(
                prior_chain.get("prior_augmentation_manifest"),
                label="carried prefill-chain origin manifest",
            )
            origin_binding_path = validate_file_item(
                prior_chain.get("prior_semantic_rollover_binding"),
                label="carried prefill-chain origin binding",
            )
            origin_binding = read_object(origin_binding_path)
            require(
                origin_manifest_path.name == "manifest.json"
                and origin_binding.get("schema")
                == "billstatus-semantic-rollover-binding-v1"
                and origin_binding.get("status") == "PASS"
                and origin_binding.get("semantic_identity")
                == binding.get("semantic_identity")
                and origin_binding.get("current_strict_manifest", {}).get(
                    "sha256"
                )
                == prefill_strict_sha
                and prior_chain.get("schema")
                == "billstatus-prefill-rollover-chain-v1"
                and prior_chain.get("status") == "PASS"
                and prior_chain.get("prefill_strict_manifest_sha256")
                == prefill_strict_sha
                and prior_chain.get("current_strict_manifest_sha256")
                == prior_binding["current_strict_manifest"]["sha256"]
                and prior_chain.get("semantic_identity_sha256")
                == canonical_json_sha256(binding["semantic_identity"])
                and prior_chain.get("query_logical_hashes_equal") is True
                and prior_chain.get("candidate_logical_hashes_equal") is True
                and prior_chain.get("network_executed") is False
                and prior_chain.get("sync_or_promotion_performed") is False,
                "Carried prefill rollover chain drift",
            )
            for field in (
                "authoritative_for_discovery",
                "authoritative_for_semantic_exclusion",
                "authoritative_for_scoring",
                "authoritative_for_sync_or_promotion",
            ):
                require(
                    origin_binding.get(field) is False
                    and prior_chain.get(field) is False,
                    f"Carried prefill rollover authority drift: {field}",
                )
            prior_carries_prefill_chain = True
            if (
                prior_binding["current_strict_manifest"]["sha256"]
                == binding["current_strict_manifest"]["sha256"]
            ):
                prefill_binding_chain = prior_chain
            else:
                prefill_binding_chain = {
                    **prior_chain,
                    "current_strict_manifest_sha256": binding[
                        "current_strict_manifest"
                    ]["sha256"],
                }
        require(
            (
                prior_directly_binds_prefill
                or prior_carries_prefill_chain
            )
            and prior_binding["reused_plan"]["sha256"]
            == structured["inputs"]["snapshot_plan"]["sha256"]
            and (
                (prior_current_plan.get("snapshot_contract") or {})
                .get("strict_manifest", {})
                .get("sha256")
            )
            == prior_binding["current_strict_manifest"]["sha256"]
            and (
                (prior_reused_plan.get("snapshot_contract") or {})
                .get("strict_manifest", {})
                .get("sha256")
            )
            == prior_binding["reused_strict_manifest"]["sha256"]
            and sha256_file(prior_current_strict_path)
            == prior_binding["current_strict_manifest"]["sha256"]
            and sha256_file(prior_reused_strict_path)
            == prior_binding["reused_strict_manifest"]["sha256"]
            and prior_binding.get("semantic_identity")
            == binding.get("semantic_identity"),
            "Prior/current rollover semantic chain drift",
        )
        if prefill_binding_chain is None:
            prefill_binding_chain = {
                "schema": "billstatus-prefill-rollover-chain-v1",
                "status": "PASS",
                "prior_augmentation_manifest": file_item(
                    prior_augmentation_manifest_path
                ),
                "prior_semantic_rollover_binding": file_item(
                    prior_binding_path
                ),
                "prefill_strict_manifest_sha256": prefill_strict_sha,
                "current_strict_manifest_sha256": binding[
                    "current_strict_manifest"
                ]["sha256"],
                "semantic_identity_sha256": canonical_json_sha256(
                    binding["semantic_identity"]
                ),
                "query_logical_hashes_equal": True,
                "candidate_logical_hashes_equal": True,
                "network_executed": False,
                "authoritative_for_discovery": False,
                "authoritative_for_semantic_exclusion": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
                "sync_or_promotion_performed": False,
            }
    current_plan = read_object(current_plan_path)
    reused_plan = read_object(reused_plan_path)
    require(
        (
            (current_plan.get("snapshot_contract") or {})
            .get("strict_manifest", {})
            .get("sha256")
        )
        == binding["current_strict_manifest"]["sha256"]
        and (
            (reused_plan.get("snapshot_contract") or {})
            .get("strict_manifest", {})
            .get("sha256")
        )
        == binding["reused_strict_manifest"]["sha256"],
        "Rollover plan/strict receipt closure drift",
    )
    require(
        sha256_file(current_strict_path)
        == binding["current_strict_manifest"]["sha256"]
        and sha256_file(reused_strict_path)
        == binding["reused_strict_manifest"]["sha256"],
        "Rollover strict artifact hash drift",
    )

    identity = binding.get("semantic_identity")
    require(
        isinstance(identity, dict)
        and identity.get("schema")
        == "billstatus-plan-semantic-identity-v1"
        and identity.get("primary_discovery") == PRIMARY
        and identity.get("registry_sha256") == sha256_file(registry_path)
        and identity.get("completed_query_count")
        == prefill.get("completed_query_count"),
        "Rollover semantic identity drift",
    )
    query_rows = identity.get("query_logical_receipts")
    candidate_identity = identity.get("candidate_identity")
    require(
        isinstance(query_rows, list)
        and isinstance(candidate_identity, dict),
        "Rollover query/candidate identity is malformed",
    )
    ordered_query_rows = [
        {
            "query_id": row.get("query_id"),
            "records_sha256": row.get("records_sha256"),
            "record_count": row.get("record_count"),
        }
        for row in query_rows
    ]
    raw_partition = candidate_identity.get("candidate_partition")
    ordered_partition = (
        {
            "all": raw_partition.get("all"),
            "top_level_enrichable": raw_partition.get(
                "top_level_enrichable"
            ),
            "nested_only_amendments": raw_partition.get(
                "nested_only_amendments"
            ),
        }
        if isinstance(raw_partition, dict)
        else None
    )
    ordered_candidate = {
        "selected_strict_candidate_count": candidate_identity.get(
            "selected_strict_candidate_count"
        ),
        "cumulative_record_count": candidate_identity.get(
            "cumulative_record_count"
        ),
        "canonical_keys_sha256": candidate_identity.get(
            "canonical_keys_sha256"
        ),
        "aggregate_rows_sha256": candidate_identity.get(
            "aggregate_rows_sha256"
        ),
        "advanced_search_line_receipts_sha256": (
            candidate_identity.get(
                "advanced_search_line_receipts_sha256"
            )
        ),
        "candidate_partition": ordered_partition,
        "record_tasks_sha256": candidate_identity.get(
            "record_tasks_sha256"
        ),
        "unsupported_candidates_sha256": candidate_identity.get(
            "unsupported_candidates_sha256"
        ),
    }
    cumulative = identity.get("cumulative_identity")
    ordered_cumulative = (
        {
            "record_count": cumulative.get("record_count"),
            "records_sha256": cumulative.get("records_sha256"),
            "membership_count": cumulative.get("membership_count"),
            "memberships_sha256": cumulative.get(
                "memberships_sha256"
            ),
        }
        if isinstance(cumulative, dict)
        else None
    )
    semantic_body = {
        "schema": identity.get("schema"),
        "primary_discovery": identity.get("primary_discovery"),
        "registry_sha256": identity.get("registry_sha256"),
        "completed_query_count": identity.get(
            "completed_query_count"
        ),
        "selected_query_ids": identity.get("selected_query_ids"),
        "query_logical_receipts": ordered_query_rows,
        "query_logical_sha256": identity.get(
            "query_logical_sha256"
        ),
        "candidate_identity": ordered_candidate,
        "candidate_logical_sha256": identity.get(
            "candidate_logical_sha256"
        ),
        "cumulative_identity": ordered_cumulative,
    }
    semantic_hash = identity.get("semantic_sha256")
    require(
        semantic_hash == collector_json_sha256(semantic_body),
        "Rollover semantic identity hash drift",
    )
    require(
        identity.get("query_logical_sha256")
        == collector_json_sha256(ordered_query_rows)
        and identity.get("candidate_logical_sha256")
        == collector_json_sha256(ordered_candidate),
        "Rollover query/candidate logical hash drift",
    )
    partition = candidate_identity.get("candidate_partition")
    require(
        partition
        == {
            "all": structured["coverage"]["selected_candidate_count"],
            "top_level_enrichable": structured["coverage"][
                "top_level_structured"
            ],
            "nested_only_amendments": structured["coverage"][
                "nested_amendments_retained_unresolved"
            ],
        }
        and candidate_identity.get("selected_strict_candidate_count")
        == prefill.get("selected_record_count")
        and candidate_identity.get("record_tasks_sha256")
        == collector_json_sha256(current_plan.get("record_tasks"))
        == collector_json_sha256(reused_plan.get("record_tasks"))
        and candidate_identity.get("unsupported_candidates_sha256")
        == collector_json_sha256(
            current_plan.get("unsupported_candidates")
        )
        == collector_json_sha256(
            reused_plan.get("unsupported_candidates")
        ),
        "Rollover candidate partition/task identity drift",
    )
    if prefill_binding_chain is not None:
        binding = {
            **binding,
            "prefill_binding_chain": prefill_binding_chain,
        }
    return binding, file_item(checkpoint_path)


def build_output_row(
    *,
    proposal: dict[str, Any],
    category_by_id: dict[str, dict[str, Any]],
    measure: dict[str, Any] | None,
    unresolved_amendment: bool,
    prepared_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = str(proposal.get("review_target_query_id") or "")
    category = category_by_id.get(target)
    official_source = (
        measure["official_source"]
        if measure is not None
        else {
            "authority": None,
            "source_url": None,
            "raw_source_sha256": None,
        }
    )
    if prepared_evidence is None:
        additions: list[dict[str, Any]] = []
        section_states: dict[str, str] = {}
        if measure is not None:
            for section_name in SECTION_ORDER:
                section = measure["sections"][section_name]
                section_states[section_name] = str(section["status"])
                if category is None:
                    continue
                for match in evidence_for_section(
                    category=category,
                    section_name=section_name,
                    section=section,
                ):
                    additions.append(
                        {
                            **match,
                            "section": section_name,
                            "source_xpath": section.get("source_xpath"),
                            "source_record_sha256": section.get(
                                "source_record_sha256"
                            ),
                            "parsed_fragment_sha256": section.get(
                                "parsed_fragment_sha256"
                            ),
                            "proposal_only": True,
                            "dispositive": False,
                            "scoring_weight": 0,
                        }
                    )
        additions.sort(
            key=lambda row: (
                SECTION_ORDER.index(str(row["section"])),
                str(row["identifier_kind"]),
                str(row["identifier_text"]).casefold(),
                str(row["element_path"]),
            )
        )
        signal_count = sum(
            row["independent_review_signal"] for row in additions
        )
        prepared_evidence = {
            "section_states": section_states,
            "additions": additions,
            "signal_count": signal_count,
            "context_only_count": len(additions) - signal_count,
        }
    additions = prepared_evidence["additions"]
    section_states = prepared_evidence["section_states"]
    signal_count = prepared_evidence["signal_count"]
    context_only_count = prepared_evidence["context_only_count"]
    row = {
        "schema": ROW_SCHEMA,
        "canonical_key": proposal.get("canonical_key"),
        "raw_query_id": proposal.get("raw_query_id"),
        "review_target_query_id": proposal.get(
            "review_target_query_id"
        ),
        "category_label": proposal.get("category_label"),
        "source_proposal_identity": {
            "review_bucket": proposal.get("review_bucket"),
            "proposal_class": proposal.get("proposal_class"),
            "source_page": proposal.get("source_page"),
            "source_rank": proposal.get("source_rank"),
        },
        "structured_measure_available": measure is not None,
        "unresolved_amendment": unresolved_amendment,
        "structured_section_states": section_states,
        "official_source": official_source,
        "official_structured_evidence_additions": additions,
        "structured_match_count": len(additions),
        "independent_review_signal_count": signal_count,
        "context_only_non_dispositive_match_count": context_only_count,
        "generic_and_short_act_matches_are_non_dispositive": True,
        "no_cross_leaf_matching_performed": True,
        "evidence_addition_only": True,
        "proposal_only": True,
        "proposed_disposition": None,
        "human_review_required": True,
        "candidate_retained": True,
        "absence_is_neutral": True,
        "usable_as_membership_review_ledger": False,
        "decision_import_allowed": False,
        "scoring_input_allowed": False,
        "exclusion_performed": False,
        "membership_disposition_authority": False,
        "ratification_authority": False,
        "scoring_authority": False,
        "scoring_weight": 0,
    }
    return row


def manifest_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": manifest["schema"],
        "build_key": manifest["build_key"],
        "input_counts": manifest["input_counts"],
        "coverage": manifest["coverage"],
        "match_counts": manifest["match_counts"],
        "outputs": manifest["outputs"],
        "section_match_policy": manifest["section_match_policy"],
        "authority_contract": manifest["authority_contract"],
        "currentness": manifest["currentness"],
    }


def validate_existing(
    out_dir: Path,
    *,
    expected_build_key: str,
) -> dict[str, Any]:
    manifest_path = out_dir / "manifest.json"
    manifest = read_object(manifest_path)
    require(
        manifest.get("schema") == SCHEMA
        and manifest.get("status") == "PASS_STAGED_EVIDENCE_ONLY"
        and manifest.get("augmentation_id") == out_dir.name
        and manifest.get("build_key") == expected_build_key
        and manifest.get("review_prefill_only") is True
        and manifest.get("human_ratification_required") is True
        and manifest.get("absence_is_neutral") is True,
        f"Existing augmentation identity drift: {out_dir}",
    )
    require(
        manifest.get("network_executed") is False,
        "Existing augmentation network contract drift",
    )
    currentness = manifest.get("currentness")
    require(
        isinstance(currentness, dict)
        and currentness.get(
            "collector_rollover_equivalence_required"
        )
        is True,
        "Existing augmentation currentness contract drift",
    )
    confirmed_current = currentness.get("confirmed") is True
    if confirmed_current:
        require(
            currentness.get("basis")
            == "validated_zero_network_semantic_rollover_binding"
            and currentness.get("mode")
            in {"EXACT_PLAN", "SEMANTIC_EQUIVALENCE"}
            and SHA256_RE.fullmatch(
                str(currentness.get("binding_sha256") or "")
            )
            is not None,
            "Existing confirmed currentness receipt drift",
        )
    else:
        require(
            currentness
            == {
            "confirmed": False,
            "basis": "explicit_immutable_input_receipts_only",
            "collector_rollover_equivalence_required": True,
            },
            "Existing unconfirmed currentness receipt drift",
        )
    validate_authority_flags(manifest, label="existing augmentation")
    outputs = manifest.get("outputs")
    expected_outputs = {
            "membership_evidence_additions",
            "unresolved_amendments",
    }
    if confirmed_current:
        expected_outputs.add("semantic_rollover_binding")
    require(
        isinstance(outputs, dict)
        and set(outputs) == expected_outputs,
        "Existing augmentation output inventory drift",
    )
    evidence_path = validate_file_item(
        outputs["membership_evidence_additions"],
        label="existing membership evidence additions",
        base_dir=out_dir,
    )
    unresolved_path = validate_file_item(
        outputs["unresolved_amendments"],
        label="existing unresolved amendments",
        base_dir=out_dir,
    )
    if confirmed_current:
        rollover_path = validate_file_item(
            outputs["semantic_rollover_binding"],
            label="existing semantic rollover binding",
            base_dir=out_dir,
        )
        require(
            canonical_json_sha256(read_object(rollover_path))
            == currentness["binding_sha256"],
            "Existing semantic rollover binding hash drift",
        )
    evidence_count = 0
    for evidence_count, row in enumerate(iter_ndjson(evidence_path), 1):
        validate_output_row(row, evidence_count)
    unresolved_count = sum(1 for _ in iter_ndjson(unresolved_path))
    require(
        evidence_count
        == outputs["membership_evidence_additions"].get("record_count")
        == manifest.get("coverage", {}).get(
            "membership_evidence_rows"
        )
        and unresolved_count
        == outputs["unresolved_amendments"].get("record_count")
        == manifest.get("coverage", {}).get(
            "unresolved_amendments_preserved"
        ),
        "Existing augmentation output count drift",
    )
    require(
        canonical_json_sha256(manifest_identity(manifest))
        == manifest["augmentation_id"],
        "Existing augmentation content identity drift",
    )
    return manifest


def fsync_path(path: Path) -> None:
    """Flush a written file to disk (power loss must not leave it empty)."""
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_bytes_durable(path: Path, body: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.tmp.",
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def remove_temporary_tree(path: Path, *, expected_parent: Path) -> None:
    """Remove only this producer's temporary tree, including on OneDrive.

    OneDrive can mark a newly-created directory read-only while it is being
    hydrated.  On Windows that allows child removal but can make the final
    ``rmdir`` fail.  Clear that attribute at the exact failing path and retry;
    never broaden cleanup beyond the validated producer-specific directory.
    """

    path = path.resolve()
    expected_parent = expected_parent.resolve()
    require(
        path.parent == expected_parent
        and path.name.startswith(".structured-augmentation."),
        f"Refusing unsafe temporary cleanup target: {path}",
    )

    def clear_read_only(target: str | bytes | os.PathLike[str]) -> None:
        try:
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        except OSError:
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)

    def retry_read_only(
        function: Any,
        target: str,
        error: BaseException,
    ) -> None:
        if not isinstance(error, PermissionError):
            raise error
        clear_read_only(target)
        function(target)

    last_error: PermissionError | None = None
    for attempt in range(5):
        try:
            shutil.rmtree(path, onexc=retry_read_only)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            last_error = exc
            clear_read_only(path)
            time.sleep(0.1 * (attempt + 1))
    if path.exists() and last_error is not None:
        raise last_error


def build_augmentation(
    *,
    prefill_manifest_path: Path,
    structured_manifest_path: Path,
    registry_path: Path,
    out_root: Path,
    pointer_path: Path | None = None,
    rollover_checkpoint_path: Path | None = None,
    write_pointer: bool = True,
) -> tuple[Path, dict[str, Any], bool]:
    prefill_manifest_path = prefill_manifest_path.resolve()
    structured_manifest_path = structured_manifest_path.resolve()
    registry_path = registry_path.resolve()
    out_root = out_root.resolve()

    prefill, prefill_paths = validate_prefill(prefill_manifest_path)
    structured, structured_paths = validate_structured_sidecar(
        structured_manifest_path
    )
    registry = read_object(registry_path)
    categories = category_map(registry)
    rollover_binding = None
    rollover_checkpoint_item = None
    prior_augmentation_manifest_path = None
    if pointer_path is not None and pointer_path.is_file():
        prior_pointer = read_object(pointer_path)
        require(
            prior_pointer.get("schema") == POINTER_SCHEMA,
            "Prior augmentation pointer schema drift",
        )
        prior_augmentation_manifest_path = validate_file_item(
            prior_pointer.get("manifest"),
            label="prior augmentation pointer manifest",
        )
        require(
            prior_augmentation_manifest_path.parent
            == Path(
                str(prior_pointer.get("augmentation_dir") or "")
            ).resolve()
            and prior_augmentation_manifest_path.name == "manifest.json",
            "Prior augmentation pointer path drift",
        )
    if rollover_checkpoint_path is not None:
        rollover_binding, rollover_checkpoint_item = (
            validate_rollover_binding(
                checkpoint_path=rollover_checkpoint_path,
                prefill=prefill,
                prefill_manifest_path=prefill_manifest_path,
                structured=structured,
                structured_manifest_path=structured_manifest_path,
                registry_path=registry_path,
                prior_augmentation_manifest_path=(
                    prior_augmentation_manifest_path
                ),
            )
        )

    producer_path = Path(__file__).resolve()
    specificity_path = Path(REVIEW.__file__).resolve()
    build_bindings = {
        "schema": SCHEMA,
        "prefill_manifest_sha256": sha256_file(
            prefill_manifest_path
        ),
        "structured_manifest_sha256": sha256_file(
            structured_manifest_path
        ),
        "registry_sha256": sha256_file(registry_path),
        "producer_sha256": sha256_file(producer_path),
        "specificity_producer_sha256": sha256_file(specificity_path),
    }
    if rollover_binding is not None:
        build_bindings["semantic_rollover_binding_sha256"] = (
            canonical_json_sha256(rollover_binding)
        )
        build_bindings["semantic_rollover_checkpoint_sha256"] = (
            rollover_checkpoint_item["sha256"]
        )
    build_key = canonical_json_sha256(build_bindings)

    measures = load_measures(
        structured_paths["measures"],
        int(structured["outputs"]["measures"]["record_count"]),
    )
    structured_unresolved_keys = {
        str(row.get("canonical_key") or "")
        for row in iter_ndjson(
            structured_paths["unresolved_amendments"]
        )
    }
    require(
        len(structured_unresolved_keys)
        == structured["outputs"]["unresolved_amendments"].get(
            "record_count"
        )
        and "" not in structured_unresolved_keys
        and not (set(measures) & structured_unresolved_keys),
        "Structured candidate partition identity drift",
    )

    membership_identities: set[tuple[str, str]] = set()
    for row in iter_ndjson(prefill_paths["selected_memberships"]):
        identity = membership_identity_from_selected(row)
        require(
            all(identity) and identity not in membership_identities,
            f"Duplicate/invalid selected membership: {identity}",
        )
        membership_identities.add(identity)
    require(
        len(membership_identities)
        == prefill["selected_membership_count"],
        "Selected membership count drift",
    )

    prefill_unresolved_keys = {
        str(row.get("canonical_key") or "")
        for row in iter_ndjson(prefill_paths["unsupported_amendments"])
    }
    require(
        prefill_unresolved_keys == structured_unresolved_keys,
        "Prefill/structured unresolved-amendment identity drift",
    )

    out_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=".structured-augmentation.",
            dir=str(out_root.parent),
        )
    )
    try:
        evidence_path = temporary / "membership_evidence_additions.ndjson"
        seen_proposals: set[tuple[str, str]] = set()
        section_match_counts: Counter[str] = Counter()
        specificity_counts: Counter[str] = Counter()
        identifier_kind_counts: Counter[str] = Counter()
        matched_membership_count = 0
        structured_membership_count = 0
        unresolved_membership_count = 0
        evidence_count = 0
        with evidence_path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            evidence_cache: dict[
                tuple[str, str], dict[str, Any]
            ] = {}
            for proposal_index, proposal in enumerate(
                iter_ndjson(prefill_paths["membership_review_proposals"]),
                1,
            ):
                validate_source_proposal(proposal, proposal_index)
                identity = membership_identity_from_proposal(proposal)
                require(
                    identity in membership_identities
                    and identity not in seen_proposals,
                    f"Proposal/membership identity drift: {identity}",
                )
                seen_proposals.add(identity)
                key = identity[0]
                measure = measures.get(key)
                unresolved = key in structured_unresolved_keys
                require(
                    (measure is not None) ^ unresolved,
                    f"Proposal lacks exact structured partition: {key}",
                )
                cache_key = (
                    key,
                    str(
                        proposal.get("review_target_query_id") or ""
                    ),
                )
                prepared = evidence_cache.get(cache_key)
                output_row = build_output_row(
                    proposal=proposal,
                    category_by_id=categories,
                    measure=measure,
                    unresolved_amendment=unresolved,
                    prepared_evidence=prepared,
                )
                if prepared is None:
                    evidence_cache[cache_key] = {
                        "section_states": output_row[
                            "structured_section_states"
                        ],
                        "additions": output_row[
                            "official_structured_evidence_additions"
                        ],
                        "signal_count": output_row[
                            "independent_review_signal_count"
                        ],
                        "context_only_count": output_row[
                            "context_only_non_dispositive_match_count"
                        ],
                    }
                validate_output_row(output_row, proposal_index)
                write_row(handle, output_row)
                evidence_count += 1
                structured_membership_count += int(measure is not None)
                unresolved_membership_count += int(unresolved)
                matches = output_row[
                    "official_structured_evidence_additions"
                ]
                matched_membership_count += int(bool(matches))
                for match in matches:
                    section_match_counts[str(match["section"])] += 1
                    specificity_counts[
                        str(match["specificity_class"])
                    ] += 1
                    identifier_kind_counts[
                        str(match["identifier_kind"])
                    ] += 1
        require(
            seen_proposals == membership_identities
            and evidence_count == prefill["selected_membership_count"],
            "Not every selected membership received one evidence overlay row",
        )

        unresolved_path = temporary / "unresolved_amendments.ndjson"
        shutil.copyfile(
            prefill_paths["unsupported_amendments"],
            unresolved_path,
        )
        require(
            sha256_file(unresolved_path)
            == sha256_file(prefill_paths["unsupported_amendments"]),
            "Unresolved-amendment byte preservation failed",
        )
        unresolved_count = sum(1 for _ in iter_ndjson(unresolved_path))
        require(
            unresolved_count == prefill["unsupported_amendment_count"],
            "Unresolved-amendment count preservation failed",
        )

        outputs = {
            "membership_evidence_additions": file_item(
                evidence_path,
                record_count=evidence_count,
                relative=True,
            ),
            "unresolved_amendments": file_item(
                unresolved_path,
                record_count=unresolved_count,
                relative=True,
            ),
        }
        if rollover_binding is not None:
            rollover_path = (
                temporary / "semantic_rollover_binding.json"
            )
            write_bytes_durable(
                rollover_path,
                pretty_json_bytes(rollover_binding),
            )
            outputs["semantic_rollover_binding"] = file_item(
                rollover_path,
                record_count=1,
                relative=True,
            )
        match_counts = {
            "matched_memberships": matched_membership_count,
            "unmatched_memberships_neutral": (
                evidence_count - matched_membership_count
            ),
            "by_section": {
                section: section_match_counts[section]
                for section in SECTION_ORDER
            },
            "by_specificity_class": dict(
                sorted(specificity_counts.items())
            ),
            "by_identifier_kind": dict(
                sorted(identifier_kind_counts.items())
            ),
        }
        coverage = {
            "source_memberships": prefill["selected_membership_count"],
            "membership_evidence_rows": evidence_count,
            "every_membership_preserved_exactly_once": True,
            "structured_top_level_memberships": (
                structured_membership_count
            ),
            "unresolved_amendment_memberships": (
                unresolved_membership_count
            ),
            "source_unresolved_amendments": (
                prefill["unsupported_amendment_count"]
            ),
            "unresolved_amendments_preserved": unresolved_count,
            "unresolved_amendment_bytes_preserved": True,
            "all_structured_sections_considered": (
                list(SECTION_MATCH_POLICIES) == list(SECTION_ORDER)
            ),
            "structured_candidates_preserved_by_receipt": structured[
                "coverage"
            ]["selected_candidate_count"],
            "exclusion_provenance_receipts_preserved": len(
                structured["inputs"]["exclusion_provenance"]
            ),
        }
        authority_contract = {
            "evidence_addition_only": True,
            "generic_and_short_act_matches_non_dispositive": True,
            "no_cross_leaf_matching": True,
            "no_decisions": True,
            "no_inclusions": True,
            "no_exclusions": True,
            "no_scoring": True,
            "no_sync_or_promotion": True,
            "no_production_writes": True,
            "no_network": True,
        }
        currentness = (
            {
                "confirmed": True,
                "basis": (
                    "validated_zero_network_semantic_rollover_binding"
                ),
                "collector_rollover_equivalence_required": True,
                "binding_sha256": canonical_json_sha256(
                    rollover_binding
                ),
                "mode": rollover_binding["mode"],
            }
            if rollover_binding is not None
            else {
                "confirmed": False,
                "basis": "explicit_immutable_input_receipts_only",
                "collector_rollover_equivalence_required": True,
            }
        )
        manifest: dict[str, Any] = {
            "schema": SCHEMA,
            "status": "PASS_STAGED_EVIDENCE_ONLY",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "build_key": build_key,
            "build_bindings": build_bindings,
            "input_counts": {
                "selected_records": prefill["selected_record_count"],
                "selected_memberships": prefill[
                    "selected_membership_count"
                ],
                "source_proposals": prefill["proposal_count"],
                "structured_measures": structured["coverage"][
                    "top_level_structured"
                ],
                "structured_unresolved_amendments": structured[
                    "coverage"
                ]["nested_amendments_retained_unresolved"],
            },
            "inputs": {
                "prefill_manifest": file_item(prefill_manifest_path),
                "structured_relevance_manifest": file_item(
                    structured_manifest_path
                ),
                "registry": file_item(registry_path),
                "producer": file_item(producer_path),
                "specificity_producer": file_item(specificity_path),
                "prefill_outputs": prefill["outputs"],
                "structured_outputs": structured["outputs"],
                "candidate_and_exclusion_provenance": {
                    "advanced_search_candidates": structured["inputs"][
                        "advanced_search_candidates"
                    ],
                    "exclusion_ledger": structured["inputs"][
                        "exclusion_ledger"
                    ],
                    "exclusion_provenance": structured["inputs"][
                        "exclusion_provenance"
                    ],
                },
                "semantic_rollover_checkpoint": (
                    rollover_checkpoint_item
                ),
            },
            "coverage": coverage,
            "match_counts": match_counts,
            "section_match_policy": SECTION_MATCH_POLICIES,
            "outputs": outputs,
            "authority_contract": authority_contract,
            "currentness": currentness,
            "candidate_creation_performed": False,
            "candidate_membership_review_overwritten": False,
            "candidate_exclusion_performed": False,
            "semantic_inclusion_performed": False,
            "semantic_exclusion_performed": False,
            "ratified_decision_performed": False,
            "automatic_scoring_performed": False,
            "sync_or_promotion_performed": False,
            "production_write_performed": False,
            "network_executed": False,
            "membership_disposition_authority": False,
            "ratification_authority": False,
            "scoring_authority": False,
            "downstream_consumption_allowed": False,
            "decision_import_allowed": False,
            "scoring_input_allowed": False,
            "acceptance_passed": False,
            "review_prefill_only": True,
            "human_ratification_required": True,
            "absence_is_neutral": True,
            "proposal_only": True,
            "scoring_weight": 0,
        }
        augmentation_id = canonical_json_sha256(manifest_identity(manifest))
        manifest["augmentation_id"] = augmentation_id
        out_dir = out_root / augmentation_id
        if out_dir.exists():
            existing = validate_existing(
                out_dir,
                expected_build_key=build_key,
            )
            created = False
            manifest = existing
        else:
            write_bytes_durable(
                temporary / "manifest.json",
                pretty_json_bytes(manifest),
            )
            for child in temporary.iterdir():
                if child.is_file():
                    fsync_path(child)
            out_root.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, out_dir)
            created = True
    finally:
        if temporary.exists():
            remove_temporary_tree(
                temporary,
                expected_parent=out_root.parent,
            )

    if pointer_path is not None and write_pointer:
        pointer_path = pointer_path.resolve()
        # An unconfirmed build must never replace a confirmed pointer: the
        # confirmed snapshot carries the rollover binding later builds need.
        if (
            not manifest["currentness"]["confirmed"]
            and pointer_path.is_file()
            and read_object(pointer_path).get("currentness_confirmed")
            is True
        ):
            print(
                "Skipping pointer update: unconfirmed augmentation would "
                f"replace confirmed pointer {pointer_path}",
                file=sys.stderr,
            )
            return out_dir, manifest, created
        pointer = {
            "schema": POINTER_SCHEMA,
            "augmentation_id": manifest["augmentation_id"],
            "augmentation_dir": str(out_dir.resolve()),
            "currentness_confirmed": manifest["currentness"][
                "confirmed"
            ],
            "currentness_basis": manifest["currentness"]["basis"],
            "eligible_as_current_prefill_augmentation": manifest[
                "currentness"
            ]["confirmed"],
            "manifest": file_item(out_dir / "manifest.json"),
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
        atomic_write(pointer_path, pretty_json_bytes(pointer))
    return out_dir, manifest, created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefill-manifest", type=Path)
    parser.add_argument(
        "--prefill-pointer",
        type=Path,
        default=DEFAULT_PREFILL_POINTER,
    )
    parser.add_argument("--structured-manifest", type=Path)
    parser.add_argument(
        "--structured-pointer",
        type=Path,
        default=DEFAULT_STRUCTURED_POINTER,
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument(
        "--rollover-checkpoint",
        type=Path,
        default=DEFAULT_ROLLOVER_CHECKPOINT,
    )
    parser.add_argument(
        "--no-rollover-currentness",
        action="store_true",
        help="Build against explicit inputs but leave currentness false.",
    )
    parser.add_argument(
        "--no-current",
        action="store_true",
        help="Do not update the staged-only current pointer.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prefill_manifest = (
        args.prefill_manifest.resolve()
        if args.prefill_manifest
        else manifest_from_pointer(
            args.prefill_pointer,
            schema="legislation-partial-review-prefill-pointer-v1",
            label="partial-review prefill",
        )
    )
    structured_manifest = (
        args.structured_manifest.resolve()
        if args.structured_manifest
        else manifest_from_pointer(
            args.structured_pointer,
            schema="govinfo-billstatus-structured-relevance-pointer-v1",
            label="structured relevance",
        )
    )
    out_dir, manifest, created = build_augmentation(
        prefill_manifest_path=prefill_manifest,
        structured_manifest_path=structured_manifest,
        registry_path=args.registry,
        out_root=args.out_root,
        pointer_path=args.current,
        write_pointer=not args.no_current,
        rollover_checkpoint_path=(
            None
            if args.no_rollover_currentness
            else args.rollover_checkpoint
        ),
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "created": created,
                "augmentation_id": manifest["augmentation_id"],
                "output_dir": str(out_dir),
                "coverage": manifest["coverage"],
                "match_counts": manifest["match_counts"],
                "sync_or_promotion_performed": False,
                "production_write_performed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

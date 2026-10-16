#!/usr/bin/env python3
"""Create an evidence-backed, non-destructive membership review ledger."""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from lane_file_io import read_ndjson_text
    from legislation_measure_key import CANONICAL_KEY_RE
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import read_ndjson_text
    from fec.legislation_measure_key import CANONICAL_KEY_RE


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = (
    ROOT / "audits" / "legfix5" / "congress_advanced_pages_20260725"
)
DEFAULT_REGISTRY = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
)
DEFAULT_IDENTIFIER_AUDIT = (
    ROOT
    / "audits"
    / "legfix5"
    / "identifier_probe_pages_20260725"
    / "reconciliation"
    / "identifier_record_memberships.ndjson"
)
DEFAULT_DECISIONS = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_membership_decisions.ndjson"
)
DOMAIN_ANCHOR_RE = re.compile(
    r"\b(?:credit union|federal credit union|state[- ]chartered credit union|"
    r"national credit union administration|NCUA|share insurance fund|"
    r"interchange fee|credit card competition act|FedNow|central bank digital "
    r"currency|CBDC|member business lending|field of membership|community "
    r"development financial institution|CDFI)\b",
    re.IGNORECASE,
)
GENERIC_EVIDENCE_TOKENS = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "of",
    "and",
    "or",
    "for",
    "to",
    "in",
    "on",
    "at",
    "by",
    "with",
    "from",
    "as",
    "be",
    "been",
    "being",
    "is",
    "are",
    "was",
    "were",
    "may",
    "shall",
    "must",
    "can",
    "could",
    "would",
    "should",
    "act",
    "bill",
    "law",
    "resolution",
    "legislation",
    "cite",
    "cited",
    "known",
    "short",
    "title",
    "section",
    "chapter",
    "code",
    "amend",
    "amended",
    "amending",
    "amendment",
    "provide",
    "provides",
    "provided",
    "providing",
    "make",
    "makes",
    "making",
    "certain",
    "purpose",
    "purposes",
    "other",
    "related",
    "relating",
    "respect",
    "regard",
}
TEXT_MATCH_POLICY_VERSION = "ordered-token-bounded-act-action-required-v2"
INCLUSION_EVIDENCE_BASES = {
    "explicit_named_outline_bill",
    "official_title",
    "official_summary",
    "operative_provisions",
    "official_title_and_provisions",
}
EXCLUSION_EVIDENCE_BASES = {
    "congress_qualified_identity_mismatch",
    "operative_provisions",
    "official_title_and_provisions",
}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
# ``CANONICAL_KEY_RE`` is imported at the top of the module.  The local pattern
# accepted any Congress (``^\d+:``); it now shares the lane's single window so
# a stray Congress fails here instead of downstream.
CONGRESS_GOV_IDENTITY_PATH_RE = re.compile(
    r"^/(?:bill|amendment)/(?P<congress>\d+)"
    r"(?:st|nd|rd|th)-congress/"
    r"(?P<url_type>house-bill|senate-bill|house-resolution|"
    r"senate-resolution|house-joint-resolution|senate-joint-resolution|"
    r"house-concurrent-resolution|senate-concurrent-resolution|"
    r"house-amendment|senate-amendment)/"
    r"(?P<number>[1-9]\d*)(?:/|$)",
    re.IGNORECASE,
)
CONGRESS_GOV_URL_TYPE_TO_CANONICAL = {
    "house-bill": "hr",
    "senate-bill": "s",
    "house-resolution": "hres",
    "senate-resolution": "sres",
    "house-joint-resolution": "hjres",
    "senate-joint-resolution": "sjres",
    "house-concurrent-resolution": "hconres",
    "senate-concurrent-resolution": "sconres",
    "house-amendment": "hamdt",
    "senate-amendment": "samdt",
}
ARTICLE_ABSENCE_EVIDENCE_RE = re.compile(
    r"(?:"
    r"\b(?:article|press|acu|america'?s credit unions?|cuna|nafcu)\b"
    r".{0,48}\b(?:absen(?:ce|t)|missing|no[_ -]?match|not found|"
    r"no coverage|no article)\b"
    r"|"
    r"\b(?:absen(?:ce|t)|missing|no[_ -]?match|not found|"
    r"no coverage|no article|no)\b"
    r".{0,48}\b(?:article|press|acu|america'?s credit unions?|cuna|nafcu)\b"
    r")",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--relationships", type=Path)
    parser.add_argument(
        "--identifier-audit", type=Path, default=DEFAULT_IDENTIFIER_AUDIT
    )
    parser.add_argument("--text-relevance", type=Path)
    parser.add_argument("--article-cross-reference", type=Path)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in read_ndjson_text(path).splitlines()
        if line.strip()
    ]


def replace_with_retry(
    temporary: Path, destination: Path, attempts: int = 8
) -> None:
    for attempt in range(attempts):
        try:
            os.replace(temporary, destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(min(0.05 * (2**attempt), 0.75))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(value, indent=2, ensure_ascii=False) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    return (
        str(resolved.relative_to(ROOT)).replace("\\", "/")
        if resolved.is_relative_to(ROOT)
        else str(resolved)
    )


def output_file_receipt(path: Path, record_count: int) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": display_path(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
        "record_count": record_count,
    }


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def canonical_measure_identity(value: Any, field: str) -> str:
    normalized = normalize(value).casefold()
    match = CANONICAL_KEY_RE.fullmatch(normalized)
    if not match:
        raise ValueError(
            f"{field} must be a type-aware Congress:type:number identity: "
            f"{value!r}"
        )
    return (
        f"{int(match.group('congress'))}:"
        f"{match.group('measure_type')}:{int(match.group('number'))}"
    )


def congress_gov_identity_from_url(value: Any) -> str | None:
    parsed = urlparse(normalize(value))
    if parsed.scheme.casefold() != "https" or parsed.hostname not in {
        "congress.gov",
        "www.congress.gov",
    }:
        return None
    match = CONGRESS_GOV_IDENTITY_PATH_RE.match(parsed.path)
    if not match:
        return None
    measure_type = CONGRESS_GOV_URL_TYPE_TO_CANONICAL[
        match.group("url_type").casefold()
    ]
    return (
        f"{int(match.group('congress'))}:{measure_type}:"
        f"{int(match.group('number'))}"
    )


def validate_identity_mismatch_evidence(row: dict[str, Any]) -> None:
    expected = canonical_measure_identity(
        row.get("identity_mismatch_expected_canonical_key"),
        "identity_mismatch_expected_canonical_key",
    )
    observed = canonical_measure_identity(
        row.get("identity_mismatch_observed_canonical_key"),
        "identity_mismatch_observed_canonical_key",
    )
    candidate = canonical_measure_identity(
        row.get("canonical_key"), "canonical_key"
    )
    source_identity = congress_gov_identity_from_url(
        row.get("official_source_url")
    )
    if source_identity is None:
        raise ValueError(
            "Congress-qualified identity mismatch evidence requires a "
            "parseable official Congress.gov measure URL"
        )
    if expected != candidate:
        raise ValueError(
            "identity_mismatch_expected_canonical_key must exactly equal "
            f"the candidate canonical_key: {row}"
        )
    if observed != source_identity:
        raise ValueError(
            "identity_mismatch_observed_canonical_key does not match the "
            f"official Congress.gov URL identity: {row}"
        )
    if expected == observed:
        raise ValueError(
            "Congress-qualified identity mismatch evidence resolves to the "
            f"same identity on both sides: {row}"
        )


def official_evidence_is_specific(value: Any) -> bool:
    tokens = re.findall(r"[a-z0-9]+", normalize(value).casefold())
    distinctive = {
        token for token in tokens if token not in GENERIC_EVIDENCE_TOKENS
    }
    return len(distinctive) >= 3


def compact_cite_key(congress: int, compact_cite: str) -> str | None:
    match = re.fullmatch(
        r"(hconres|sconres|hjres|sjres|hamdt|samdt|hres|sres|hr|s)(\d+)",
        compact_cite.lower(),
    )
    if not match:
        return None
    return f"{congress}:{match.group(1)}:{int(match.group(2))}"


def evidence_text(
    record: dict[str, Any], metadata: dict[str, Any]
) -> str:
    return normalize(
        " ".join(
            str(value or "")
            for value in (
                record.get("title"),
                record.get("purpose"),
                record.get("raw_text"),
                metadata.get("official_title"),
                metadata.get("summary"),
                metadata.get("meta_description"),
                metadata.get("main_text_excerpt"),
                metadata.get("policy_area"),
            )
        )
    )


def matched_identifier_phrases(
    evidence: str, category: dict[str, Any]
) -> list[str]:
    normalized_evidence = evidence.lower()
    matches: list[str] = []
    for identifier in category["identifiers"]:
        phrase = normalize(identifier["text"]).lower()
        tokens = re.findall(r"[a-z0-9]+", phrase)
        distinctive = {
            token
            for token in tokens
            if token not in GENERIC_EVIDENCE_TOKENS
        }
        contains_act = "act" in tokens
        specificity_valid = (
            bool(tokens)
            and not (contains_act and len(distinctive) < 2)
            and not all(
                token in GENERIC_EVIDENCE_TOKENS for token in tokens
            )
        )
        if (
            specificity_valid
            and not contains_act
            and len(phrase) >= 5
            and phrase in normalized_evidence
        ):
            matches.append(identifier["text"])
    return matches


def matched_named_act_references(
    evidence: str, category: dict[str, Any] | None
) -> list[str]:
    """Expose full named-Act references without treating them as relevance."""
    if not category:
        return []
    normalized_evidence = " ".join(
        re.findall(r"[a-z0-9]+", normalize(evidence).lower())
    )
    references: list[str] = []
    for identifier in category.get("identifiers", []):
        raw = normalize(identifier.get("text"))
        query_match = re.fullmatch(r'"([^"]+)"\s*~\s*\d+', raw)
        phrase = query_match.group(1) if query_match else raw
        tokens = re.findall(r"[a-z0-9]+", phrase.lower())
        if "act" not in tokens:
            continue
        act_index = tokens.index("act")
        act_name_tokens = tokens[: act_index + 1]
        distinctive = {
            token
            for token in act_name_tokens
            if token not in GENERIC_EVIDENCE_TOKENS
        }
        if len(distinctive) < 2:
            continue
        act_name = " ".join(act_name_tokens)
        if re.search(
            rf"(?:^| ){re.escape(act_name)}(?: |$)",
            normalized_evidence,
        ):
            references.append(identifier["text"])
    return references


def rejected_non_specific_identifiers(
    category: dict[str, Any] | None,
) -> list[str]:
    if not category:
        return []
    rejected: list[str] = []
    for identifier in category["identifiers"]:
        phrase = normalize(identifier["text"]).lower()
        tokens = re.findall(r"[a-z0-9]+", phrase)
        distinctive = {
            token
            for token in tokens
            if token not in GENERIC_EVIDENCE_TOKENS
        }
        if (
            not tokens
            or ("act" in tokens and len(distinctive) < 2)
            or all(
                token in GENERIC_EVIDENCE_TOKENS for token in tokens
            )
        ):
            rejected.append(identifier["text"])
    return rejected


def matched_title_aliases(
    evidence: str, category: dict[str, Any] | None
) -> tuple[list[str], list[str]]:
    if not category:
        return [], []
    normalized_evidence = normalize(evidence).lower()
    specific: list[str] = []
    short_act: list[str] = []
    for title in category.get("title_aliases", []):
        normalized_title = normalize(title).lower()
        if normalized_title not in normalized_evidence:
            continue
        tokens = re.findall(r"[a-z0-9]+", normalized_title)
        distinctive = [
            token
            for token in tokens
            if token not in GENERIC_EVIDENCE_TOKENS
        ]
        if "act" in tokens and len(set(distinctive)) < 3:
            short_act.append(title)
        else:
            specific.append(title)
    return specific, short_act


def add_review_sort_fields(row: dict[str, Any]) -> None:
    """Attach non-dispositive review signals and a deterministic work order."""
    title_nexus = bool(
        row.get("official_title_identifier_matches")
        or row.get("matched_specific_title_aliases")
    )
    summary_nexus = bool(row.get("official_summary_identifier_matches"))
    operative_nexus = (
        row.get("official_text_evidence_state")
        == "localized_official_text_evidence"
    )
    act_reference_only = bool(
        row.get("official_text_act_reference_only_evidence")
        or row.get("matched_named_act_references_non_dispositive")
    ) and not operative_nexus
    strong_article_action = (
        row.get("trusted_article_strong_corroboration_count", 0) > 0
    )
    article_action = (
        row.get("trusted_article_action_match_count", 0) > 0
    )
    text_unavailable = row.get("official_text_evidence_state") in {
        None,
        "official_text_unavailable",
    }
    independent_signal_count = sum(
        (
            title_nexus,
            summary_nexus,
            operative_nexus,
            strong_article_action,
            bool(row.get("live_identifier_query_hits")),
            bool(row.get("candidate_category_edge_origins")),
        )
    )

    disposition = row.get("disposition")
    if disposition == "exclude_false_positive":
        priority = 90
        bucket = "culled_false_positive_officially_verified"
        likely_false_positive_reason = row.get("reason")
    elif disposition == "include":
        priority = 91
        bucket = "included_officially_or_outline_verified"
        likely_false_positive_reason = None
    elif (
        row.get("coverage_probe_unclassified")
        and independent_signal_count
    ):
        priority = 1
        bucket = "recall_only_possible_category_miss"
        likely_false_positive_reason = None
    elif row.get("ambiguous_short_act_alias_probe") or row.get(
        "matched_short_act_title_aliases"
    ):
        priority = 2
        bucket = "ambiguous_short_act_collision"
        likely_false_positive_reason = (
            "Short Act alias matched without enough independent specificity."
        )
    elif operative_nexus and (strong_article_action or title_nexus):
        priority = 3
        bucket = "multiple_independent_relevance_signals"
        likely_false_positive_reason = None
    elif operative_nexus:
        priority = 4
        bucket = "official_operative_text_signal"
        likely_false_positive_reason = None
    elif strong_article_action or article_action:
        priority = 5
        bucket = "trusted_article_action_requires_official_review"
        likely_false_positive_reason = None
    elif title_nexus or summary_nexus:
        priority = 6
        bucket = "official_title_or_summary_nexus"
        likely_false_positive_reason = None
    elif act_reference_only:
        priority = 7
        bucket = "named_act_reference_only"
        likely_false_positive_reason = (
            "The named Act appears only as a reference; no localized "
            "operative action has been established."
        )
    elif row.get("live_identifier_query_hits") or row.get(
        "candidate_category_edge_origins"
    ):
        priority = 8
        bucket = "probe_or_synthesized_category_edge"
        likely_false_positive_reason = None
    elif text_unavailable:
        priority = 9
        bucket = "official_text_unavailable_or_not_enriched"
        likely_false_positive_reason = None
    else:
        priority = 10
        bucket = "official_context_review_needed"
        likely_false_positive_reason = None

    row.update(
        {
            "review_priority": priority,
            "review_bucket": bucket,
            "official_title_nexus": title_nexus,
            "official_summary_nexus": summary_nexus,
            "operative_action_nexus": operative_nexus,
            "act_reference_only": act_reference_only,
            "strong_article_action": strong_article_action,
            "any_trusted_article_action": article_action,
            "text_unavailable": text_unavailable,
            "independent_review_signal_count": independent_signal_count,
            "likely_false_positive_reason": likely_false_positive_reason,
        }
    )


def validate_decision(row: dict[str, Any]) -> None:
    disposition = row.get("disposition")
    if disposition not in {"include", "exclude_false_positive", "unresolved"}:
        raise ValueError(
            f"Invalid disposition for {row.get('canonical_key')} / "
            f"{row.get('query_id')}: {disposition!r}"
        )
    if disposition in {"include", "exclude_false_positive"}:
        required = [
            "reason",
            "evidence",
            "evidence_basis",
            "decision_type",
            "reason_code",
            "reviewer_confidence",
            "adjudication_state",
            "evidence_sources_checked",
            "reviewer",
            "reviewed_at",
            "official_source_url",
            "official_text_version",
            "official_section",
            "official_evidence_excerpt",
            "official_evidence_sha256",
            "official_retrieved_at",
        ]
        missing = [field for field in required if not normalize(row.get(field))]
        if missing:
            raise ValueError(
                "Resolved membership decision lacks required fields "
                f"{missing}: {row}"
            )
        evidence_basis = normalize(row.get("evidence_basis"))
        allowed_bases = (
            INCLUSION_EVIDENCE_BASES
            if disposition == "include"
            else EXCLUSION_EVIDENCE_BASES
        )
        if evidence_basis not in allowed_bases:
            raise ValueError(
                f"{disposition} decisions require one of "
                f"{sorted(allowed_bases)}; received {evidence_basis!r}: "
                f"{row}"
            )
        if not re.fullmatch(
            r"[a-z0-9]+(?:_[a-z0-9]+)*",
            normalize(row.get("reason_code")),
        ):
            raise ValueError(
                f"Invalid controlled reason_code: {row}"
            )
        if row.get("reviewer_confidence") not in {
            "high",
            "medium",
            "low",
        }:
            raise ValueError(
                f"Invalid reviewer_confidence: {row}"
            )
        if not isinstance(row.get("evidence_sources_checked"), list) or not (
            row["evidence_sources_checked"]
        ):
            raise ValueError(
                "Resolved decisions require a nonempty "
                f"evidence_sources_checked list: {row}"
            )
        official_source_url = normalize(row.get("official_source_url"))
        if not re.match(
            r"https://(?:www\.)?congress\.gov/", official_source_url, re.I
        ):
            raise ValueError(
                "Resolved membership decision must cite a Congress.gov "
                f"official source URL: {row}"
            )
        excerpt = normalize(row.get("official_evidence_excerpt"))
        excerpt_sha256 = normalize(
            row.get("official_evidence_sha256")
        ).lower()
        if not SHA256_RE.fullmatch(excerpt_sha256):
            raise ValueError(
                "official_evidence_sha256 must be a lowercase SHA-256 digest: "
                f"{row}"
            )
        observed_sha256 = hashlib.sha256(
            excerpt.encode("utf-8")
        ).hexdigest()
        if excerpt_sha256 != observed_sha256:
            raise ValueError(
                "official_evidence_sha256 does not match the normalized "
                f"official_evidence_excerpt: {row}"
            )
        text_unavailable = row.get("text_unavailable")
        if not isinstance(text_unavailable, bool):
            raise ValueError(
                "Resolved decisions must explicitly set text_unavailable to "
                f"true or false: {row}"
            )
        if (
            disposition == "exclude_false_positive"
            and text_unavailable
            and evidence_basis != "congress_qualified_identity_mismatch"
        ):
            raise ValueError(
                "A false-positive exclusion with unavailable operative text "
                "is permitted only for an exact Congress-qualified identity "
                f"mismatch: {row}"
            )
        if evidence_basis == "congress_qualified_identity_mismatch":
            validate_identity_mismatch_evidence(row)
        elif not official_evidence_is_specific(excerpt):
            raise ValueError(
                "Resolved membership evidence must quote a specific official "
                "title, summary, or operative provision; generic or ambiguous "
                f"Act language is non-dispositive: {row}"
            )
        if disposition == "exclude_false_positive":
            evidence_sources = [
                normalize(value)
                for value in row["evidence_sources_checked"]
            ]
            absence_claims = " ".join(
                [
                    *evidence_sources,
                    normalize(row.get("evidence")),
                    normalize(row.get("reason")),
                ]
            )
            if ARTICLE_ABSENCE_EVIDENCE_RE.search(absence_claims):
                raise ValueError(
                    "Article or ACU press absence is neutral and cannot be "
                    f"used as false-positive exclusion evidence: {row}"
                )


def main() -> int:
    args = parse_args()
    run_dir = args.run.resolve()
    out_dir = (
        args.out.resolve()
        if args.out
        else run_dir / "candidate_membership_review"
    )
    relationships_dir = (
        args.relationships.resolve()
        if args.relationships
        else run_dir / "relationships"
    )
    text_relevance_dir = (
        args.text_relevance.resolve()
        if args.text_relevance
        else run_dir / "text_relevance"
    )
    article_cross_reference_dir = (
        args.article_cross_reference.resolve()
        if args.article_cross_reference
        else run_dir / "article_cross_reference"
    )
    manifest = read_json(run_dir / "manifest.json")
    if manifest["status"] != "complete":
        raise RuntimeError("Advanced Search discovery run is not complete")
    registry = read_json(args.registry.resolve())
    union = manifest["union"]
    for key in ("records_file", "memberships_file"):
        if not (run_dir / union[key]).is_file():
            raise RuntimeError(f"Discovery union {key} is missing")
    records = read_ndjson(run_dir / union["records_file"])
    raw_memberships = read_ndjson(run_dir / union["memberships_file"])
    if (
        not records
        or not raw_memberships
        or len(records) != union.get("unique_record_count")
        or len(raw_memberships) != union.get("membership_count")
    ):
        raise RuntimeError(
            "Discovery union record/membership counts are empty or "
            "disagree with the manifest"
        )
    record_by_key = {row["canonical_key"]: row for row in records}
    query_meta = {row["query_id"]: row for row in manifest["queries"]}
    category_by_id = {
        category["id"]: category for category in registry["categories"]
    }
    alias_probe_hits: dict[
        tuple[str, str], list[dict[str, Any]]
    ] = {}
    existing_category_memberships = {
        (row["canonical_key"], row["query_id"])
        for row in raw_memberships
        if row["query_id"] in category_by_id
    }
    memberships: list[dict[str, Any]] = []
    for membership in raw_memberships:
        parent_category_id = membership.get("parent_category_id")
        if (
            membership.get("membership_non_dispositive")
            and parent_category_id in category_by_id
        ):
            key = (membership["canonical_key"], parent_category_id)
            alias_probe_hits.setdefault(key, []).append(membership)
            if key not in existing_category_memberships:
                category = category_by_id[parent_category_id]
                memberships.append(
                    {
                        **membership,
                        "query_id": parent_category_id,
                        "label": category["label"],
                        "scoring_category": category["scoring_category"],
                        "record_kind": "title_alias_probe_candidate",
                        "source_title_alias_probe_id": membership["query_id"],
                    }
                )
                existing_category_memberships.add(key)
            continue
        memberships.append(membership)
    known_by_category: dict[str, set[str]] = {}
    for category in registry["categories"]:
        known_by_category[category["id"]] = {
            key
            for bill in category.get("known_bills", [])
            if (
                key := compact_cite_key(
                    int(bill["congress"]), bill["compact_cite"]
                )
            )
        }

    metadata_by_key: dict[str, dict[str, Any]] = {}
    relationship_manifest_path = relationships_dir / "manifest.json"
    if relationship_manifest_path.exists():
        relationship_manifest = read_json(relationship_manifest_path)
        if (
            relationship_manifest.get("status") == "complete"
            and relationship_manifest.get("measure_metadata_file")
        ):
            metadata_by_key = {
                row["canonical_key"]: row
                for row in read_ndjson(
                    relationships_dir
                    / relationship_manifest["measure_metadata_file"]
                )
            }

    identifier_hits: dict[tuple[str, str], list[str]] = {}
    if args.identifier_audit.resolve().exists():
        for row in read_ndjson(args.identifier_audit.resolve()):
            key = (row["parent_category_id"], row["canonical_key"])
            identifier_hits.setdefault(key, []).append(
                row["identifier_text"]
            )

    text_evidence_by_membership: dict[
        tuple[str, str], dict[str, Any]
    ] = {}
    text_suggestions_by_record: dict[str, list[dict[str, Any]]] = {}
    text_relevance_manifest_path = text_relevance_dir / "manifest.json"
    if text_relevance_manifest_path.exists():
        text_relevance_manifest = read_json(text_relevance_manifest_path)
        if text_relevance_manifest.get("status") == "complete":
            observed_policy = text_relevance_manifest.get(
                "text_match_policy_version"
            )
            if observed_policy != TEXT_MATCH_POLICY_VERSION:
                raise RuntimeError(
                    "Official-text evidence was produced by a stale matching "
                    f"policy ({observed_policy!r}); expected "
                    f"{TEXT_MATCH_POLICY_VERSION!r}. Regenerate it before "
                    "membership review."
                )
            for row in read_ndjson(
                text_relevance_dir
                / Path(text_relevance_manifest["ledger_file"]).name
            ):
                if row.get("evidence_role") == "category_membership":
                    text_evidence_by_membership[
                        (row["canonical_key"], row["query_id"])
                    ] = row
                elif (
                    row.get("evidence_role")
                    == "coverage_probe_classification"
                ):
                    text_suggestions_by_record.setdefault(
                        row["canonical_key"], []
                    ).append(row)

    article_evidence_by_membership: dict[
        tuple[str, str], dict[str, Any]
    ] = {}
    article_matches_by_record: dict[str, list[dict[str, Any]]] = {}
    article_manifest_path = article_cross_reference_dir / "manifest.json"
    article_manifest: dict[str, Any] | None = None
    if article_manifest_path.exists():
        article_manifest = read_json(article_manifest_path)
        if article_manifest.get("status") == "complete":
            for row in read_ndjson(
                article_cross_reference_dir
                / article_manifest["membership_evidence_file"]
            ):
                article_evidence_by_membership[
                    (row["canonical_key"], row["query_id"])
                ] = row
            for row in read_ndjson(
                article_cross_reference_dir
                / article_manifest["measure_matches_file"]
            ):
                article_matches_by_record.setdefault(
                    row["canonical_key"], []
                ).append(row)

    decisions = read_ndjson(args.decisions.resolve()) if args.decisions else []
    decision_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for decision in decisions:
        validate_decision(decision)
        key = (decision["canonical_key"], decision["query_id"])
        if key in decision_by_key:
            raise ValueError(f"Duplicate review decision: {key}")
        decision_by_key[key] = decision

    # A broad recall or historical probe can discover a measure even when the
    # corresponding category query misses it. Turn each independent signal into
    # a non-dispositive candidate-category edge so a reviewer can include,
    # exclude, or reroute it. The original recall/probe membership is retained.
    candidate_edge_by_key = {
        (row["canonical_key"], row["query_id"]): row
        for row in memberships
    }

    def add_candidate_category_edge(
        canonical_key: str,
        query_id: str,
        origin: str,
        source_detail: Any,
        *,
        strict: bool = False,
    ) -> None:
        if canonical_key not in record_by_key or query_id not in category_by_id:
            if strict:
                raise ValueError(
                    "Reviewer-added category edge references an unknown "
                    f"record/category: {(canonical_key, query_id)}"
                )
            return
        edge_key = (canonical_key, query_id)
        if edge_key in candidate_edge_by_key:
            row = candidate_edge_by_key[edge_key]
            row.setdefault(
                "candidate_category_edge_origins", []
            ).append(origin)
            row.setdefault(
                "candidate_category_edge_source_details", []
            ).append(source_detail)
            return
        category = category_by_id[query_id]
        new_edge = {
            "canonical_key": canonical_key,
            "query_id": query_id,
            "label": category["label"],
            "scoring_category": category["scoring_category"],
            "record_kind": "synthesized_candidate_category",
            "membership_non_dispositive": True,
            "candidate_category_edge_origins": [origin],
            "candidate_category_edge_source_details": [source_detail],
        }
        memberships.append(new_edge)
        candidate_edge_by_key[edge_key] = new_edge

    for (query_id, canonical_key), phrases in identifier_hits.items():
        add_candidate_category_edge(
            canonical_key,
            query_id,
            "isolated_identifier_probe",
            sorted(set(phrases)),
        )
    for canonical_key, suggestions in text_suggestions_by_record.items():
        for suggestion in suggestions:
            if (
                suggestion.get("evidence_state")
                == "localized_official_text_evidence"
            ):
                add_candidate_category_edge(
                    canonical_key,
                    suggestion.get("query_id"),
                    "official_text_coverage_probe_suggestion",
                    {
                        "source_url": suggestion.get("source_url"),
                        "localized_identifiers": suggestion.get(
                            "localized_identifiers", []
                        ),
                    },
                )
    for canonical_key, matches in article_matches_by_record.items():
        for match in matches:
            substantive_actions = set(
                match.get("action_types_near_reference", [])
            ) - {"support_or_opposition"}
            article_can_suggest = (
                "exact_bill_citation"
                in match.get("reference_basis", [])
                and match.get("congress_compatible") is True
                and bool(substantive_actions)
                and match.get("citations_in_action_clause") == 1
            )
            if not article_can_suggest:
                continue
            for query_id in match.get("article_category_ids", []):
                add_candidate_category_edge(
                    canonical_key,
                    query_id,
                    "trusted_article_action_category_suggestion",
                    {
                        "article_url": match.get("article_url"),
                        "action_types": sorted(substantive_actions),
                        "action_context_excerpt": match.get(
                            "action_context_excerpt"
                        ),
                    },
                )
    for canonical_key, query_id in decision_by_key:
        add_candidate_category_edge(
            canonical_key,
            query_id,
            "reviewer_added_category",
            {
                "decision_provenance": decision_by_key[
                    (canonical_key, query_id)
                ].get("decision_provenance")
            },
            strict=True,
        )

    ledger: list[dict[str, Any]] = []
    seen_decisions: set[tuple[str, str]] = set()
    for membership in memberships:
        query_id = membership["query_id"]
        meta = query_meta[query_id]
        record = record_by_key[membership["canonical_key"]]
        classified_category = bool(
            record.get("matched_scoring_categories")
            or record.get("matched_monitor_categories")
        )
        coverage_probe_unclassified = (
            meta["record_kind"] == "coverage_probe"
            and not classified_category
        )
        if meta["record_kind"] == "coverage_probe" and not (
            coverage_probe_unclassified
        ):
            continue
        metadata = metadata_by_key.get(record["canonical_key"], {})
        evidence = evidence_text(record, metadata)
        category = category_by_id.get(query_id)
        alias_hits = alias_probe_hits.get(
            (record["canonical_key"], query_id), []
        )
        alias_hit_titles = sorted(
            {
                normalize(hit.get("title_alias"))
                for hit in alias_hits
                if normalize(hit.get("title_alias"))
            }
        )
        ambiguous_short_act_alias_hit = any(
            hit.get("ambiguous_short_act_alias") for hit in alias_hits
        )
        phrase_matches = (
            matched_identifier_phrases(evidence, category) if category else []
        )
        official_title_identifier_matches = (
            matched_identifier_phrases(
                normalize(metadata.get("official_title")), category
            )
            if category
            else []
        )
        official_summary_identifier_matches = (
            matched_identifier_phrases(
                normalize(metadata.get("summary")), category
            )
            if category
            else []
        )
        named_act_references = matched_named_act_references(
            evidence, category
        )
        rejected_generic_identifiers = (
            rejected_non_specific_identifiers(category)
        )
        (
            specific_title_alias_matches,
            short_act_title_alias_matches,
        ) = matched_title_aliases(evidence, category)
        live_identifier_hits = sorted(
            set(identifier_hits.get((query_id, record["canonical_key"]), []))
        )
        named_outline = (
            record["canonical_key"] in known_by_category.get(query_id, set())
        )
        text_evidence = text_evidence_by_membership.get(
            (record["canonical_key"], query_id), {}
        )
        if not text_evidence:
            text_evidence = next(
                (
                    suggestion
                    for suggestion in text_suggestions_by_record.get(
                        record["canonical_key"], []
                    )
                    if suggestion.get("query_id") == query_id
                ),
                {},
            )
        localized_text_evidence = (
            text_evidence.get("evidence_state")
            == "localized_official_text_evidence"
        )
        act_reference_only_evidence = [
            item
            for item in text_evidence.get("identifier_evidence", [])
            if item.get("act_reference_only")
        ]
        article_evidence = article_evidence_by_membership.get(
            (record["canonical_key"], query_id), {}
        )
        article_matches = article_matches_by_record.get(
            record["canonical_key"], []
        )
        article_matches_unique: dict[str, dict[str, Any]] = {}
        article_strength_order = {"strong": 0, "moderate": 1, "review": 2}
        for match in article_matches:
            article_url = (
                match.get("canonical_article_url")
                or match.get("article_url")
            )
            prior = article_matches_unique.get(article_url)
            if prior is None or article_strength_order.get(
                match.get("evidence_strength"), 9
            ) < article_strength_order.get(
                prior.get("evidence_strength"), 9
            ):
                article_matches_unique[article_url] = match
        article_matches = sorted(
            article_matches_unique.values(),
            key=lambda match: (
                article_strength_order.get(
                    match.get("evidence_strength"), 9
                ),
                match.get("article_date") or "",
                match.get("canonical_article_url")
                or match.get("article_url")
                or "",
            ),
        )
        article_any = bool(article_matches)
        article_action_matches = [
            match
            for match in article_matches
            if match.get("action_types_near_reference")
        ]
        article_category_matches = [
            match
            for match in article_matches
            if query_id in match.get("category_overlap_query_ids", [])
        ]
        article_strong_matches = [
            match
            for match in article_category_matches
            if match.get("evidence_strength") == "strong"
        ]
        article_strong = bool(article_strong_matches)
        article_signal = article_evidence.get(
            "trusted_article_review_signal"
        )
        if not article_signal:
            if article_strong_matches:
                article_signal = (
                    "trusted_article_action_and_category_corroboration"
                )
            elif any(
                match.get("evidence_strength") == "moderate"
                for match in article_category_matches
            ):
                article_signal = (
                    "trusted_article_category_corroboration_without_action"
                )
            elif article_action_matches:
                article_signal = (
                    "trusted_article_action_or_identity_requires_review"
                )
            elif article_matches:
                article_signal = (
                    "trusted_article_reference_requires_identity_or_"
                    "congress_review"
                )
            else:
                article_signal = (
                    "no_trusted_article_match_not_dispositive"
                )
        domain_anchor = DOMAIN_ANCHOR_RE.search(evidence)
        if ambiguous_short_act_alias_hit:
            triage = (
                "ambiguous_short_act_alias_requires_official_bill_review"
            )
        elif coverage_probe_unclassified and article_any:
            triage = (
                "coverage_probe_trusted_article_suggestion_requires_review"
            )
        elif coverage_probe_unclassified:
            triage = "coverage_probe_unclassified_requires_review"
        elif named_outline:
            triage = "high_priority_named_outline_bill"
        elif localized_text_evidence:
            triage = "official_text_relevance_signal_requires_review"
        elif act_reference_only_evidence or named_act_references:
            triage = (
                "named_act_reference_without_localized_action_requires_review"
            )
        elif article_strong:
            triage = (
                "trusted_article_action_corroboration_requires_review"
            )
        elif article_any:
            triage = "trusted_article_reference_requires_review"
        elif live_identifier_hits and (phrase_matches or domain_anchor):
            triage = "positive_metadata_evidence"
        elif live_identifier_hits:
            triage = "live_identifier_hit_needs_context_review"
        elif short_act_title_alias_matches:
            triage = (
                "short_act_title_alone_is_not_relevance_evidence"
            )
        elif metadata:
            triage = "needs_full_text_relevance_review"
        else:
            triage = "needs_official_metadata_enrichment"

        decision_key = (record["canonical_key"], query_id)
        decision = decision_by_key.get(decision_key)
        if decision:
            seen_decisions.add(decision_key)
            disposition = decision["disposition"]
            decision_source = "explicit_review_decision"
            reason = decision.get("reason")
            decision_evidence = decision.get("evidence")
            decision_evidence_basis = decision.get("evidence_basis")
            reviewer = decision.get("reviewer")
            reviewed_at = decision.get("reviewed_at")
        elif named_outline:
            disposition = "include"
            decision_source = "automated_named_outline_inclusion"
            reason = (
                "Congress-qualified bill is explicitly named in the "
                "source outline."
            )
            decision_evidence = record["canonical_url"]
            decision_evidence_basis = "explicit_named_outline_bill"
            reviewer = "deterministic coverage rule"
            reviewed_at = manifest.get("completed_at")
        else:
            disposition = "unresolved"
            decision_source = (
                "automated_official_text_signal_requires_careful_review"
                if localized_text_evidence
                else (
                    "automated_article_corroboration_requires_careful_review"
                    if article_any
                    else "automated_triage_does_not_exclude"
                )
            )
            reason = None
            decision_evidence = None
            decision_evidence_basis = None
            reviewer = None
            reviewed_at = None
        ledger.append(
            {
                "canonical_key": record["canonical_key"],
                "display_number": record["display_number"],
                "title": record.get("title") or record.get("purpose"),
                "measure_initial_action": record.get("initial_action"),
                "measure_initial_action_date": record.get(
                    "initial_action_date"
                ),
                "measure_initial_action_date_iso": record.get(
                    "initial_action_date_iso"
                ),
                "measure_latest_action": record.get("latest_action"),
                "measure_latest_action_date": record.get(
                    "latest_action_date"
                ),
                "measure_tracker_status": record.get("tracker_status"),
                "measure_cosponsor_count": record.get("cosponsor_count"),
                "measure_committees": record.get("committees"),
                "measure_amends": record.get("amends"),
                "query_id": query_id,
                "category_label": (
                    f"{meta['label']} (Unclassified)"
                    if coverage_probe_unclassified
                    else meta["label"]
                ),
                "scoring_category": meta["scoring_category"],
                "record_kind": membership.get(
                    "record_kind", meta["record_kind"]
                ),
                "membership_non_dispositive": membership.get(
                    "membership_non_dispositive", False
                ),
                "candidate_category_edge_origins": sorted(
                    set(
                        membership.get(
                            "candidate_category_edge_origins", []
                        )
                    )
                ),
                "candidate_category_edge_source_details": membership.get(
                    "candidate_category_edge_source_details", []
                ),
                "triage": triage,
                "named_outline_bill": named_outline,
                "coverage_probe_unclassified": coverage_probe_unclassified,
                "source_title_alias_probe_ids": sorted(
                    {
                        hit["query_id"]
                        for hit in alias_hits
                    }
                ),
                "source_title_aliases": alias_hit_titles,
                "ambiguous_short_act_alias_probe": (
                    ambiguous_short_act_alias_hit
                ),
                "title_alias_probe_is_non_dispositive": bool(alias_hits),
                "suggested_categories_from_official_text": [
                    {
                        "query_id": suggestion["query_id"],
                        "category_label": suggestion["category_label"],
                        "localized_identifiers": suggestion.get(
                            "localized_identifiers", []
                        ),
                        "source_url": suggestion.get("source_url"),
                    }
                    for suggestion in text_suggestions_by_record.get(
                        record["canonical_key"], []
                    )
                    if suggestion.get("evidence_state")
                    == "localized_official_text_evidence"
                ],
                "matched_identifier_phrases": phrase_matches,
                "official_title_identifier_matches": (
                    official_title_identifier_matches
                ),
                "official_summary_identifier_matches": (
                    official_summary_identifier_matches
                ),
                "matched_named_act_references_non_dispositive": (
                    named_act_references
                ),
                "rejected_non_specific_identifier_phrases": (
                    rejected_generic_identifiers
                ),
                "matched_specific_title_aliases": (
                    specific_title_alias_matches
                ),
                "matched_short_act_title_aliases": (
                    short_act_title_alias_matches
                ),
                "short_act_title_requires_independent_evidence": True,
                "generic_act_language_can_never_establish_relevance": True,
                "live_identifier_query_hits": live_identifier_hits,
                "official_text_evidence_state": text_evidence.get(
                    "evidence_state"
                ),
                "official_text_localized_identifiers": text_evidence.get(
                    "localized_identifiers", []
                ),
                "official_text_identifier_evidence": text_evidence.get(
                    "identifier_evidence", []
                ),
                "official_text_act_reference_only_evidence": (
                    act_reference_only_evidence
                ),
                "official_text_separated_identifiers": text_evidence.get(
                    "separated_identifiers", []
                ),
                "official_text_source_url": text_evidence.get("source_url"),
                "matched_domain_anchor": (
                    domain_anchor.group(0) if domain_anchor else None
                ),
                "trusted_article_review_signal": article_signal,
                "trusted_article_match_count": article_evidence.get(
                    "trusted_article_match_count", len(article_matches)
                ),
                "trusted_article_evidence_row_count": article_evidence.get(
                    "trusted_article_evidence_row_count", 0
                ),
                "trusted_article_action_match_count": article_evidence.get(
                    "trusted_article_action_match_count",
                    len(article_action_matches),
                ),
                "trusted_article_category_overlap_count": (
                    article_evidence.get(
                        "trusted_article_category_overlap_count", 0
                        if article_evidence
                        else len(article_category_matches)
                    )
                ),
                "trusted_article_strong_corroboration_count": (
                    article_evidence.get(
                        "trusted_article_strong_corroboration_count",
                        len(article_strong_matches),
                    )
                ),
                "trusted_article_suggested_category_ids": (
                    article_evidence.get(
                        "trusted_article_suggested_category_ids", []
                    )
                ),
                "trusted_article_evidence": [
                    {
                        "article_id": match.get("article_id"),
                        "article_date": match.get("article_date"),
                        "article_title": match.get("article_title"),
                        "article_url": match.get("article_url"),
                        "article_member": match.get("article_member"),
                        "article_category_ids": match.get(
                            "article_category_ids", []
                        ),
                        "reference_basis": match.get(
                            "reference_basis", []
                        ),
                        "action_types_near_reference": match.get(
                            "action_types_near_reference", []
                        ),
                        "action_context_excerpt": match.get(
                            "action_context_excerpt"
                        ),
                        "category_overlap_query_ids": match.get(
                            "category_overlap_query_ids", []
                        ),
                        "congress_compatible": match.get(
                            "congress_compatible"
                        ),
                        "congress_compatibility_basis": match.get(
                            "congress_compatibility_basis"
                        ),
                        "citations_in_action_clause": match.get(
                            "citations_in_action_clause"
                        ),
                        "body_available": match.get("body_available"),
                        "bill_number_unique_in_discovery_corpus": match.get(
                            "bill_number_unique_in_discovery_corpus"
                        ),
                        "evidence_strength": match.get(
                            "evidence_strength"
                        ),
                        "review_signal": match.get("review_signal"),
                    }
                    for match in article_matches
                ],
                "article_absence_is_never_exclusion_evidence": True,
                "article_evidence_is_corroborative_not_dispositive": True,
                "article_evidence_assigns_legislative_role": False,
                "official_summary_available": bool(metadata.get("summary")),
                "official_title": metadata.get("official_title"),
                "official_summary": metadata.get("summary"),
                "policy_area": metadata.get("policy_area"),
                "disposition": disposition,
                "decision_source": decision_source,
                "reason": reason,
                "evidence": decision_evidence,
                "evidence_basis": decision_evidence_basis,
                "official_decision_source_url": (
                    decision.get("official_source_url")
                    if decision
                    else None
                ),
                "official_decision_text_version": (
                    decision.get("official_text_version")
                    if decision
                    else None
                ),
                "official_decision_section": (
                    decision.get("official_section")
                    if decision
                    else None
                ),
                "official_decision_evidence_excerpt": (
                    decision.get("official_evidence_excerpt")
                    if decision
                    else None
                ),
                "official_decision_evidence_sha256": (
                    decision.get("official_evidence_sha256")
                    if decision
                    else None
                ),
                "official_decision_retrieved_at": (
                    decision.get("official_retrieved_at")
                    if decision
                    else None
                ),
                "official_decision_text_unavailable": (
                    decision.get("text_unavailable")
                    if decision
                    else None
                ),
                "identity_mismatch_expected_canonical_key": (
                    decision.get(
                        "identity_mismatch_expected_canonical_key"
                    )
                    if decision
                    else None
                ),
                "identity_mismatch_observed_canonical_key": (
                    decision.get(
                        "identity_mismatch_observed_canonical_key"
                    )
                    if decision
                    else None
                ),
                "decision_provenance": (
                    decision.get("decision_provenance")
                    if decision
                    else None
                ),
                "decision_type": (
                    decision.get("decision_type") if decision else None
                ),
                "reason_code": (
                    decision.get("reason_code") if decision else None
                ),
                "reviewer_confidence": (
                    decision.get("reviewer_confidence")
                    if decision
                    else None
                ),
                "adjudication_state": (
                    decision.get("adjudication_state")
                    if decision
                    else None
                ),
                "evidence_sources_checked": (
                    decision.get("evidence_sources_checked", [])
                    if decision
                    else []
                ),
                "reviewer": reviewer,
                "reviewed_at": reviewed_at,
                "congress_gov_url": record["canonical_url"],
                "candidate_retained_in_discovery_corpus": True,
            }
        )

    unused = sorted(set(decision_by_key) - seen_decisions)
    if unused:
        raise ValueError(f"Review decisions do not match memberships: {unused}")
    for row in ledger:
        add_review_sort_fields(row)
    ledger.sort(
        key=lambda row: (row["query_id"], row["canonical_key"])
    )
    sorted_review_queue = sorted(
        ledger,
        key=lambda row: (
            row["review_priority"],
            -row["independent_review_signal_count"],
            row["query_id"],
            row["canonical_key"],
        ),
    )
    included = [row for row in ledger if row["disposition"] == "include"]
    excluded = [
        row
        for row in ledger
        if row["disposition"] == "exclude_false_positive"
    ]
    unresolved = [
        row for row in ledger if row["disposition"] == "unresolved"
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "membership_review_ledger.ndjson"
    included_path = out_dir / "included_memberships.ndjson"
    exclusions_path = out_dir / "false_positive_exclusions.ndjson"
    unresolved_path = out_dir / "unresolved_memberships.ndjson"
    sorted_review_queue_path = out_dir / "sorted_review_queue.ndjson"
    write_ndjson(ledger_path, ledger)
    write_ndjson(included_path, included)
    write_ndjson(exclusions_path, excluded)
    write_ndjson(unresolved_path, unresolved)
    write_ndjson(sorted_review_queue_path, sorted_review_queue)
    output_receipts = {
        "ledger": output_file_receipt(ledger_path, len(ledger)),
        "included": output_file_receipt(included_path, len(included)),
        "exclusions": output_file_receipt(exclusions_path, len(excluded)),
        "unresolved": output_file_receipt(unresolved_path, len(unresolved)),
        "sorted_review_queue": output_file_receipt(
            sorted_review_queue_path, len(sorted_review_queue)
        ),
    }
    review_bucket_counts = dict(
        sorted(Counter(row["review_bucket"] for row in ledger).items())
    )
    candidate_edge_origin_counts = Counter(
        origin
        for row in ledger
        for origin in row.get("candidate_category_edge_origins", [])
    )
    report = {
        "schema": "acu-legislation-membership-review-v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "discovery_records_are_never_deleted": True,
        "automated_semantic_exclusion": False,
        "article_cross_reference_complete": bool(article_manifest)
        and article_manifest.get("status") == "complete",
        "article_based_automated_inclusion_count": 0,
        "article_based_automated_exclusion_count": 0,
        "article_absence_is_never_exclusion_evidence": True,
        "article_evidence_is_corroborative_not_dispositive": True,
        "generic_act_language_can_never_establish_relevance": True,
        "generic_or_ambiguous_official_evidence_never_resolves_membership": True,
        "named_act_reference_without_localized_action_is_non_dispositive": True,
        "official_text_signals_require_review_before_inclusion": True,
        "resolved_decisions_require_official_title_summary_or_provisions": True,
        "false_positive_exclusions_require_identity_mismatch_or_operative_review": True,
        "identity_mismatch_exclusions_require_exact_expected_observed_identities": True,
        "resolved_decisions_require_reproducible_official_evidence": True,
        "decision_file": (
            str(args.decisions.resolve().relative_to(ROOT)).replace("\\", "/")
            if args.decisions
            and args.decisions.resolve().is_relative_to(ROOT)
            else str(args.decisions.resolve())
            if args.decisions
            else None
        ),
        "decision_file_sha256": (
            sha256_file(args.decisions.resolve())
            if args.decisions and args.decisions.resolve().exists()
            else None
        ),
        "discovery_manifest_sha256": sha256_file(
            run_dir / "manifest.json"
        ),
        "registry_sha256": sha256_file(args.registry.resolve()),
        "official_text_manifest_sha256": (
            sha256_file(text_relevance_manifest_path)
            if text_relevance_manifest_path.exists()
            else None
        ),
        "trusted_article_manifest_sha256": (
            sha256_file(article_manifest_path)
            if article_manifest_path.exists()
            else None
        ),
        "short_act_title_requires_independent_evidence": True,
        "memberships_with_short_act_title_alias_count": sum(
            bool(row.get("matched_short_act_title_aliases"))
            for row in ledger
        ),
        "rejected_non_specific_identifier_count": sum(
            len(row.get("rejected_non_specific_identifier_phrases", []))
            for row in ledger
        ),
        "membership_count": len(ledger),
        "distinct_review_membership_key_count": len(
            {
                (row["canonical_key"], row["query_id"])
                for row in ledger
            }
        ),
        "records_with_review_memberships": len(
            {row["canonical_key"] for row in ledger}
        ),
        "synthesized_candidate_category_edge_count": sum(
            row.get("record_kind") == "synthesized_candidate_category"
            for row in ledger
        ),
        "candidate_edges_with_additional_provenance_count": sum(
            bool(row.get("candidate_category_edge_origins"))
            for row in ledger
        ),
        "candidate_category_edge_origin_counts": dict(
            sorted(candidate_edge_origin_counts.items())
        ),
        "review_bucket_counts": review_bucket_counts,
        "included_count": len(included),
        "false_positive_exclusion_count": len(excluded),
        "unresolved_count": len(unresolved),
        "automated_named_outline_inclusion_count": sum(
            row["decision_source"] == "automated_named_outline_inclusion"
            for row in ledger
        ),
        "automated_official_text_inclusion_count": sum(
            row["decision_source"]
            == "automated_official_text_evidence_inclusion"
            for row in ledger
        ),
        "no_localized_text_evidence_count": sum(
            row.get("official_text_evidence_state")
            == "no_localized_identifier_evidence"
            for row in ledger
        ),
        "unclassified_coverage_probe_membership_count": sum(
            row.get("coverage_probe_unclassified") for row in ledger
        ),
        "trusted_article_matched_membership_count": sum(
            row.get("trusted_article_match_count", 0) > 0
            for row in ledger
        ),
        "trusted_article_action_corroborated_membership_count": sum(
            row.get("trusted_article_action_match_count", 0) > 0
            for row in ledger
        ),
        "trusted_article_strong_corroborated_membership_count": sum(
            row.get("trusted_article_strong_corroboration_count", 0) > 0
            for row in ledger
        ),
        "promotion_gate_passed": not unresolved,
        "atomic_output_publish": True,
        "output_files_hash_bound": True,
        "outputs": output_receipts,
        "ledger_file": display_path(ledger_path),
        "included_file": display_path(included_path),
        "exclusions_file": display_path(exclusions_path),
        "unresolved_file": display_path(unresolved_path),
        "sorted_review_queue_file": display_path(sorted_review_queue_path),
    }
    write_json(out_dir / "manifest.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["promotion_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

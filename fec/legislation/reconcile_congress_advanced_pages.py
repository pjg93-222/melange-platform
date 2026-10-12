#!/usr/bin/env python3
"""Reconcile the lossless Congress.gov page corpus and export review artifacts."""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from lane_file_io import read_ndjson_text
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import read_ndjson_text


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = (
    ROOT / "audits" / "legfix5" / "congress_advanced_pages_20260725"
)
DEFAULT_ARCHIVE = ROOT / "congress_bills_archive_20260624_115822.xlsx"
DEFAULT_REGISTRY = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
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
DECAY_WEIGHT = {116: 0.16, 117: 0.33, 118: 0.66, 119: 1.00}
LEGACY_DECAY_WEIGHT = {116: 0.512, 117: 0.64, 118: 0.80, 119: 1.00}
INTERCHANGE_FAMILY = {
    "117:s:4674",
    "117:hr:8874",
    "118:s:1838",
    "118:hr:3881",
    "119:s:3623",
    "119:hr:7035",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--relationships", type=Path)
    parser.add_argument("--membership-review", type=Path)
    parser.add_argument("--text-relevance", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--allow-incomplete-relationships",
        action="store_true",
        help="Write non-workbook audit artifacts even if enrichment is incomplete.",
    )
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT)).replace("\\", "/")


def legislation_key(congress: Any, display_number: Any) -> str | None:
    try:
        congress_number = int(congress)
    except (TypeError, ValueError):
        return None
    normalized = re.sub(r"\s+", "", str(display_number or ""))
    for prefix, kind in TYPE_PREFIXES:
        compact_prefix = prefix.replace(" ", "")
        if normalized.lower().startswith(compact_prefix.lower()):
            number = normalized[len(compact_prefix) :]
            if number.isdigit():
                return f"{congress_number}:{kind}:{int(number)}"
    return None


def known_bill_rows(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category in registry["categories"]:
        for bill in category.get("known_bills", []):
            rows.append(
                {
                    "category_id": category["id"],
                    "category_label": category["label"],
                    "congress": bill["congress"],
                    "number": bill["number"],
                    "acu_position": bill.get("acu_position"),
                    "canonical_key": legislation_key(
                        bill["congress"], bill["number"]
                    ),
                }
            )
    return rows


def clean_prefixed(value: Any, prefix: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return re.sub(rf"^{re.escape(prefix)}\s*", "", text, flags=re.I)


def safe_sheet_name(value: str, used: set[str]) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:31] or "Sheet"
    candidate = cleaned
    suffix = 2
    while candidate.lower() in {name.lower() for name in used}:
        ending = f" {suffix}"
        candidate = cleaned[: 31 - len(ending)] + ending
        suffix += 1
    used.add(candidate)
    return candidate


def style_sheet(
    writer: pd.ExcelWriter,
    sheet_name: str,
    frame: pd.DataFrame,
) -> None:
    workbook = writer.book
    worksheet = writer.sheets[sheet_name]
    header_format = workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#1F4E78",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
        }
    )
    body_format = workbook.add_format({"valign": "top", "text_wrap": True})
    date_format = workbook.add_format(
        {"num_format": "yyyy-mm-dd", "valign": "top"}
    )
    for column_index, column in enumerate(frame.columns):
        worksheet.write(0, column_index, column, header_format)
        sample = frame[column].head(500)
        max_length = max(
            [len(str(column)), *(len(str(value)) for value in sample)],
            default=10,
        )
        width = min(max(max_length + 2, 10), 55)
        chosen_format = (
            date_format if "date" in column.lower() else body_format
        )
        worksheet.set_column(
            column_index, column_index, width, chosen_format
        )
    worksheet.freeze_panes(1, 0)
    if len(frame.columns):
        worksheet.autofilter(0, 0, max(len(frame), 1), len(frame.columns) - 1)
    worksheet.set_default_row(30)
    worksheet.set_row(0, 42)


def category_memberships(
    record: dict[str, Any], query_meta: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    memberships: list[dict[str, Any]] = []
    for query_id in record.get("matched_scoring_categories", []):
        meta = query_meta[query_id]
        memberships.append(
            {
                "query_id": query_id,
                "label": meta["label"],
                "scoring_category": True,
                "record_kind": meta["record_kind"],
            }
        )
    for query_id in record.get("matched_monitor_categories", []):
        meta = query_meta[query_id]
        memberships.append(
            {
                "query_id": query_id,
                "label": meta["label"],
                "scoring_category": False,
                "record_kind": meta["record_kind"],
            }
        )
    if not memberships:
        for query_id in record.get("matched_coverage_probes", []):
            meta = query_meta[query_id]
            memberships.append(
                {
                    "query_id": query_id,
                    "label": f"{meta['label']} (Unclassified)",
                    "scoring_category": False,
                    "record_kind": meta["record_kind"],
                }
            )
    return memberships


def workbook_frames(
    records: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    query_meta: dict[str, dict[str, Any]],
    query_rows: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
    relationship_ledger: list[dict[str, Any]],
    measure_metadata: dict[str, dict[str, Any]],
    membership_review: list[dict[str, Any]],
    text_relevance: list[dict[str, Any]],
    registry: dict[str, Any],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    records_by_key = {record["canonical_key"]: record for record in records}
    edges_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        edges_by_key[edge["canonical_key"]].append(edge)
    known_position = {
        (row["category_id"], row["canonical_key"]): row["acu_position"]
        for row in known_bill_rows(registry)
    }
    review_by_membership = {
        (row["canonical_key"], row["query_id"]): row
        for row in membership_review
    }
    review_memberships_by_record: dict[str, list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for row in membership_review:
        review_memberships_by_record[row["canonical_key"]].append(row)
    candidate_rows: list[dict[str, Any]] = []
    membership_rows: list[dict[str, Any]] = []
    member_rows: list[dict[str, Any]] = []
    category_member_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in records:
        metadata = measure_metadata.get(record["canonical_key"], {})
        review_membership_rows = review_memberships_by_record.get(
            record["canonical_key"], []
        )
        memberships = (
            [
                {
                    "query_id": row["query_id"],
                    "label": row["category_label"],
                    "scoring_category": row["scoring_category"],
                    "record_kind": row.get(
                        "record_kind", "review_candidate_category"
                    ),
                }
                for row in review_membership_rows
            ]
            if review_membership_rows
            else category_memberships(record, query_meta)
        )
        reviewable_scoring_categories = [
            membership["query_id"]
            for membership in memberships
            if membership["scoring_category"]
        ]
        reviewable_monitor_categories = [
            membership["query_id"]
            for membership in memberships
            if not membership["scoring_category"]
            and membership["record_kind"] != "coverage_probe"
        ]
        reviewable_coverage_probes = [
            membership["query_id"]
            for membership in memberships
            if membership["record_kind"] == "coverage_probe"
        ]
        categories_text = " | ".join(
            membership["label"] for membership in memberships
        )
        active_scoring_categories = [
            membership["query_id"]
            for membership in memberships
            if membership["scoring_category"]
            and review_by_membership.get(
                (record["canonical_key"], membership["query_id"]), {}
            ).get("disposition")
            == "include"
        ]
        candidate_rows.append(
            {
                "CanonicalKey": record["canonical_key"],
                "Congress": record["congress"],
                "Type": record["type"],
                "Name": record["display_number"],
                "Title": record.get("title") or record.get("purpose"),
                "OfficialTitle": metadata.get("official_title"),
                "OfficialSummary": metadata.get("summary"),
                "PolicyArea": metadata.get("policy_area"),
                "Sponsor": record.get("sponsor"),
                "CosponsorCount": record.get("cosponsor_count"),
                "InitialActionDate": record.get("initial_action_date_iso"),
                "WindowStatus": record.get("window_status"),
                "CandidateStatus": record.get("candidate_status"),
                "ScoringCategories": " | ".join(
                    reviewable_scoring_categories
                ),
                "DiscoveredScoringCategories": " | ".join(
                    record.get("matched_scoring_categories", [])
                ),
                "ActiveScoringCategories": " | ".join(
                    active_scoring_categories
                ),
                "MonitorCategories": " | ".join(
                    reviewable_monitor_categories
                ),
                "DiscoveredMonitorCategories": " | ".join(
                    record.get("matched_monitor_categories", [])
                ),
                "CoverageProbes": " | ".join(
                    reviewable_coverage_probes
                ),
                "DiscoveredCoverageProbes": " | ".join(
                    record.get("matched_coverage_probes", [])
                ),
                "Committee": clean_prefixed(
                    record.get("committees"), "Committees:"
                ),
                "LatestAction": clean_prefixed(
                    record.get("latest_action"), "Latest Action:"
                ),
                "DetailLink": record.get("canonical_url"),
                "AdvancedSearchResultLink": record.get("result_url"),
            }
        )
        for membership in memberships:
            review = review_by_membership.get(
                (record["canonical_key"], membership["query_id"]), {}
            )
            membership_rows.append(
                {
                    "CanonicalKey": record["canonical_key"],
                    "Name": record["display_number"],
                    "Title": record.get("title") or record.get("purpose"),
                    "CategoryId": membership["query_id"],
                    "Section": membership["label"],
                    "ScoringCategory": membership["scoring_category"],
                    "RecordKind": membership["record_kind"],
                    "ACUPosition": known_position.get(
                        (membership["query_id"], record["canonical_key"])
                    ),
                    "CandidateOnly": True,
                    "ReviewDisposition": review.get("disposition"),
                    "ReviewPriority": review.get("review_priority"),
                    "ReviewBucket": review.get("review_bucket"),
                    "ReviewTriage": review.get("triage"),
                    "ReviewReason": review.get("reason"),
                    "ReviewEvidence": review.get("evidence"),
                    "ReviewEvidenceBasis": review.get("evidence_basis"),
                    "OfficialTitle": review.get("official_title"),
                    "OfficialSummary": review.get("official_summary"),
                    "OfficialTextSource": review.get(
                        "official_text_source_url"
                    ),
                    "OfficialTextIdentifierEvidence": json.dumps(
                        review.get(
                            "official_text_identifier_evidence", []
                        ),
                        ensure_ascii=False,
                    ),
                    "NamedActReferenceOnlyEvidence": json.dumps(
                        review.get(
                            "official_text_act_reference_only_evidence",
                            [],
                        ),
                        ensure_ascii=False,
                    ),
                    "SourceTitleAliasProbeIds": " | ".join(
                        review.get("source_title_alias_probe_ids", [])
                    ),
                    "SourceTitleAliases": " | ".join(
                        review.get("source_title_aliases", [])
                    ),
                    "AmbiguousShortActAliasProbe": review.get(
                        "ambiguous_short_act_alias_probe", False
                    ),
                    "CandidateCategoryEdgeOrigins": " | ".join(
                        review.get(
                            "candidate_category_edge_origins", []
                        )
                    ),
                    "LikelyFalsePositiveReason": review.get(
                        "likely_false_positive_reason"
                    ),
                    "TrustedArticleReviewSignal": review.get(
                        "trusted_article_review_signal"
                    ),
                    "TrustedArticleMatchCount": review.get(
                        "trusted_article_match_count", 0
                    ),
                    "TrustedArticleEvidenceRowCount": review.get(
                        "trusted_article_evidence_row_count", 0
                    ),
                    "TrustedArticleActionMatchCount": review.get(
                        "trusted_article_action_match_count", 0
                    ),
                    "TrustedArticleCategoryOverlapCount": review.get(
                        "trusted_article_category_overlap_count", 0
                    ),
                    "TrustedArticleSuggestedCategories": " | ".join(
                        review.get(
                            "trusted_article_suggested_category_ids", []
                        )
                    ),
                    "TrustedArticleEvidence": json.dumps(
                        review.get("trusted_article_evidence", []),
                        ensure_ascii=False,
                    ),
                    "MatchedSpecificTitleAliases": " | ".join(
                        review.get(
                            "matched_specific_title_aliases", []
                        )
                    ),
                    "MatchedShortActTitleAliases": " | ".join(
                        review.get(
                            "matched_short_act_title_aliases", []
                        )
                    ),
                    "ShortActTitleRequiresIndependentEvidence": (
                        review.get(
                            "short_act_title_requires_independent_evidence",
                            True,
                        )
                    ),
                    "ArticleAbsenceIsNeverExclusionEvidence": (
                        review.get(
                            "article_absence_is_never_exclusion_evidence",
                            True,
                        )
                    ),
                    "Reviewer": review.get("reviewer"),
                    "IncludedAfterReview": (
                        review.get("disposition") == "include"
                    ),
                    "IncludedForScoring": (
                        membership["scoring_category"]
                        and review.get("disposition") == "include"
                    ),
                    "RetainedForAudit": True,
                }
            )
        measure_edges = edges_by_key.get(record["canonical_key"], [])
        if not measure_edges:
            measure_edges = [
                {
                    "role": None,
                    "member": None,
                    "bioguide_id": None,
                    "party": None,
                    "state": None,
                    "district": None,
                    "sponsorship_date": None,
                    "original_cosponsor": None,
                    "relationship_status": "missing_relationship_enrichment",
                    "withdrawn_date": None,
                    "withdrawal_explanation": None,
                    "member_url": None,
                }
            ]
        for edge in measure_edges:
            row = {
                "Section": categories_text,
                "Role": edge.get("role"),
                "Member": edge.get("member"),
                "Name": record["display_number"],
                "Title": record.get("title") or record.get("purpose"),
                "Sponsor": record.get("sponsor"),
                "CosponsorCount": record.get("cosponsor_count"),
                "Committee": clean_prefixed(
                    record.get("committees"), "Committees:"
                ),
                "LastAction": clean_prefixed(
                    record.get("latest_action"), "Latest Action:"
                ),
                "Congress": record["congress"],
                "DecayWeight": DECAY_WEIGHT.get(record["congress"]),
                "LegacyDecayWeight": LEGACY_DECAY_WEIGHT.get(
                    record["congress"]
                ),
                "DetailLink": record.get("canonical_url"),
                "bill_summary": metadata.get("summary"),
                "PolicyArea": metadata.get("policy_area"),
                "OriginalCosponsor": edge.get("original_cosponsor"),
                "SponsorshipDate": edge.get("sponsorship_date"),
                "RelationshipStatus": edge.get("relationship_status"),
                "WithdrawnDate": edge.get("withdrawn_date"),
                "WithdrawalExplanation": edge.get(
                    "withdrawal_explanation"
                ),
                "BioguideId": edge.get("bioguide_id"),
                "Party": edge.get("party"),
                "State": edge.get("state"),
                "District": edge.get("district"),
                "MemberLink": edge.get("member_url"),
                "CanonicalKey": record["canonical_key"],
                "WindowStatus": record.get("window_status"),
                "CandidateStatus": record.get("candidate_status"),
                "ScoringCategories": " | ".join(
                    reviewable_scoring_categories
                ),
                "DiscoveredScoringCategories": " | ".join(
                    record.get("matched_scoring_categories", [])
                ),
                "ActiveScoringCategories": " | ".join(
                    active_scoring_categories
                ),
                "MonitorCategories": " | ".join(
                    reviewable_monitor_categories
                ),
                "DiscoveredMonitorCategories": " | ".join(
                    record.get("matched_monitor_categories", [])
                ),
                "CoverageProbes": " | ".join(
                    reviewable_coverage_probes
                ),
                "DiscoveredCoverageProbes": " | ".join(
                    record.get("matched_coverage_probes", [])
                ),
            }
            member_rows.append(row)
            for membership in memberships:
                category_row = dict(row)
                category_row["Section"] = membership["label"]
                category_row["CategoryId"] = membership["query_id"]
                category_row["ScoringCategory"] = membership[
                    "scoring_category"
                ]
                category_row["ACUPosition"] = known_position.get(
                    (membership["query_id"], record["canonical_key"])
                )
                review = review_by_membership.get(
                    (record["canonical_key"], membership["query_id"]), {}
                )
                category_row["ReviewDisposition"] = review.get(
                    "disposition"
                )
                category_row["ReviewPriority"] = review.get(
                    "review_priority"
                )
                category_row["ReviewBucket"] = review.get("review_bucket")
                category_row["ReviewReason"] = review.get("reason")
                category_row["ReviewEvidence"] = review.get("evidence")
                category_row["ReviewEvidenceBasis"] = review.get(
                    "evidence_basis"
                )
                category_row["OfficialTitle"] = review.get(
                    "official_title"
                )
                category_row["OfficialSummary"] = review.get(
                    "official_summary"
                )
                category_row["OfficialTextSource"] = review.get(
                    "official_text_source_url"
                )
                category_row[
                    "OfficialTextIdentifierEvidence"
                ] = json.dumps(
                    review.get("official_text_identifier_evidence", []),
                    ensure_ascii=False,
                )
                category_row["NamedActReferenceOnlyEvidence"] = json.dumps(
                    review.get(
                        "official_text_act_reference_only_evidence", []
                    ),
                    ensure_ascii=False,
                )
                category_row["SourceTitleAliasProbeIds"] = " | ".join(
                    review.get("source_title_alias_probe_ids", [])
                )
                category_row["SourceTitleAliases"] = " | ".join(
                    review.get("source_title_aliases", [])
                )
                category_row["AmbiguousShortActAliasProbe"] = review.get(
                    "ambiguous_short_act_alias_probe", False
                )
                category_row["CandidateCategoryEdgeOrigins"] = " | ".join(
                    review.get("candidate_category_edge_origins", [])
                )
                category_row["LikelyFalsePositiveReason"] = review.get(
                    "likely_false_positive_reason"
                )
                category_row["TrustedArticleReviewSignal"] = review.get(
                    "trusted_article_review_signal"
                )
                category_row["TrustedArticleMatchCount"] = review.get(
                    "trusted_article_match_count", 0
                )
                category_row[
                    "TrustedArticleEvidenceRowCount"
                ] = review.get(
                    "trusted_article_evidence_row_count", 0
                )
                category_row["TrustedArticleActionMatchCount"] = review.get(
                    "trusted_article_action_match_count", 0
                )
                category_row[
                    "TrustedArticleCategoryOverlapCount"
                ] = review.get(
                    "trusted_article_category_overlap_count", 0
                )
                category_row[
                    "TrustedArticleSuggestedCategories"
                ] = " | ".join(
                    review.get(
                        "trusted_article_suggested_category_ids", []
                    )
                )
                category_row["TrustedArticleEvidence"] = json.dumps(
                    review.get("trusted_article_evidence", []),
                    ensure_ascii=False,
                )
                category_row[
                    "MatchedSpecificTitleAliases"
                ] = " | ".join(
                    review.get(
                        "matched_specific_title_aliases", []
                    )
                )
                category_row[
                    "MatchedShortActTitleAliases"
                ] = " | ".join(
                    review.get(
                        "matched_short_act_title_aliases", []
                    )
                )
                category_row[
                    "ShortActTitleRequiresIndependentEvidence"
                ] = review.get(
                    "short_act_title_requires_independent_evidence", True
                )
                category_row[
                    "ArticleAbsenceIsNeverExclusionEvidence"
                ] = review.get(
                    "article_absence_is_never_exclusion_evidence", True
                )
                category_row["IncludedAfterReview"] = (
                    review.get("disposition") == "include"
                )
                category_row["IncludedForScoring"] = (
                    membership["scoring_category"]
                    and review.get("disposition") == "include"
                )
                category_row["RetainedForAudit"] = True
                category_member_rows[membership["query_id"]].append(
                    category_row
                )

    core_frames = {
        "All Candidates": pd.DataFrame(candidate_rows),
        "Member x Measure": pd.DataFrame(member_rows),
        "Category Memberships": pd.DataFrame(membership_rows),
        "Query Audit": pd.DataFrame(query_rows),
        "Coverage Gaps": pd.DataFrame(gaps),
        "Exclusions": pd.DataFrame(
            exclusions,
            columns=[
                "canonical_key",
                "display_number",
                "title",
                "query_id",
                "category_label",
                "triage",
                "disposition",
                "reason",
                "evidence",
                "evidence_basis",
                "official_title",
                "official_summary",
                "official_text_source_url",
                "official_text_act_reference_only_evidence",
                "source_title_alias_probe_ids",
                "source_title_aliases",
                "reviewer",
                "reviewed_at",
                "congress_gov_url",
                "candidate_retained_in_discovery_corpus",
            ],
        ),
        "Relationship Verification": pd.DataFrame(relationship_ledger),
        "Text Evidence": pd.DataFrame(
            [
                {
                    "CanonicalKey": row.get("canonical_key"),
                    "CategoryId": row.get("query_id"),
                    "Category": row.get("category_label"),
                    "EvidenceRole": row.get("evidence_role"),
                    "SourceCoverageProbes": " | ".join(
                        row.get("source_coverage_probe_ids", [])
                    ),
                    "EvidenceState": row.get("evidence_state"),
                    "TextAvailable": row.get("text_available"),
                    "TextVersion": row.get("version_label"),
                    "LocalizedIdentifiers": " | ".join(
                        row.get("localized_identifiers", [])
                    ),
                    "IdentifierMatchModes": " | ".join(
                        sorted(
                            {
                                item.get("match_mode")
                                for item in row.get(
                                    "identifier_evidence", []
                                )
                                if item.get("match_mode")
                            }
                        )
                    ),
                    "NamedActReferenceOnly": " | ".join(
                        item.get("identifier", "")
                        for item in row.get("identifier_evidence", [])
                        if item.get("act_reference_only")
                    ),
                    "SeparatedIdentifiers": json.dumps(
                        row.get("separated_identifiers", []),
                        ensure_ascii=False,
                    ),
                    "LocalizedContexts": "\n\n".join(
                        f"{item.get('identifier')}: {item.get('context')}"
                        for item in row.get("identifier_evidence", [])
                        if item.get("localized_match")
                        and item.get("context")
                    ),
                    "SourceURL": row.get("source_url"),
                    "CandidateRetained": row.get(
                        "candidate_retained_in_discovery_corpus"
                    ),
                }
                for row in text_relevance
            ]
        ),
        "Membership Review": pd.DataFrame(membership_review),
    }
    category_frames = {
        query_id: pd.DataFrame(rows)
        for query_id, rows in category_member_rows.items()
    }
    return core_frames, category_frames


def export_workbook(
    path: Path,
    core_frames: dict[str, pd.DataFrame],
    category_frames: dict[str, pd.DataFrame],
    query_meta: dict[str, dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    with pd.ExcelWriter(
        path,
        engine="xlsxwriter",
        engine_kwargs={"options": {"strings_to_urls": False}},
    ) as writer:
        for name, frame in core_frames.items():
            sheet = safe_sheet_name(name, used)
            frame.to_excel(writer, sheet_name=sheet, index=False)
            style_sheet(writer, sheet, frame)
        ordered_categories = sorted(
            category_frames,
            key=lambda query_id: list(query_meta).index(query_id),
        )
        for index, query_id in enumerate(ordered_categories, start=1):
            label = query_meta[query_id]["label"]
            sheet = safe_sheet_name(f"{index:02d} {label}", used)
            frame = category_frames[query_id]
            frame.to_excel(writer, sheet_name=sheet, index=False)
            style_sheet(writer, sheet, frame)


def main() -> int:
    args = parse_args()
    run_dir = args.run.resolve()
    out_dir = (args.out or run_dir / "reconciliation").resolve()
    relationships_dir = (
        args.relationships.resolve()
        if args.relationships
        else run_dir / "relationships"
    )
    membership_review_dir = (
        args.membership_review.resolve()
        if args.membership_review
        else run_dir / "candidate_membership_review"
    )
    text_relevance_dir = (
        args.text_relevance.resolve()
        if args.text_relevance
        else run_dir / "text_relevance"
    )
    article_cross_reference_dir = run_dir / "article_cross_reference"
    manifest = read_json(run_dir / "manifest.json")
    if manifest["status"] != "complete":
        raise RuntimeError("Advanced Search run is not complete")
    registry = read_json(args.registry.resolve())
    records = read_ndjson(run_dir / manifest["union"]["records_file"])
    memberships = read_ndjson(
        run_dir / manifest["union"]["memberships_file"]
    )
    collector_exclusions = read_ndjson(run_dir / "exclusion_ledger.ndjson")
    collector_gaps = read_ndjson(run_dir / "coverage_gap_ledger.ndjson")
    record_keys = {record["canonical_key"] for record in records}
    query_meta = {query["query_id"]: query for query in manifest["queries"]}

    text_relevance_manifest_path = text_relevance_dir / "manifest.json"
    text_relevance_manifest: dict[str, Any] | None = None
    text_relevance: list[dict[str, Any]] = []
    if text_relevance_manifest_path.exists():
        text_relevance_manifest = read_json(text_relevance_manifest_path)
        if text_relevance_manifest.get("status") == "complete":
            text_relevance = read_ndjson(
                text_relevance_dir
                / Path(text_relevance_manifest["ledger_file"]).name
            )

    article_manifest_path = article_cross_reference_dir / "manifest.json"
    article_manifest: dict[str, Any] | None = None
    article_reverse_audit: list[dict[str, Any]] = []
    article_ambiguous_unresolved: list[dict[str, Any]] = []
    if article_manifest_path.exists():
        article_manifest = read_json(article_manifest_path)
        if article_manifest.get("status") == "complete":
            article_reverse_audit = read_ndjson(
                article_cross_reference_dir
                / article_manifest["reverse_citation_audit_file"]
            )
            article_ambiguous_unresolved = read_ndjson(
                article_cross_reference_dir
                / article_manifest[
                    "ambiguous_unresolved_references_file"
                ]
            )

    membership_review_manifest_path = (
        membership_review_dir / "manifest.json"
    )
    membership_review_manifest: dict[str, Any] | None = None
    membership_review: list[dict[str, Any]] = []
    review_exclusions: list[dict[str, Any]] = []
    unresolved_memberships: list[dict[str, Any]] = []
    if membership_review_manifest_path.exists():
        membership_review_manifest = read_json(
            membership_review_manifest_path
        )
        review_display_file = membership_review_manifest.get(
            "sorted_review_queue_file",
            membership_review_manifest["ledger_file"],
        )
        membership_review = read_ndjson(
            membership_review_dir
            / Path(review_display_file).name
        )
        review_exclusions = [
            row
            for row in membership_review
            if row.get("disposition") == "exclude_false_positive"
        ]
        unresolved_memberships = [
            row
            for row in membership_review
            if row.get("disposition") == "unresolved"
        ]
    exclusions = [*collector_exclusions, *review_exclusions]

    query_rows = []
    for query in manifest["queries"]:
        validation = query["validation"]
        query_rows.append(
            {
                "QueryId": query["query_id"],
                "Label": query["label"],
                "ScoringCategory": query["scoring_category"],
                "RecordKind": query["record_kind"],
                "ParentCategoryId": query.get("parent_category_id"),
                "TitleAlias": query.get("title_alias"),
                "AmbiguousShortActAlias": query.get(
                    "ambiguous_short_act_alias", False
                ),
                "MembershipNonDispositive": query.get(
                    "membership_non_dispositive", False
                ),
                "ResultTotal": query["result_total"],
                "UniqueRecordCount": query["unique_record_count"],
                "PageCount": query["page_count"],
                "ExactResultTotal": validation["exact_result_total"],
                "UniqueResultTotal": validation["unique_result_total"],
                "EveryPageExact": validation["every_page_exact"],
                "QueryFile": query["query_file"],
                "RecordsSHA256": query["records_sha256"],
            }
        )

    known_rows = known_bill_rows(registry)
    known_results = [
        {**row, "found": row["canonical_key"] in record_keys}
        for row in known_rows
    ]
    known_missing = [row for row in known_results if not row["found"]]

    archive = pd.read_excel(args.archive.resolve(), sheet_name="All Bills")
    archive_keys = {
        key
        for key in (
            legislation_key(row.Congress, row.Name)
            for row in archive.itertuples(index=False)
        )
        if key
    }
    historical_missing = sorted(archive_keys - record_keys)
    new_not_archive = sorted(record_keys - archive_keys)
    records_by_congress = Counter(
        int(record["congress"]) for record in records
    )
    required_congresses = set(
        registry["collection_contract"]["congresses"]
    )
    missing_required_congresses = sorted(
        required_congresses - set(records_by_congress)
    )
    required_query_ids = {
        category["id"] for category in registry["categories"]
    } | {
        probe["id"]
        for probe in registry.get("coverage_probes", [])
        if probe.get("required_for_acceptance", False)
    }
    completed_query_ids = [row["query_id"] for row in manifest["queries"]]
    completed_query_id_set = set(completed_query_ids)
    selected_query_id_set = set(manifest["selected_query_ids"])
    missing_required_query_ids = sorted(
        required_query_ids - completed_query_id_set
    )
    manifest_registry_sha256 = manifest.get("registry_sha256")
    current_registry_sha256 = sha256(args.registry.resolve())
    registry_hash_matches_manifest = (
        manifest_registry_sha256 == current_registry_sha256
    )
    required_legislation_types = set(
        registry["collection_contract"]["legislation_types"]
    )
    observed_legislation_types = {record["type"] for record in records}
    missing_required_legislation_types = sorted(
        required_legislation_types - observed_legislation_types
    )

    relationship_manifest_path = relationships_dir / "manifest.json"
    relationship_complete = relationship_manifest_path.exists()
    relationship_manifest: dict[str, Any] | None = None
    edges: list[dict[str, Any]] = []
    relationship_ledger: list[dict[str, Any]] = []
    measure_metadata_rows: list[dict[str, Any]] = []
    if relationship_complete:
        relationship_manifest = read_json(relationship_manifest_path)
        relationship_complete = relationship_manifest["status"] == "complete"
        if relationship_complete:
            edges = read_ndjson(
                relationships_dir / relationship_manifest["edges_file"]
            )
            relationship_ledger = read_ndjson(
                relationships_dir
                / relationship_manifest["verification_ledger_file"]
            )
            if relationship_manifest.get("measure_metadata_file"):
                measure_metadata_rows = read_ndjson(
                    relationships_dir
                    / relationship_manifest["measure_metadata_file"]
                )
    measure_metadata = {
        row["canonical_key"]: row for row in measure_metadata_rows
    }

    sponsors_by_key = {
        edge["canonical_key"]
        for edge in edges
        if edge.get("role") == "Sponsor" and edge.get("member")
    }
    missing_sponsors = sorted(record_keys - sponsors_by_key)
    recall_query = query_meta.get("credit_union_headline_recall_net")
    recall_records = [
        record
        for record in records
        if "credit_union_headline_recall_net"
        in record.get("matched_coverage_probes", [])
    ]
    relationship_gate = (
        relationship_complete
        and not relationship_ledger
        and not missing_sponsors
    )
    query_gate = all(
        row["ExactResultTotal"]
        and row["UniqueResultTotal"]
        and row["EveryPageExact"]
        and row["ResultTotal"] == row["UniqueRecordCount"]
        for row in query_rows
    )
    query_dimension_gate = all(
        sum(int(value) for value in query.get("counts_by_congress", {}).values())
        == query["result_total"]
        and {
            int(congress)
            for congress in query.get("counts_by_congress", {})
        }
        <= required_congresses
        and sum(int(value) for value in query.get("counts_by_type", {}).values())
        == query["result_total"]
        and set(query.get("counts_by_type", {}))
        <= required_legislation_types
        for query in manifest["queries"]
    )
    membership_keys = {row["canonical_key"] for row in memberships}
    classified_record_keys = {
        row["canonical_key"]
        for row in records
        if row.get("matched_scoring_categories")
        or row.get("matched_monitor_categories")
    }
    reviewable_membership_keys: set[tuple[str, str]] = set()
    for row in memberships:
        query_id = row["query_id"]
        meta = query_meta[query_id]
        if (
            row.get("membership_non_dispositive")
            and row.get("parent_category_id")
        ):
            reviewable_membership_keys.add(
                (row["canonical_key"], row["parent_category_id"])
            )
        elif (
            meta["record_kind"] != "coverage_probe"
            or row["canonical_key"] not in classified_record_keys
        ):
            reviewable_membership_keys.add(
                (row["canonical_key"], query_id)
            )
    review_membership_key_list = [
        (row["canonical_key"], row["query_id"])
        for row in membership_review
    ]
    review_membership_keys = set(review_membership_key_list)
    review_duplicate_membership_keys = sorted(
        key
        for key, count in Counter(review_membership_key_list).items()
        if count > 1
    )
    review_records = {key[0] for key in review_membership_keys}
    missing_raw_review_memberships = sorted(
        reviewable_membership_keys - review_membership_keys
    )
    records_without_review_membership = sorted(record_keys - review_records)
    membership_review_key_gate = (
        bool(membership_review_manifest)
        and not review_duplicate_membership_keys
        and not missing_raw_review_memberships
        and not records_without_review_membership
        and membership_review_manifest.get("membership_count")
        == len(membership_review)
        and membership_review_manifest.get(
            "distinct_review_membership_key_count"
        )
        == len(review_membership_keys)
    )
    text_relevance_keys = {
        (row["canonical_key"], row["query_id"])
        for row in text_relevance
        if row.get("evidence_role") == "category_membership"
    }
    expected_text_membership_keys = {
        key
        for key in reviewable_membership_keys
        if query_meta[key[1]]["record_kind"] != "coverage_probe"
    }
    unclassified_coverage_record_keys = {
        key[0]
        for key in reviewable_membership_keys
        if query_meta[key[1]]["record_kind"] == "coverage_probe"
    }
    text_classification_record_keys = {
        row["canonical_key"]
        for row in text_relevance
        if row.get("evidence_role") == "coverage_probe_classification"
    }
    text_relevance_complete = (
        bool(text_relevance_manifest)
        and expected_text_membership_keys == text_relevance_keys
        and unclassified_coverage_record_keys
        <= text_classification_record_keys
    )
    article_congress_qualified_absent = [
        row
        for row in article_reverse_audit
        if row.get("canonical_key")
        and row.get("absent_from_discovery_union") is True
    ]
    article_reverse_coverage_gate = (
        bool(article_manifest)
        and article_manifest.get(
            "reverse_citation_audit_is_lossless_by_occurrence"
        )
        is True
        and not article_congress_qualified_absent
    )
    acceptance = {
        "registry_hash_matches_manifest": registry_hash_matches_manifest,
        "manifest_registry_sha256": manifest_registry_sha256,
        "current_registry_sha256": current_registry_sha256,
        "query_page_reconciliation": query_gate,
        "query_congress_and_type_dimension_reconciliation": (
            query_dimension_gate
        ),
        "selected_queries_complete": (
            len(completed_query_ids) == len(completed_query_id_set)
            and completed_query_id_set == selected_query_id_set
        ),
        "required_registry_queries_complete": (
            required_query_ids <= completed_query_id_set
            and required_query_ids <= selected_query_id_set
        ),
        "required_registry_query_count": len(required_query_ids),
        "completed_required_registry_query_count": len(
            required_query_ids & completed_query_id_set
        ),
        "missing_required_registry_query_ids": missing_required_query_ids,
        "union_records_have_membership": record_keys == membership_keys,
        "outline_named_found": len(known_rows) - len(known_missing),
        "outline_named_total": len(known_rows),
        "outline_named_missing": known_missing,
        "interchange_family_found": len(INTERCHANGE_FAMILY & record_keys),
        "interchange_family_missing": sorted(
            INTERCHANGE_FAMILY - record_keys
        ),
        "credit_union_recall_result_total": (
            recall_query["result_total"] if recall_query else None
        ),
        "credit_union_recall_unique_records": len(recall_records),
        "credit_union_recall_exact": bool(recall_query)
        and len(recall_records) == recall_query["result_total"],
        "historical_archive_total": len(archive_keys),
        "historical_archive_recovered": len(archive_keys & record_keys),
        "historical_archive_missing": historical_missing,
        "required_congress_record_counts": {
            str(congress): records_by_congress.get(congress, 0)
            for congress in sorted(required_congresses)
        },
        "missing_required_congresses": missing_required_congresses,
        "six_year_congress_scope_present": not missing_required_congresses,
        "required_legislation_types_present": (
            not missing_required_legislation_types
        ),
        "missing_required_legislation_types": (
            missing_required_legislation_types
        ),
        "automated_semantic_exclusions": len(collector_exclusions),
        "reviewed_false_positive_exclusions": len(review_exclusions),
        "membership_review_key_set_complete": membership_review_key_gate,
        "membership_review_duplicate_keys": (
            review_duplicate_membership_keys
        ),
        "membership_review_missing_raw_keys": (
            missing_raw_review_memberships
        ),
        "records_without_membership_review": (
            records_without_review_membership
        ),
        "membership_review_complete": membership_review_key_gate
        and not unresolved_memberships,
        "membership_review_unresolved_count": len(
            unresolved_memberships
        ),
        "official_text_relevance_complete": text_relevance_complete,
        "trusted_article_cross_reference_complete": bool(
            membership_review_manifest
        )
        and membership_review_manifest.get(
            "article_cross_reference_complete", False
        ),
        "trusted_article_reverse_citation_lossless": bool(
            article_manifest
        )
        and article_manifest.get(
            "reverse_citation_audit_is_lossless_by_occurrence", False
        ),
        "trusted_article_congress_qualified_absent_occurrence_count": (
            len(article_congress_qualified_absent)
        ),
        "trusted_article_congress_qualified_absent_unique_measure_count": (
            len(
                {
                    row["canonical_key"]
                    for row in article_congress_qualified_absent
                }
            )
        ),
        "trusted_article_reverse_coverage_complete": (
            article_reverse_coverage_gate
        ),
        "official_text_relevance_expected_memberships": len(
            expected_text_membership_keys
        ),
        "official_text_relevance_recovered_memberships": len(
            text_relevance_keys
        ),
        "unclassified_coverage_records_scanned": len(
            unclassified_coverage_record_keys
            & text_classification_record_keys
        ),
        "unclassified_coverage_records_expected": len(
            unclassified_coverage_record_keys
        ),
        "relationship_enrichment_complete": relationship_complete,
        "relationship_unresolved_measures": len(relationship_ledger),
        "missing_sponsor_records": missing_sponsors,
    }
    acceptance["passed"] = (
        acceptance["registry_hash_matches_manifest"]
        and acceptance["query_page_reconciliation"]
        and acceptance[
            "query_congress_and_type_dimension_reconciliation"
        ]
        and acceptance["selected_queries_complete"]
        and acceptance["required_registry_queries_complete"]
        and acceptance["union_records_have_membership"]
        and acceptance["outline_named_found"]
        == acceptance["outline_named_total"]
        and acceptance["interchange_family_found"] == 6
        and acceptance["credit_union_recall_exact"]
        and not acceptance["historical_archive_missing"]
        and acceptance["six_year_congress_scope_present"]
        and acceptance["required_legislation_types_present"]
        and acceptance["automated_semantic_exclusions"] == 0
        and acceptance["official_text_relevance_complete"]
        and acceptance["trusted_article_cross_reference_complete"]
        and acceptance["trusted_article_reverse_coverage_complete"]
        and acceptance["membership_review_complete"]
        and relationship_gate
    )

    combined_gaps = list(collector_gaps)
    if not registry_hash_matches_manifest:
        combined_gaps.append(
            {
                "gap_kind": "stale_collection_registry_hash",
                "manifest_registry_sha256": manifest_registry_sha256,
                "current_registry_sha256": current_registry_sha256,
                "disposition": "acceptance_failure",
                "explanation": (
                    "The completed discovery manifest was not collected "
                    "against the current strict search registry."
                ),
            }
        )
    combined_gaps.extend(
        {
            "gap_kind": "required_registry_query_not_completed",
            "query_id": query_id,
            "disposition": "acceptance_failure",
            "explanation": (
                "A category or required coverage probe in the current "
                "registry is absent from the completed collection manifest."
            ),
        }
        for query_id in missing_required_query_ids
    )
    combined_gaps.extend(
        {
            "gap_kind": "required_legislation_type_not_recovered",
            "legislation_type": legislation_type,
            "disposition": "acceptance_failure",
            "explanation": (
                "The collection contract selected this legislation type, "
                "but the reconciled union contains no record of that type."
            ),
        }
        for legislation_type in missing_required_legislation_types
    )
    combined_gaps.extend(
        {
            "gap_kind": (
                "trusted_article_congress_qualified_reference_missing_"
                "from_union"
            ),
            **row,
            "disposition": "acceptance_failure_targeted_recovery_required",
            "explanation": (
                "A bill citation in the frozen trusted press corpus maps to "
                "the required Congress window but is absent from the final "
                "Congress.gov discovery union."
            ),
        }
        for row in article_congress_qualified_absent
    )
    combined_gaps.extend(
        {
            "gap_kind": "trusted_article_reference_ambiguous_or_unqualified",
            **row,
            "disposition": "visible_non_dispositive_review_item",
            "explanation": (
                "The frozen trusted article contains a bill citation that "
                "cannot be Congress-qualified inside the required window; "
                "it remains visible but cannot create or exclude a candidate."
            ),
        }
        for row in article_ambiguous_unresolved
        if not row.get("canonical_key")
    )
    combined_gaps.extend(
        {
            "gap_kind": "required_congress_has_no_records",
            "congress": congress,
            "disposition": "acceptance_failure",
            "explanation": (
                "The six-year collection contract requires this Congress, "
                "but the Advanced Search union contains no record from it."
            ),
        }
        for congress in missing_required_congresses
    )
    combined_gaps.extend(
        {
            "gap_kind": "historical_archive_not_recovered",
            "canonical_key": key,
            "disposition": "requires_targeted_congress_gov_recovery",
            "explanation": (
                "Present in the historical archive but absent from the new "
                "Advanced Search union."
            ),
        }
        for key in historical_missing
    )
    combined_gaps.extend(
        {
            "gap_kind": "outline_named_bill_not_recovered",
            "canonical_key": row["canonical_key"],
            "display_number": row["number"],
            "category_id": row["category_id"],
            "disposition": "acceptance_failure",
            "explanation": "Named in the authoritative outline but not recovered.",
        }
        for row in known_missing
    )
    combined_gaps.extend(
        {
            "gap_kind": "relationship_verification_unresolved",
            **row,
        }
        for row in relationship_ledger
    )
    combined_gaps.extend(
        {
            "gap_kind": "category_membership_review_unresolved",
            "canonical_key": row["canonical_key"],
            "display_number": row.get("display_number"),
            "query_id": row["query_id"],
            "category_label": row.get("category_label"),
            "triage": row.get("triage"),
            "live_identifier_query_hits": row.get(
                "live_identifier_query_hits"
            ),
            "official_text_evidence_state": row.get(
                "official_text_evidence_state"
            ),
            "official_text_separated_identifiers": row.get(
                "official_text_separated_identifiers"
            ),
            "suggested_categories_from_official_text": row.get(
                "suggested_categories_from_official_text"
            ),
            "disposition": "requires_review_before_scoring",
            "explanation": (
                "Candidate is retained, but category relevance has not been "
                "ratified or explicitly excluded as a false positive."
            ),
            "congress_gov_url": row.get("congress_gov_url"),
        }
        for row in unresolved_memberships
    )
    combined_gaps.extend(
        {
            "gap_kind": "raw_membership_missing_from_review_ledger",
            "canonical_key": canonical_key,
            "query_id": query_id,
            "disposition": "acceptance_failure",
            "explanation": (
                "A reviewable raw or normalized membership has no row in "
                "the membership-review ledger."
            ),
        }
        for canonical_key, query_id in missing_raw_review_memberships
    )
    combined_gaps.extend(
        {
            "gap_kind": "duplicate_membership_review_key",
            "canonical_key": canonical_key,
            "query_id": query_id,
            "disposition": "acceptance_failure",
            "explanation": (
                "The membership-review ledger contains a duplicate "
                "Congress-qualified record/category key."
            ),
        }
        for canonical_key, query_id in review_duplicate_membership_keys
    )
    combined_gaps.extend(
        {
            "gap_kind": "record_has_no_membership_review_row",
            "canonical_key": canonical_key,
            "disposition": "acceptance_failure",
            "explanation": (
                "A discovered record is absent from the membership-review "
                "ledger."
            ),
        }
        for canonical_key in records_without_review_membership
    )
    combined_gaps.extend(
        {
            "gap_kind": "missing_sponsor_edge",
            "canonical_key": key,
            "disposition": "acceptance_failure",
            "explanation": "No official Sponsor edge was recovered.",
        }
        for key in missing_sponsors
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    gaps_path = out_dir / "complete_coverage_gap_ledger.ndjson"
    write_ndjson(gaps_path, combined_gaps)
    report = {
        "schema": "acu-congress-legislation-reconciliation-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "primary_instrument": "Congress.gov Advanced Search - Legislation",
        "quorum_used": False,
        "run_manifest": relative(run_dir / "manifest.json"),
        "run_manifest_sha256": sha256(run_dir / "manifest.json"),
        "registry": relative(args.registry.resolve()),
        "registry_sha256": sha256(args.registry.resolve()),
        "historical_archive": {
            "path": relative(args.archive.resolve()),
            "distinct_records": len(archive_keys),
            "recovered": len(archive_keys & record_keys),
            "missing": len(historical_missing),
            "missing_keys": historical_missing,
            "new_not_in_archive": len(new_not_archive),
        },
        "corpus": {
            "unique_record_count": len(records),
            "membership_count": len(memberships),
            "counts_by_congress": dict(
                sorted(Counter(row["congress"] for row in records).items())
            ),
            "counts_by_type": dict(
                sorted(Counter(row["type"] for row in records).items())
            ),
            "boundary_extension_count": sum(
                row.get("window_status")
                == "boundary_extension_before_window"
                for row in records
            ),
            "date_unknown_count": sum(
                row.get("window_status") == "date_unknown" for row in records
            ),
            "recall_only_unclassified_count": sum(
                bool(row.get("matched_coverage_probes"))
                and not row.get("matched_scoring_categories")
                and not row.get("matched_monitor_categories")
                for row in records
            ),
        },
        "relationships": {
            "complete": relationship_complete,
            "edge_count": len(edges),
            "unresolved_measure_count": len(relationship_ledger),
            "missing_sponsor_count": len(missing_sponsors),
        },
        "official_text_relevance": {
            "manifest_present": bool(text_relevance_manifest),
            "complete": text_relevance_complete,
            "expected_membership_count": len(
                expected_text_membership_keys
            ),
            "recovered_membership_count": len(text_relevance_keys),
            "unclassified_coverage_record_count": len(
                unclassified_coverage_record_keys
            ),
            "unclassified_coverage_records_scanned": len(
                unclassified_coverage_record_keys
                & text_classification_record_keys
            ),
            "localized_official_text_membership_count": sum(
                row.get("evidence_state")
                == "localized_official_text_evidence"
                for row in text_relevance
            ),
            "no_localized_identifier_evidence_count": sum(
                row.get("evidence_state")
                == "no_localized_identifier_evidence"
                for row in text_relevance
            ),
            "official_text_unavailable_count": sum(
                row.get("evidence_state") == "official_text_unavailable"
                for row in text_relevance
            ),
        },
        "trusted_article_reverse_coverage": {
            "manifest_present": bool(article_manifest),
            "lossless_by_citation_occurrence": bool(article_manifest)
            and article_manifest.get(
                "reverse_citation_audit_is_lossless_by_occurrence", False
            ),
            "citation_occurrence_count": len(article_reverse_audit),
            "congress_qualified_absent_occurrence_count": len(
                article_congress_qualified_absent
            ),
            "congress_qualified_absent_unique_measure_count": len(
                {
                    row["canonical_key"]
                    for row in article_congress_qualified_absent
                }
            ),
            "ambiguous_or_unqualified_occurrence_count": len(
                article_ambiguous_unresolved
            ),
            "complete": article_reverse_coverage_gate,
        },
        "membership_review": {
            "manifest_present": bool(membership_review_manifest),
            "membership_count": len(membership_review),
            "included_count": sum(
                row.get("disposition") == "include"
                for row in membership_review
            ),
            "false_positive_exclusion_count": len(review_exclusions),
            "unresolved_count": len(unresolved_memberships),
            "promotion_gate_passed": bool(membership_review_manifest)
            and not unresolved_memberships,
            "article_cross_reference_complete": bool(
                membership_review_manifest
            )
            and membership_review_manifest.get(
                "article_cross_reference_complete", False
            ),
            "trusted_article_matched_membership_count": (
                membership_review_manifest.get(
                    "trusted_article_matched_membership_count", 0
                )
                if membership_review_manifest
                else 0
            ),
            "article_based_automated_inclusion_count": 0,
            "article_based_automated_exclusion_count": 0,
            "article_absence_is_never_exclusion_evidence": True,
        },
        "scoring_decay": {
            "active": DECAY_WEIGHT,
            "legacy_comparison": LEGACY_DECAY_WEIGHT,
            "affects_collection_or_exclusion": False,
        },
        "acceptance": acceptance,
        "known_bill_results": known_results,
        "query_audit": query_rows,
        "coverage_gap_ledger": relative(gaps_path),
        "coverage_gap_count": len(combined_gaps),
        "exclusion_count": len(exclusions),
    }
    report_path = out_dir / "reconciliation_report.json"
    write_json(report_path, report)

    markdown_path = out_dir / "RECONCILIATION_REPORT.md"
    lines = [
        "# Congress.gov Legislation Reconciliation",
        "",
        f"- Primary instrument: **{report['primary_instrument']}**",
        "- Quorum used: **No**",
        f"- Unique Congress-qualified records: **{len(records):,}**",
        "- Records by required Congress: **"
        + " / ".join(
            f"{congress}={records_by_congress.get(congress, 0):,}"
            for congress in sorted(required_congresses)
        )
        + "**",
        f"- Query memberships retained: **{len(memberships):,}**",
        f"- Historical archive recovered: **{len(archive_keys & record_keys):,}/{len(archive_keys):,}**",
        f"- New records beyond historical archive: **{len(new_not_archive):,}**",
        f"- Outline-named bills: **{acceptance['outline_named_found']}/{acceptance['outline_named_total']}**",
        f"- Credit Card Competition Act family: **{acceptance['interchange_family_found']}/6**",
        f"- Credit-union recall net: **{len(recall_records):,}/{acceptance['credit_union_recall_result_total'] or 0:,} exact**",
        f"- Boundary-extension records retained: **{report['corpus']['boundary_extension_count']:,}**",
        f"- Recall-only unclassified candidates retained: **{report['corpus']['recall_only_unclassified_count']:,}**",
        f"- Automated semantic exclusions: **{len(collector_exclusions):,}**",
        f"- Reviewed false-positive exclusions: **{len(review_exclusions):,}**",
        f"- Official-text relevance memberships recovered: **{len(text_relevance_keys):,}/{len(expected_text_membership_keys):,}**",
        f"- Unclassified coverage records text-scanned: **{len(unclassified_coverage_record_keys & text_classification_record_keys):,}/{len(unclassified_coverage_record_keys):,}**",
        f"- Unresolved category memberships: **{len(unresolved_memberships):,}**",
        "- Active scoring decay: **119th 1.00 / 118th 0.66 / "
        "117th 0.33 / 116th 0.16**",
        "- Legacy comparison retained: **119th 1.00 / 118th 0.80 / "
        "117th 0.64 / 116th 0.512**",
        f"- Relationship edges: **{len(edges):,}**",
        f"- Relationship measures requiring verification: **{len(relationship_ledger):,}**",
        f"- Overall acceptance: **{'PASS' if acceptance['passed'] else 'FAIL'}**",
        "",
        "Every non-pass condition is enumerated in "
        "`complete_coverage_gap_ledger.ndjson`; no missing or unclassified "
        "record is converted to a zero.",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    workbook_path: Path | None = None
    if relationship_complete or args.allow_incomplete_relationships:
        core_frames, category_frames = workbook_frames(
            records,
            edges,
            query_meta,
            query_rows,
            combined_gaps,
            exclusions,
            relationship_ledger,
            measure_metadata,
            membership_review,
            text_relevance,
            registry,
        )
        workbook_path = (
            out_dir / "congress_bills_exhaustive_20260725.xlsx"
        )
        export_workbook(
            workbook_path, core_frames, category_frames, query_meta
        )

    summary = {
        "report": relative(report_path),
        "markdown": relative(markdown_path),
        "workbook": relative(workbook_path) if workbook_path else None,
        "unique_records": len(records),
        "membership_count": len(memberships),
        "historical_recovered": len(archive_keys & record_keys),
        "historical_total": len(archive_keys),
        "acceptance": "PASS" if acceptance["passed"] else "FAIL",
    }
    print(json.dumps(summary, indent=2))
    return 0 if acceptance["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

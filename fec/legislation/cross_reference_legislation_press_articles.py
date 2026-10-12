#!/usr/bin/env python3
"""Cross-reference legislation candidates against the trusted press corpus.

This is deliberately a review-evidence producer, not a semantic filter:

* real articles frozen before 2026-07-25 are user-ratified positive evidence;
* synthetic ``billstatus:`` rows are circular and are never article evidence;
* an exact bill/title reference, nearby action language, and category overlap
  strengthen corroboration;
* no article match is never evidence that a candidate is a false positive;
* article evidence never assigns Sponsor / Original Cosponsor / Cosponsor;
* no result from this module can auto-include or auto-exclude a membership.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import openpyxl

try:
    from lane_file_io import read_ndjson_text
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import read_ndjson_text


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = (
    ROOT / "audits" / "legfix5" / "congress_advanced_final_20260725"
)
DEFAULT_REGISTRY = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
)
DEFAULT_TRUSTED_MANIFEST = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_trusted_press_article_manifest.json"
)

TOKEN_RE = re.compile(r"[a-z0-9]+")
GENERIC_TITLE_PREFIX_RE = re.compile(
    r"^(?:a bill|a resolution|an act|to amend|providing for|expressing the "
    r"sense|recognizing|commemorating)\b",
    re.IGNORECASE,
)
ACTION_PATTERNS = (
    ("original_cosponsorship", re.compile(
        r"\b(?:original(?:ly)?\s+co-?sponsor(?:ed|ing)?|"
        r"joined\s+as\s+an?\s+original\s+co-?sponsor)\b",
        re.IGNORECASE,
    )),
    ("cosponsorship", re.compile(
        r"\b(?:co-?sponsor(?:ed|ing)?|joined\s+(?:the|a)\s+bill)\b",
        re.IGNORECASE,
    )),
    ("sponsorship_or_introduction", re.compile(
        r"\b(?:sponsor(?:ed|ing)?|introduc(?:e|ed|es|ing)|"
        r"reintroduc(?:e|ed|es|ing)|unveil(?:ed|s|ing)?)\b",
        re.IGNORECASE,
    )),
    ("leadership", re.compile(
        r"\b(?:led|co-led|champion(?:ed|ing)?|authored|"
        r"worked\s+to\s+(?:advance|pass))\b",
        re.IGNORECASE,
    )),
    ("support_or_opposition", re.compile(
        r"\b(?:back(?:ed|s|ing)|support(?:ed|s|ing)|"
        r"oppos(?:e|ed|es|ing)|endorse(?:d|s|ing)|"
        r"urge(?:d|s|ing))\b",
        re.IGNORECASE,
    )),
    ("vote_or_advancement", re.compile(
        r"\b(?:vot(?:e|ed|es|ing)|pass(?:ed|es|ing)|"
        r"advance(?:d|s|ing)|approved?|reported\s+(?:the\s+)?bill|"
        r"ordered\s+reported|cleared\s+(?:the\s+)?(?:House|Senate|committee))\b",
        re.IGNORECASE,
    )),
    ("enactment", re.compile(
        r"\b(?:signed\s+into\s+law|became\s+law|enacted)\b",
        re.IGNORECASE,
    )),
)

CITE_PATTERNS = (
    ("hconres", re.compile(r"\bH\s*\.?\s*Con\s*\.?\s*Res\s*\.?\s*(\d+)\b", re.I)),
    ("sconres", re.compile(r"\bS\s*\.?\s*Con\s*\.?\s*Res\s*\.?\s*(\d+)\b", re.I)),
    ("hjres", re.compile(r"\bH\s*\.?\s*J\s*\.?\s*Res\s*\.?\s*(\d+)\b", re.I)),
    ("sjres", re.compile(r"\bS\s*\.?\s*J\s*\.?\s*Res\s*\.?\s*(\d+)\b", re.I)),
    ("hamdt", re.compile(r"\bH\s*\.?\s*Amdt\s*\.?\s*(\d+)\b", re.I)),
    ("samdt", re.compile(r"\bS\s*\.?\s*Amdt\s*\.?\s*(\d+)\b", re.I)),
    ("hres", re.compile(r"\bH\s*\.?\s*Res\s*\.?\s*(\d+)\b", re.I)),
    ("sres", re.compile(r"\bS\s*\.?\s*Res\s*\.?\s*(\d+)\b", re.I)),
    ("hr", re.compile(r"\bH\s*\.?\s*R\s*\.?\s*(\d+)\b", re.I)),
    ("s", re.compile(r"\bS\s*\.\s*(\d+)\b", re.I)),
)

CATEGORY_ALIASES = {
    "telephone consumer protection act": "telephone_consumer_protection_act",
    "telephone consumer protection act issues": "telephone_consumer_protection_act",
    "cu-bank competitive dynamics": "credit_union_bank_competitive_dynamics",
    "credit union-bank competitive dynamics": "credit_union_bank_competitive_dynamics",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument(
        "--trusted-manifest", type=Path, default=DEFAULT_TRUSTED_MANIFEST
    )
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized_phrase(value: Any) -> str:
    return " ".join(TOKEN_RE.findall(normalize(value).lower()))


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


def write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_trusted_source(root: Path, source: dict[str, Any]) -> Path:
    path = (root / source["path"]).resolve()
    expected = source["sha256"].lower()
    observed = sha256_file(path)
    if observed != expected:
        raise RuntimeError(
            f"Trusted press source changed: {path}; "
            f"expected sha256={expected}, observed={observed}. "
            "Do not silently expand the pre-2026-07-25 trusted corpus."
        )
    return path


def parse_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = normalize(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def congress_for_article_date(value: str | None) -> int | None:
    if not value:
        return None
    day = date.fromisoformat(value)
    if date(2019, 1, 3) <= day < date(2021, 1, 3):
        return 116
    if date(2021, 1, 3) <= day < date(2023, 1, 3):
        return 117
    if date(2023, 1, 3) <= day < date(2025, 1, 3):
        return 118
    if date(2025, 1, 3) <= day < date(2027, 1, 3):
        return 119
    return None


def extract_citations(text: str) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for bill_type, pattern in CITE_PATTERNS:
        for match in pattern.finditer(text):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            occupied.append(span)
            citations.append(
                {
                    "compact_key": f"{bill_type}:{int(match.group(1))}",
                    "bill_type": bill_type,
                    "number": int(match.group(1)),
                    "start": span[0],
                    "end": span[1],
                    "matched_text": match.group(0),
                }
            )
    return sorted(citations, key=lambda row: (row["start"], row["end"]))


def clause_bounds(
    text: str,
    start: int,
    end: int,
    protected_spans: Iterable[tuple[int, int]] = (),
) -> tuple[int, int]:
    """Return the sentence/clause containing one exact reference."""
    protected = list(protected_spans)

    def is_clause_boundary(index: int) -> bool:
        return (
            text[index] in ".!?;\n"
            and not any(
                protected_start <= index < protected_end
                for protected_start, protected_end in protected
            )
        )

    left = start
    while left > 0 and not is_clause_boundary(left - 1):
        left -= 1
    right = end
    while right < len(text) and not is_clause_boundary(right):
        right += 1
    return left, right


def localized_action_evidence(
    text: str,
    starts_and_ends: Iterable[tuple[int, int]],
    citations: Iterable[dict[str, Any]] = (),
) -> tuple[list[str], str | None, int | None]:
    action_types: set[str] = set()
    best_context: str | None = None
    maximum_citations_in_clause: int | None = None
    citation_rows = list(citations)
    protected_spans = [
        (int(citation["start"]), int(citation["end"]))
        for citation in citation_rows
    ]
    for start, end in starts_and_ends:
        left, right = clause_bounds(
            text,
            start,
            end,
            protected_spans,
        )
        context = normalize(text[left:right])
        local_types = {
            label
            for label, pattern in ACTION_PATTERNS
            if pattern.search(context)
        }
        if local_types:
            action_types.update(local_types)
            if best_context is None or len(context) < len(best_context):
                best_context = context
            local_citation_count = sum(
                citation["start"] < right and left < citation["end"]
                for citation in citation_rows
            )
            maximum_citations_in_clause = max(
                maximum_citations_in_clause or 0,
                local_citation_count,
            )
    return (
        sorted(action_types),
        best_context,
        maximum_citations_in_clause,
    )


def action_evidence(
    text: str, starts_and_ends: Iterable[tuple[int, int]], radius: int = 320
) -> tuple[list[str], str | None]:
    """Compatibility wrapper; action is now limited to the same clause."""
    del radius
    action_types, context, _citation_count = localized_action_evidence(
        text, starts_and_ends
    )
    return action_types, context


def title_match_phrase(title: Any) -> str | None:
    phrase = normalized_phrase(title)
    tokens = [
        token
        for token in phrase.split()
        if len(token) >= 3 and token not in {"the", "and", "for", "act", "of"}
    ]
    if (
        len(phrase) < 16
        or len(tokens) < 3
        or GENERIC_TITLE_PREFIX_RE.search(normalize(title))
    ):
        return None
    return phrase


def title_reference_span(text: str, title: Any) -> tuple[int, int] | None:
    phrase = title_match_phrase(title)
    if not phrase:
        return None
    tokens = phrase.split()
    flexible = re.compile(
        r"\b" + r"[\W_]+".join(re.escape(token) for token in tokens) + r"\b",
        re.IGNORECASE,
    )
    match = flexible.search(text)
    return match.span() if match else None


def canonical_parts(canonical_key: str) -> tuple[int, str, int]:
    congress, bill_type, number = canonical_key.split(":")
    return int(congress), bill_type, int(number)


def reverse_citation_coverage_audit(
    articles: Iterable[dict[str, Any]],
    discovery_keys: set[str],
    required_congresses: Iterable[int],
) -> dict[str, list[dict[str, Any]]]:
    """Classify every trusted-article bill-citation occurrence against discovery.

    Article date is the only Congress qualifier available in the frozen press
    corpus. A citation is therefore unambiguous for this coverage audit only
    when the parsed article date maps to one of the required Congresses. Every
    occurrence is retained, including repeated citations within one article.
    This function produces coverage-review evidence only; it never creates a
    candidate, membership decision, exclusion, or sponsorship role.
    """
    required = {int(value) for value in required_congresses}
    audit: list[dict[str, Any]] = []
    absent: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for article in articles:
        citations = list(article.get("citations", []))
        for occurrence_index, citation in enumerate(citations, start=1):
            article_congress = article.get("article_congress")
            ambiguity_reason: str | None = None
            canonical_key: str | None = None
            if article_congress is None:
                ambiguity_reason = (
                    "article_date_missing_unparseable_or_outside_"
                    "supported_congress_window"
                )
            else:
                try:
                    resolved_congress = int(article_congress)
                except (TypeError, ValueError):
                    ambiguity_reason = "article_congress_is_not_an_integer"
                else:
                    if resolved_congress not in required:
                        ambiguity_reason = (
                            "article_date_maps_outside_required_congress_scope"
                        )
                    else:
                        canonical_key = (
                            f"{resolved_congress}:{citation['bill_type']}:"
                            f"{int(citation['number'])}"
                        )

            action_types, action_context, citations_in_action_clause = (
                localized_action_evidence(
                    article.get("searchable_text", ""),
                    [(int(citation["start"]), int(citation["end"]))],
                    citations,
                )
            )
            if canonical_key is None:
                resolution = "ambiguous_or_unresolved"
                union_status = "not_testable_without_congress_qualification"
            elif canonical_key in discovery_keys:
                resolution = "unambiguous_congress_qualified_present"
                union_status = "present_in_discovery_union"
            else:
                union_status = "absent_from_discovery_union"
                if not action_types:
                    resolution = "ambiguous_or_unresolved"
                    ambiguity_reason = (
                        "citation_absent_but_no_local_legislative_action"
                    )
                elif citations_in_action_clause != 1:
                    resolution = "ambiguous_or_unresolved"
                    ambiguity_reason = (
                        "citation_absent_in_multi_measure_action_clause"
                    )
                else:
                    resolution = "unambiguous_congress_qualified_absent"

            row = {
                "schema": "acu-trusted-article-reverse-citation-audit-v1",
                "citation_audit_id": (
                    f"{article['article_id']}:citation:"
                    f"{occurrence_index:03d}:{int(citation['start'])}"
                ),
                "citation_occurrence_index": occurrence_index,
                "citation_start": int(citation["start"]),
                "citation_end": int(citation["end"]),
                "matched_citation_text": citation["matched_text"],
                "compact_cite": citation["compact_key"],
                "bill_type": citation["bill_type"],
                "bill_number": int(citation["number"]),
                "canonical_key": canonical_key,
                "reference_resolution": resolution,
                "congress_qualification_basis": (
                    "trusted_article_date_maps_to_required_congress"
                    if canonical_key
                    else None
                ),
                "ambiguity_reason": ambiguity_reason,
                "discovery_union_status": union_status,
                "absent_from_discovery_union": (
                    union_status == "absent_from_discovery_union"
                ),
                "targeted_congress_gov_query": (
                    f'(congressId:{canonical_key.split(":", 1)[0]} AND '
                    f'cite:"{citation["bill_type"]}{int(citation["number"])}")'
                    if resolution == "unambiguous_congress_qualified_absent"
                    else None
                ),
                "article_id": article["article_id"],
                "trusted_before": article["trusted_before"],
                "trusted_corpus_provenance": (
                    "frozen_pre_2026_07_25_user_ratified_press_corpus"
                ),
                "synthetic_billstatus_source": False,
                "article_member": article.get("member"),
                "article_bioguide": article.get("bioguide"),
                "article_date": article.get("date"),
                "article_congress": article_congress,
                "article_title": article.get("title"),
                "article_url": article.get("url"),
                "canonical_article_url": article.get(
                    "canonical_article_url",
                    str(article.get("url") or "").split("#", 1)[0],
                ),
                "article_categories_raw": article.get("categories_raw"),
                "article_category_ids": article.get("category_ids", []),
                "article_relevance": article.get("relevance"),
                "body_available": bool(article.get("body_available")),
                "action_types_near_reference": action_types,
                "action_context_excerpt": action_context,
                "action_localization": "same_sentence_or_semicolon_clause",
                "citations_in_action_clause": citations_in_action_clause,
                "review_required": True,
                "article_evidence_is_corroborative_not_dispositive": True,
                "article_absence_is_never_exclusion_evidence": True,
                "automated_candidate_creation": None,
                "automated_membership_decision": None,
                "automated_exclusion_decision": None,
                "sponsorship_role_assigned": None,
            }
            audit.append(row)
            if resolution == "unambiguous_congress_qualified_absent":
                absent.append(row)
            elif resolution == "ambiguous_or_unresolved":
                unresolved.append(row)

    return {
        "audit": audit,
        "absent_unambiguous": absent,
        "ambiguous_unresolved": unresolved,
    }


def category_ids(
    raw_categories: Any, category_label_to_id: dict[str, str]
) -> list[str]:
    result: list[str] = []
    for label in normalize(raw_categories).split(";"):
        key = normalized_phrase(label)
        if not key or key == "general credit union":
            continue
        category_id = (
            CATEGORY_ALIASES.get(key)
            or category_label_to_id.get(key)
        )
        if category_id and category_id not in result:
            result.append(category_id)
    return result


def load_text_caches(
    manifest: dict[str, Any],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    texts: dict[str, str] = {}
    verified: list[dict[str, Any]] = []
    for source in manifest["body_caches"]:
        path = verify_trusted_source(ROOT, source)
        rows = read_ndjson(path)
        usable = 0
        for row in rows:
            url = normalize(row.get("url"))
            text = normalize(row.get("text"))
            if url and text:
                texts[url] = text
                usable += 1
        verified.append(
            {
                "path": source["path"],
                "sha256": source["sha256"],
                "row_count": len(rows),
                "usable_text_count": usable,
            }
        )
    return texts, verified


def load_trusted_articles(
    trusted_manifest: dict[str, Any],
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    workbook_spec = trusted_manifest["article_workbook"]
    workbook_path = verify_trusted_source(ROOT, workbook_spec)
    texts, verified_caches = load_text_caches(trusted_manifest)
    label_to_id = {
        normalized_phrase(category["label"]): category["id"]
        for category in registry["categories"]
    }
    workbook = openpyxl.load_workbook(
        workbook_path, read_only=True, data_only=True
    )
    try:
        sheet = workbook[workbook_spec["sheet"]]
        iterator = sheet.iter_rows(values_only=True)
        headers = next(iterator)
        header = {normalize(value): index for index, value in enumerate(headers)}
        articles: list[dict[str, Any]] = []
        synthetic = 0
        for row_number, row in enumerate(iterator, 2):
            def value(name: str) -> Any:
                index = header.get(name)
                return row[index] if index is not None and index < len(row) else None

            url = normalize(value("url"))
            if url.startswith("billstatus:"):
                synthetic += 1
                continue
            if not url.startswith(("http://", "https://")):
                raise RuntimeError(
                    f"Trusted article row {row_number} has non-web URL: {url!r}"
                )
            title = normalize(value("title"))
            body = texts.get(url, "")
            searchable_text = normalize(f"{title} {body}")
            normalized_text = normalized_phrase(searchable_text)
            articles.append(
                {
                    "article_id": f"trusted:{row_number}",
                    "trusted_before": trusted_manifest["trust_cutoff_date"],
                    "member": normalize(value("member")),
                    "bioguide": normalize(value("bioguide")),
                    "date": parse_date(value("date")),
                    "article_congress": congress_for_article_date(
                        parse_date(value("date"))
                    ),
                    "title": title,
                    "url": url,
                    "canonical_article_url": url.split("#", 1)[0],
                    "categories_raw": normalize(value("categories")),
                    "category_ids": category_ids(
                        value("categories"), label_to_id
                    ),
                    "relevance": normalize(value("relevance")),
                    "body_available": bool(body),
                    "searchable_text": searchable_text,
                    "normalized_text": normalized_text,
                    "citations": extract_citations(searchable_text),
                }
            )
    finally:
        workbook.close()
    if len(articles) != workbook_spec["expected_real_article_rows"]:
        raise RuntimeError(
            "Trusted real-article count changed: "
            f"expected {workbook_spec['expected_real_article_rows']}, "
            f"observed {len(articles)}"
        )
    if synthetic != workbook_spec[
        "expected_synthetic_billstatus_rows_excluded"
    ]:
        raise RuntimeError(
            "Circular billstatus-row count changed: "
            f"expected {workbook_spec['expected_synthetic_billstatus_rows_excluded']}, "
            f"observed {synthetic}"
        )
    return articles, {
        "workbook": {
            "path": workbook_spec["path"],
            "sheet": workbook_spec["sheet"],
            "sha256": workbook_spec["sha256"],
            "real_article_rows": len(articles),
            "synthetic_billstatus_rows_excluded": synthetic,
        },
        "body_caches": verified_caches,
    }


def reference_positions(
    article: dict[str, Any], record: dict[str, Any]
) -> list[dict[str, Any]]:
    _congress, bill_type, number = canonical_parts(record["canonical_key"])
    compact_key = f"{bill_type}:{number}"
    references = [
        {
            "start": citation["start"],
            "end": citation["end"],
            "basis": "exact_bill_citation",
        }
        for citation in article["citations"]
        if citation["compact_key"] == compact_key
    ]
    title_span = title_reference_span(
        article["searchable_text"],
        record.get("title") or record.get("purpose"),
    )
    if title_span:
        references.append(
            {
                "start": title_span[0],
                "end": title_span[1],
                "basis": "exact_normalized_title",
            }
        )
    return references


def match_strength(
    *,
    action_types: list[str],
    category_overlap: bool,
    congress_compatible: bool,
    exact_bill_citation: bool = False,
    citations_in_action_clause: int | None = None,
) -> tuple[str, str]:
    substantive_actions = set(action_types) - {"support_or_opposition"}
    if (
        substantive_actions
        and category_overlap
        and congress_compatible
        and exact_bill_citation
        and citations_in_action_clause == 1
    ):
        return "strong", "trusted_article_action_and_category_corroboration"
    if (
        exact_bill_citation
        and category_overlap
        and congress_compatible
        and citations_in_action_clause
        and citations_in_action_clause > 1
    ):
        return "review", "multi_measure_action_clause_requires_review"
    if exact_bill_citation and category_overlap and congress_compatible:
        return "moderate", "trusted_article_category_corroboration_without_action"
    if not exact_bill_citation:
        return "review", "title_family_reference_requires_bill_identity_review"
    if not congress_compatible:
        return "review", "bill_citation_congress_mismatch_requires_review"
    if action_types and not category_overlap:
        return "review", "trusted_article_action_category_mismatch_requires_review"
    if category_overlap:
        return "review", "trusted_article_category_match_congress_ambiguity"
    return "review", "trusted_article_reference_without_category_overlap"


def build_match(
    article: dict[str, Any],
    record: dict[str, Any],
    record_query_ids: set[str],
    cite_congresses: dict[str, set[int]],
) -> dict[str, Any] | None:
    references = reference_positions(article, record)
    if not references:
        return None
    congress, bill_type, number = canonical_parts(record["canonical_key"])
    compact_key = f"{bill_type}:{number}"
    article_congress = article["article_congress"]
    unique_cite = len(cite_congresses.get(compact_key, set())) == 1
    congress_compatible = article_congress == congress
    if article_congress == congress:
        congress_basis = "article_date_matches_candidate_congress"
    elif article_congress is None:
        congress_basis = "article_date_unknown_and_citation_cross_congress_ambiguous"
    else:
        congress_basis = "article_date_points_to_different_congress"
    citation_references = [
        reference
        for reference in references
        if reference["basis"] == "exact_bill_citation"
    ]
    action_types, action_context, citations_in_action_clause = (
        localized_action_evidence(
            article["searchable_text"],
            [
                (reference["start"], reference["end"])
                for reference in citation_references
            ],
            article["citations"],
        )
    )
    overlap = sorted(set(article["category_ids"]) & record_query_ids)
    strength, signal = match_strength(
        action_types=action_types,
        category_overlap=bool(overlap),
        congress_compatible=congress_compatible,
        exact_bill_citation=bool(citation_references),
        citations_in_action_clause=citations_in_action_clause,
    )
    return {
        "schema": "acu-legislation-trusted-article-match-v1",
        "canonical_key": record["canonical_key"],
        "display_number": record.get("display_number"),
        "measure_title": record.get("title") or record.get("purpose"),
        "article_id": article["article_id"],
        "trusted_before": article["trusted_before"],
        "article_member": article["member"],
        "article_bioguide": article["bioguide"],
        "article_date": article["date"],
        "article_title": article["title"],
        "article_url": article["url"],
        "canonical_article_url": article.get(
            "canonical_article_url",
            article["url"].split("#", 1)[0],
        ),
        "article_categories_raw": article["categories_raw"],
        "article_category_ids": article["category_ids"],
        "article_relevance": article["relevance"],
        "body_available": article["body_available"],
        "reference_basis": sorted(
            {reference["basis"] for reference in references}
        ),
        "action_types_near_reference": action_types,
        "action_context_excerpt": action_context,
        "action_localization": "same_sentence_or_semicolon_clause",
        "citations_in_action_clause": citations_in_action_clause,
        "candidate_query_ids": sorted(record_query_ids),
        "category_overlap_query_ids": overlap,
        "congress_compatible": congress_compatible,
        "congress_compatibility_basis": congress_basis,
        "bill_number_unique_in_discovery_corpus": unique_cite,
        "bill_number_uniqueness_never_overrides_congress_mismatch": True,
        "evidence_strength": strength,
        "review_signal": signal,
        "article_evidence_is_corroborative_not_dispositive": True,
        "article_evidence_does_not_assign_legislative_role": True,
        "article_absence_is_never_exclusion_evidence": True,
        "automated_membership_decision": None,
    }


def main() -> int:
    args = parse_args()
    run_dir = args.run.resolve()
    out_dir = (
        args.out.resolve()
        if args.out
        else run_dir / "article_cross_reference"
    )
    discovery_manifest = read_json(run_dir / "manifest.json")
    if discovery_manifest.get("status") != "complete":
        raise RuntimeError("Advanced Search discovery run is not complete")
    registry = read_json(args.registry.resolve())
    trusted_manifest = read_json(args.trusted_manifest.resolve())
    contracts = trusted_manifest["contracts"]
    required_true = (
        "article_presence_is_corroborative_not_dispositive",
        "article_absence_is_never_exclusion_evidence",
        "article_evidence_never_assigns_sponsorship_role",
        "article_evidence_never_auto_includes_membership",
        "article_evidence_never_auto_excludes_membership",
        "synthetic_billstatus_rows_are_circular_and_excluded",
    )
    if not all(contracts.get(key) is True for key in required_true):
        raise RuntimeError("Trusted article safety contract is incomplete")

    records = read_ndjson(
        run_dir / discovery_manifest["union"]["records_file"]
    )
    memberships = read_ndjson(
        run_dir / discovery_manifest["union"]["memberships_file"]
    )
    record_by_key = {row["canonical_key"]: row for row in records}
    record_query_ids: dict[str, set[str]] = defaultdict(set)
    for membership in memberships:
        record_query_ids[membership["canonical_key"]].add(
            membership["query_id"]
        )

    articles, source_audit = load_trusted_articles(
        trusted_manifest, registry
    )
    required_congresses = registry["collection_contract"]["congresses"]
    reverse_citation_audit = reverse_citation_coverage_audit(
        articles,
        set(record_by_key),
        required_congresses,
    )
    cite_congresses: dict[str, set[int]] = defaultdict(set)
    for record in records:
        congress, bill_type, number = canonical_parts(record["canonical_key"])
        cite_congresses[f"{bill_type}:{number}"].add(congress)

    citation_article_index: dict[str, set[int]] = defaultdict(set)
    token_article_index: dict[str, set[int]] = defaultdict(set)
    for index, article in enumerate(articles):
        for citation in article["citations"]:
            citation_article_index[citation["compact_key"]].add(index)
        for token in set(article["normalized_text"].split()):
            if len(token) >= 5:
                token_article_index[token].add(index)

    measure_matches: list[dict[str, Any]] = []
    matches_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        _congress, bill_type, number = canonical_parts(
            record["canonical_key"]
        )
        candidate_indexes = set(
            citation_article_index.get(f"{bill_type}:{number}", set())
        )
        title_phrase = title_match_phrase(
            record.get("title") or record.get("purpose")
        )
        if title_phrase:
            title_tokens = [
                token
                for token in title_phrase.split()
                if len(token) >= 5
            ]
            if title_tokens:
                rarest = min(
                    title_tokens,
                    key=lambda token: len(token_article_index.get(token, set())),
                )
                candidate_indexes.update(
                    token_article_index.get(rarest, set())
                )
        for article_index in sorted(candidate_indexes):
            match = build_match(
                articles[article_index],
                record,
                record_query_ids.get(record["canonical_key"], set()),
                cite_congresses,
            )
            if match:
                measure_matches.append(match)
                matches_by_key[record["canonical_key"]].append(match)

    strength_order = {"strong": 0, "moderate": 1, "review": 2}
    measure_matches.sort(
        key=lambda row: (
            row["canonical_key"],
            strength_order[row["evidence_strength"]],
            row["article_date"] or "",
            row["article_url"],
        )
    )
    membership_evidence: list[dict[str, Any]] = []
    category_by_id = {
        category["id"]: category for category in registry["categories"]
    }
    for membership in memberships:
        canonical_key = membership["canonical_key"]
        query_id = membership["query_id"]
        evidence_rows = matches_by_key.get(canonical_key, [])
        unique_by_url: dict[str, dict[str, Any]] = {}
        for row in evidence_rows:
            article_url = row["canonical_article_url"]
            prior = unique_by_url.get(article_url)
            if prior is None or strength_order[row["evidence_strength"]] < (
                strength_order[prior["evidence_strength"]]
            ):
                unique_by_url[article_url] = row
        matches = sorted(
            unique_by_url.values(),
            key=lambda row: (
                strength_order[row["evidence_strength"]],
                row["article_date"] or "",
                row["canonical_article_url"],
            ),
        )
        same_category = [
            row
            for row in matches
            if query_id in row["category_overlap_query_ids"]
        ]
        action_matches = [
            row for row in matches if row["action_types_near_reference"]
        ]
        strong_same_category = [
            row
            for row in same_category
            if row["evidence_strength"] == "strong"
        ]
        moderate_same_category = [
            row
            for row in same_category
            if row["evidence_strength"] == "moderate"
        ]
        if strong_same_category:
            signal = "trusted_article_action_and_category_corroboration"
        elif moderate_same_category:
            signal = "trusted_article_category_corroboration_without_action"
        elif action_matches:
            signal = "trusted_article_action_or_identity_requires_review"
        elif matches:
            signal = (
                "trusted_article_reference_requires_identity_or_congress_review"
            )
        else:
            signal = "no_trusted_article_match_not_dispositive"
        suggested = sorted(
            {
                category_id
                for row in matches
                for category_id in row["article_category_ids"]
                if category_id in category_by_id
            }
        )
        membership_evidence.append(
            {
                "schema": "acu-legislation-trusted-article-membership-v1",
                "canonical_key": canonical_key,
                "query_id": query_id,
                "category_label": (
                    category_by_id.get(query_id, {}).get("label")
                ),
                "trusted_article_match_count": len(matches),
                "trusted_article_evidence_row_count": len(evidence_rows),
                "trusted_article_action_match_count": len(action_matches),
                "trusted_article_category_overlap_count": len(same_category),
                "trusted_article_strong_corroboration_count": len(
                    strong_same_category
                ),
                "trusted_article_review_signal": signal,
                "trusted_article_suggested_category_ids": suggested,
                "matched_article_ids": [
                    row["article_id"] for row in matches
                ],
                "matched_article_urls": [
                    row["canonical_article_url"] for row in matches
                ],
                "article_absence_is_never_exclusion_evidence": True,
                "article_evidence_is_corroborative_not_dispositive": True,
                "automated_membership_decision": None,
            }
        )
    membership_evidence.sort(
        key=lambda row: (row["query_id"], row["canonical_key"])
    )

    measure_file = out_dir / "trusted_article_measure_matches.ndjson"
    membership_file = (
        out_dir / "trusted_article_membership_evidence.ndjson"
    )
    reverse_audit_file = (
        out_dir / "trusted_article_reverse_citation_audit.ndjson"
    )
    absent_reference_file = (
        out_dir
        / "trusted_article_absent_unambiguous_citations.ndjson"
    )
    unresolved_reference_file = (
        out_dir
        / "trusted_article_ambiguous_unresolved_citations.ndjson"
    )
    write_ndjson(measure_file, measure_matches)
    write_ndjson(membership_file, membership_evidence)
    write_ndjson(reverse_audit_file, reverse_citation_audit["audit"])
    write_ndjson(
        absent_reference_file,
        reverse_citation_audit["absent_unambiguous"],
    )
    write_ndjson(
        unresolved_reference_file,
        reverse_citation_audit["ambiguous_unresolved"],
    )
    strength_counts = Counter(
        row["evidence_strength"] for row in measure_matches
    )
    signal_counts = Counter(
        row["trusted_article_review_signal"]
        for row in membership_evidence
    )
    report = {
        "schema": "acu-legislation-article-cross-reference-v3",
        "status": "complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trusted_press_manifest": str(
            args.trusted_manifest.resolve().relative_to(ROOT)
        ).replace("\\", "/"),
        "trust_cutoff_date": trusted_manifest["trust_cutoff_date"],
        "source_audit": source_audit,
        "discovery_record_count": len(records),
        "discovery_membership_count": len(memberships),
        "measure_match_count": len(measure_matches),
        "matched_measure_count": len(matches_by_key),
        "measure_match_strength_counts": dict(sorted(strength_counts.items())),
        "membership_review_signal_counts": dict(sorted(signal_counts.items())),
        "trusted_article_citation_occurrence_count": len(
            reverse_citation_audit["audit"]
        ),
        "trusted_article_citation_present_occurrence_count": sum(
            row["discovery_union_status"] == "present_in_discovery_union"
            for row in reverse_citation_audit["audit"]
        ),
        "trusted_article_absent_unambiguous_occurrence_count": len(
            reverse_citation_audit["absent_unambiguous"]
        ),
        "trusted_article_absent_unambiguous_unique_measure_count": len(
            {
                row["canonical_key"]
                for row in reverse_citation_audit["absent_unambiguous"]
            }
        ),
        "trusted_article_ambiguous_unresolved_occurrence_count": len(
            reverse_citation_audit["ambiguous_unresolved"]
        ),
        "trusted_article_congress_qualified_absent_occurrence_count": sum(
            bool(row.get("canonical_key"))
            and row.get("absent_from_discovery_union") is True
            for row in reverse_citation_audit["audit"]
        ),
        "trusted_article_congress_qualified_absent_unique_measure_count": len(
            {
                row["canonical_key"]
                for row in reverse_citation_audit["audit"]
                if row.get("canonical_key")
                and row.get("absent_from_discovery_union") is True
            }
        ),
        "reverse_citation_audit_is_lossless_by_occurrence": (
            len(reverse_citation_audit["audit"])
            == sum(len(article.get("citations", [])) for article in articles)
        ),
        "reverse_citation_audit_auto_created_candidates": 0,
        "reverse_citation_audit_auto_inclusions": 0,
        "reverse_citation_audit_auto_exclusions": 0,
        "reverse_citation_audit_sponsorship_roles_assigned": 0,
        "synthetic_billstatus_rows_used_as_article_evidence": 0,
        "article_based_sponsorship_roles_assigned": 0,
        "article_based_automated_inclusions": 0,
        "article_based_automated_exclusions": 0,
        "article_absence_is_never_exclusion_evidence": True,
        "article_evidence_is_corroborative_not_dispositive": True,
        "measure_matches_file": str(
            measure_file.relative_to(out_dir)
        ).replace("\\", "/"),
        "membership_evidence_file": str(
            membership_file.relative_to(out_dir)
        ).replace("\\", "/"),
        "reverse_citation_audit_file": str(
            reverse_audit_file.relative_to(out_dir)
        ).replace("\\", "/"),
        "reverse_citation_audit_sha256": sha256_file(reverse_audit_file),
        "absent_unambiguous_references_file": str(
            absent_reference_file.relative_to(out_dir)
        ).replace("\\", "/"),
        "absent_unambiguous_references_sha256": sha256_file(
            absent_reference_file
        ),
        "ambiguous_unresolved_references_file": str(
            unresolved_reference_file.relative_to(out_dir)
        ).replace("\\", "/"),
        "ambiguous_unresolved_references_sha256": sha256_file(
            unresolved_reference_file
        ),
    }
    write_json(out_dir / "manifest.json", report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

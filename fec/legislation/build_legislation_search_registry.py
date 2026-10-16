#!/usr/bin/env python3
"""Build the auditable Congress.gov Advanced Search registry from the ACU outline.

The source document is authoritative.  Paragraph ranges below identify the
searchable issue sections in that document; every retained identifier records
its source paragraph so edits to the outline can be reviewed rather than
silently changing collection behavior.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from docx import Document


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTLINE = (
    ROOT / "source_data" / "legislation" / "Moneyball_ACU_Search_Outline.docx"
)
DEFAULT_OUTPUT = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
)

NUMBER_RE = re.compile(
    r"\b("
    r"H\.R\.|"
    r"H\.J\.\s*Res\.|S\.J\.\s*Res\.|"
    r"H\.\s*Con\.\s*Res\.|S\.\s*Con\.\s*Res\.|"
    r"H\.\s*Res\.|S\.\s*Res\.|"
    r"S\."
    r")\s*(\d+)\b",
    re.IGNORECASE,
)

CATEGORY_SPECS = (
    ("examination_supervision", "Examination & Supervision", 5, 31, True),
    (
        "credit_union_capital_requirements",
        "Credit Union Capital Requirements",
        33,
        40,
        True,
    ),
    (
        "ncua_budget_share_insurance",
        "NCUA Budget & Share Insurance Fund",
        42,
        52,
        True,
    ),
    ("interchange", "Interchange", 55, 64, True),
    ("payments", "Payments", 66, 72, True),
    (
        "technology_emerging_issues",
        "Technology & Emerging Issues",
        74,
        90,
        True,
    ),
    ("fraud_prevention", "Fraud Prevention", 93, 105, True),
    (
        "data_privacy_cybersecurity",
        "Data Privacy & Cybersecurity",
        107,
        119,
        True,
    ),
    (
        "charter_modernization_governance",
        "Charter Modernization & Governance",
        122,
        132,
        True,
    ),
    (
        "defense_credit_union_issues",
        "Defense Credit Union Issues",
        134,
        141,
        True,
    ),
    ("field_of_membership", "Field of Membership", 143, 154, True),
    ("credit_union_tax_status", "Credit Union Tax Status", 157, 168, True),
    ("cdfi_fund", "CDFI Fund", 170, 180, True),
    (
        "credit_union_bank_competitive_dynamics",
        "Credit Union-Bank Competitive Dynamics",
        183,
        190,
        False,
    ),
    ("cfpb_issues_structural_reform", "CFPB Issues & Structural Reform", 193, 212, True),
    (
        "credit_card_consumer_fee_mandates",
        "Credit Card & Consumer-Fee Mandates",
        214,
        226,
        True,
    ),
    (
        "telephone_consumer_protection_act",
        "Telephone Consumer Protection Act Issues",
        228,
        232,
        True,
    ),
    ("member_business_lending", "Member Business Lending", 235, 253, True),
    (
        "mortgage_lending_housing",
        "Mortgage Lending & Housing",
        255,
        272,
        True,
    ),
)

# Titles are recall terms, not category judgments.  Known 119th-Congress bill
# numbers remain separate, Congress-qualified acceptance checks.
TITLE_ALIASES = {
    "examination_supervision": (
        "FIRM Act",
        "TAILOR Act",
        "FAIR Exams Act",
        "HUMPS Act",
        "SMART Act",
        "STREAMLINE Act",
        "Financial Reporting Threshold Modernization Act",
        "Community Bank Regulatory Tailoring Act",
    ),
    "ncua_budget_share_insurance": (
        "NCUA Central Liquidity Facility Enhancements Act",
    ),
    "interchange": ("Credit Card Competition Act",),
    "technology_emerging_issues": (
        "Anti-CBDC Surveillance State Act",
        "Digital Asset Market Clarity Act",
        "CLARITY Act",
        "GENIUS Act",
        "STABLE Act",
        "SECURE Notarization Act",
    ),
    "fraud_prevention": (
        "Task Force for Recognizing and Averting Payment Scams Act",
        "TRAPS Act",
    ),
    "data_privacy_cybersecurity": ("Homebuyers Privacy Protection Act",),
    "charter_modernization_governance": (
        "Credit Union Board Modernization Act",
        "American Access to Banking Act",
        "MORE Opportunities for Homeownership Act",
    ),
    "credit_union_tax_status": ("One Big Beautiful Bill Act",),
    "cdfi_fund": (
        "CDFI Fund Transparency Act",
        "AFFORD Act",
        "CDFI Bond Guarantee Program Improvement Act",
    ),
    "cfpb_issues_structural_reform": (
        "Transparency in CFPB Cost-Benefit Analysis Act",
        "Bureau of Consumer Financial Protection Commission Act",
        "Rectifying UDAAP Act",
        "TABS Act",
        "Taking Account of Bureaucrats' Spending Act",
        "1071 Repeal to Protect Small Business Lending Act",
        "Business of Insurance Regulatory Reform Act",
    ),
    "credit_card_consumer_fee_mandates": (
        "10 Percent Credit Card Interest Rate Cap Act",
        "Credit Card Fairness Act",
    ),
    "member_business_lending": (
        "Veterans Member Business Loan Act",
        "Increasing Credit Union Lending for Business Growth Act",
        "Expanding Access to Lending Options Act",
        "Protecting Access to Credit for Small Businesses Act",
    ),
    "mortgage_lending_housing": (
        "HELPER Act",
        "Home of Your Own Act",
        "SUPPLY Act",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_identifier(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^\s*[ivxlcdm]+\.\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*~\s*", "", value)
    return re.sub(r"\s+", " ", value).strip()


def split_identifier(value: str) -> list[str]:
    """Split outline alternatives while preserving proximity operators."""
    value = clean_identifier(value)
    return [part.strip() for part in re.split(r"\s+/\s+", value) if part.strip()]


def compact_cite(prefix: str, number: str) -> str:
    return re.sub(r"[^a-z]", "", prefix.lower()) + number


def display_cite(prefix: str, number: str) -> str:
    normalized = re.sub(r"\s+", "", prefix)
    normalized = {
        "H.R.": "H.R.",
        "S.": "S.",
        "H.J.Res.": "H.J.Res.",
        "S.J.Res.": "S.J.Res.",
        "H.Con.Res.": "H.Con.Res.",
        "S.Con.Res.": "S.Con.Res.",
        "H.Res.": "H.Res.",
        "S.Res.": "S.Res.",
    }.get(normalized, normalized)
    return f"{normalized}{number}"


def acu_position(source_text: str) -> str | None:
    for position in ("OPPOSE", "SUPPORT", "MONITOR"):
        if re.search(rf"\b{position}\b", source_text, flags=re.IGNORECASE):
            return position
    return None


def short_act_alias(title: str) -> bool:
    tokens = re.findall(r"[a-z0-9]+", title.lower())
    distinctive = {
        token
        for token in tokens
        if token
        not in {
            "a",
            "an",
            "the",
            "this",
            "that",
            "of",
            "and",
            "or",
            "for",
            "to",
            "in",
            "on",
            "act",
        }
    }
    return "act" in tokens and len(distinctive) < 3


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def build_registry(outline_path: Path) -> dict[str, Any]:
    outline_path = outline_path.resolve()
    document = Document(outline_path)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]

    categories: list[dict[str, Any]] = []
    title_alias_probes: list[dict[str, Any]] = []
    for category_id, label, start, end, scoring_category in CATEGORY_SPECS:
        identifiers: list[dict[str, Any]] = []
        known_bills: list[dict[str, Any]] = []

        for paragraph_index in range(start, end + 1):
            source_text = paragraphs[paragraph_index].strip()
            if not source_text:
                continue
            bill_matches = list(NUMBER_RE.finditer(source_text))
            if bill_matches:
                for match in bill_matches:
                    known_bills.append(
                        {
                            "congress": 119,
                            "number": display_cite(match.group(1), match.group(2)),
                            "compact_cite": compact_cite(
                                match.group(1), match.group(2)
                            ),
                            "acu_position": acu_position(source_text),
                            "source_paragraph": paragraph_index,
                            "source_text": source_text,
                        }
                    )
                continue

            for identifier in split_identifier(source_text):
                identifiers.append(
                    {
                        "text": identifier,
                        "source_paragraph": paragraph_index,
                        "source_text": source_text,
                    }
                )

        title_aliases = list(TITLE_ALIASES.get(category_id, ()))
        query_terms: list[dict[str, Any]] = []
        seen: set[str] = set()

        for identifier in identifiers:
            text = identifier["text"]
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                query_terms.append(
                    {
                        "kind": "outline_identifier",
                        "query": text,
                        "source_paragraph": identifier["source_paragraph"],
                    }
                )
        for title in title_aliases:
            query = '"' + title.replace('"', r"\"") + '"'
            ambiguous_short_act = short_act_alias(title)
            title_alias_probes.append(
                {
                    "id": (
                        f"title_alias_probe_{category_id}_{slug(title)}"
                    ),
                    "label": f"{label} — title alias: {title}",
                    "scoring_category": False,
                    "record_kind": "coverage_probe",
                    "required_for_acceptance": True,
                    "parent_category_id": category_id,
                    "title_alias": title,
                    "ambiguous_short_act_alias": ambiguous_short_act,
                    "membership_non_dispositive": True,
                    "query_terms": [
                        {
                            "kind": "ambiguous_title_alias_probe"
                            if ambiguous_short_act
                            else "title_alias_probe",
                            "query": query,
                        }
                    ],
                    "advanced_search_query": f"({query})",
                    "purpose": (
                        "Lossless exact-title recall and per-alias provenance. "
                        "Results require Congress-qualified official-title or "
                        "operative-provision review before category promotion."
                    ),
                }
            )
            if ambiguous_short_act:
                # Keep the collision family visible in its own non-scoring
                # probe; do not let it assert category membership.
                continue
            key = query.casefold()
            if key not in seen:
                seen.add(key)
                query_terms.append({"kind": "known_title", "query": query})
        for bill in known_bills:
            query = (
                f'(congressId:{bill["congress"]} AND '
                f'cite:"{bill["compact_cite"]}")'
            )
            key = query.casefold()
            if key not in seen:
                seen.add(key)
                query_terms.append(
                    {
                        "kind": "known_bill_cite",
                        "query": query,
                        "source_paragraph": bill["source_paragraph"],
                    }
                )

        categories.append(
            {
                "id": category_id,
                "label": label,
                "scoring_category": scoring_category,
                "outline_paragraph_range": [start, end],
                "identifiers": identifiers,
                "title_aliases": title_aliases,
                "known_bills": known_bills,
                "query_terms": query_terms,
                "advanced_search_query": " OR ".join(
                    f"({term['query']})" for term in query_terms
                ),
            }
        )

    return {
        "schema": "acu-congress-advanced-search-registry-v1",
        "source": {
            "file": outline_path.name,
            "repository_path": str(outline_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(outline_path),
            "paragraph_count": len(paragraphs),
            "title": paragraphs[0],
            "scope_note": paragraphs[1],
            "instructions": paragraphs[2],
        },
        "collection_contract": {
            "primary_instrument": "Congress.gov Advanced Search - Legislation",
            "advanced_search_url": (
                "https://www.congress.gov/index.php/advanced-search/legislation"
            ),
            "search_scope": "All fields including Bill Text",
            "word_variants": True,
            "case_sensitive": False,
            "congresses": [116, 117, 118, 119],
            "required_window_start": "2020-07-25",
            "required_window_end": "2026-07-25",
            "boundary_rule": (
                "Collect the complete 116th-119th Congress result sets; mark "
                "pre-2020-07-25 records as boundary-extension rather than "
                "discarding them before review."
            ),
            "legislation_types": [
                "hr",
                "hres",
                "hjres",
                "hconres",
                "hamdt",
                "s",
                "sres",
                "sjres",
                "sconres",
                "samdt",
            ],
            "query_logic": (
                "OR across outline identifiers, specific known title aliases, "
                "and Congress-qualified known-bill citations within each "
                "category. Every title alias also runs as an isolated "
                "non-scoring provenance probe; ambiguous short-Act aliases "
                "appear only in those probes."
            ),
            "result_transport": (
                "Traverse the official Congress.gov Advanced Search result pages "
                "at 250 records per page using document-number ascending sort."
            ),
            "page_reconciliation_rule": (
                "For every query, collected rows and distinct Congress-qualified "
                "record IDs must both equal the live result total. Every page "
                "range and row count must reconcile exactly."
            ),
            "lossless_candidate_rule": (
                "Retain every Advanced Search hit as a candidate. Deduplicate "
                "only by Congress-qualified record ID while retaining all query "
                "membership edges."
            ),
            "exclusion_rule": (
                "No automated semantic exclusion. Any later exclusion requires "
                "an explicit review disposition and reason in the exclusion "
                "ledger; recall-only hits remain visible as unclassified "
                "candidates."
            ),
            "relationship_enrichment": (
                "After Advanced Search discovery, use official Congress.gov "
                "measure and cosponsor pages to emit member-by-measure Sponsor "
                "and Cosponsor edges, sponsorship date, and original-cosponsor "
                "status. Enrichment cannot add undiscovered measures."
            ),
            "promotion": (
                "Candidate only. Category membership and ACU position require "
                "human ratification before scoring."
            ),
            "quorum_used": False,
        },
        "category_count": len(categories),
        "scoring_category_count": sum(
            1 for category in categories if category["scoring_category"]
        ),
        "coverage_probes": [
            {
                "id": "credit_union_headline_recall_net",
                "label": "Credit Union Headline Recall Net",
                "scoring_category": False,
                "record_kind": "coverage_probe",
                "required_for_acceptance": True,
                "query_terms": [
                    {
                        "kind": "coverage_probe",
                        "query": '"credit union"',
                    }
                ],
                "advanced_search_query": '("credit union")',
                "purpose": (
                    "Catch and measure legislation that contains the headline "
                    "credit-union phrase but is not recovered by an outline "
                    "category query. Results remain unclassified candidates."
                ),
            }
        ]
        + title_alias_probes,
        "categories": categories,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outline", type=Path, default=DEFAULT_OUTLINE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    registry = build_registry(args.outline)
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(registry, indent=2, ensure_ascii=False) + "\n"
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # A concurrent scanner can hold the freshly written file open; retry the
        # replace before failing, then raise.
        for attempt in range(8):
            try:
                os.replace(temporary, output)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.05 * (2**attempt), 0.75))
    finally:
        if temporary.exists():
            temporary.unlink()
    print(
        json.dumps(
            {
                "output": str(output),
                "source_sha256": registry["source"]["sha256"],
                "categories": registry["category_count"],
                "scoring_categories": registry["scoring_category_count"],
                "identifiers": sum(
                    len(category["identifiers"])
                    for category in registry["categories"]
                ),
                "known_bill_records": sum(
                    len(category["known_bills"])
                    for category in registry["categories"]
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

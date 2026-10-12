from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

from legislation import cross_reference_legislation_press_articles as article_xref
from legislation import review_legislation_candidate_memberships as membership_review


def test_trusted_manifest_freezes_pre_today_sources_and_safety_contract() -> None:
    manifest = json.loads(
        (
            ROOT
            / "source_data"
            / "legislation"
            / "acu_trusted_press_article_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["trust_cutoff_date"] == "2026-07-25"
    assert manifest["article_workbook"]["expected_real_article_rows"] == 2388
    assert (
        manifest["article_workbook"][
            "expected_synthetic_billstatus_rows_excluded"
        ]
        == 1310
    )
    contracts = manifest["contracts"]
    assert contracts["article_absence_is_never_exclusion_evidence"] is True
    assert contracts["article_evidence_never_auto_includes_membership"] is True
    assert contracts["article_evidence_never_auto_excludes_membership"] is True
    assert contracts["article_evidence_never_assigns_sponsorship_role"] is True


def test_bill_citations_and_nearby_actions_are_observable() -> None:
    text = (
        "The committee advanced H.R. 7035. Senator Example later "
        "cosponsored S. 3623, the Credit Card Competition Act."
    )
    citations = article_xref.extract_citations(text)
    assert [row["compact_key"] for row in citations] == ["hr:7035", "s:3623"]
    s_cite = next(row for row in citations if row["compact_key"] == "s:3623")
    actions, context = article_xref.action_evidence(
        text, [(s_cite["start"], s_cite["end"])]
    )
    assert "cosponsorship" in actions
    assert "S. 3623" in context


def test_action_category_match_is_strong_but_never_a_decision() -> None:
    article = {
        "article_id": "trusted:2",
        "trusted_before": "2026-07-25",
        "member": "Example Member",
        "bioguide": "E000001",
        "date": "2025-05-01",
        "article_congress": 119,
        "title": "Member cosponsors the Credit Card Competition Act",
        "url": "https://example.test/article",
        "categories_raw": "Interchange",
        "category_ids": ["interchange"],
        "relevance": "tracked bill only",
        "body_available": True,
        "searchable_text": (
            "Example Member cosponsored H.R. 7035, the Credit Card "
            "Competition Act."
        ),
        "normalized_text": (
            "example member cosponsored h r 7035 the credit card competition act"
        ),
        "citations": article_xref.extract_citations(
            "Example Member cosponsored H.R. 7035, the Credit Card "
            "Competition Act."
        ),
    }
    record = {
        "canonical_key": "119:hr:7035",
        "display_number": "H.R. 7035",
        "title": "Credit Card Competition Act of 2025",
    }
    match = article_xref.build_match(
        article,
        record,
        {"interchange"},
        {"hr:7035": {119}},
    )
    assert match is not None
    assert match["evidence_strength"] == "strong"
    assert (
        match["review_signal"]
        == "trusted_article_action_and_category_corroboration"
    )
    assert match["automated_membership_decision"] is None
    assert match["article_evidence_does_not_assign_legislative_role"] is True


def test_category_mismatch_requires_review_and_absence_is_not_negative() -> None:
    strength, signal = article_xref.match_strength(
        action_types=["cosponsorship"],
        category_overlap=False,
        congress_compatible=True,
        exact_bill_citation=True,
        citations_in_action_clause=1,
    )
    assert strength == "review"
    assert signal == "trusted_article_action_category_mismatch_requires_review"
    assert "no_trusted_article_match_not_dispositive" in (
        ROOT / "fec" / "legislation" / "cross_reference_legislation_press_articles.py"
    ).read_text(encoding="utf-8")


def test_article_date_congress_mapping_is_explicit() -> None:
    assert article_xref.congress_for_article_date("2020-07-25") == 116
    assert article_xref.congress_for_article_date("2021-01-03") == 117
    assert article_xref.congress_for_article_date("2023-01-03") == 118
    assert article_xref.congress_for_article_date("2025-01-03") == 119


def test_generic_act_titles_cannot_match_articles() -> None:
    assert article_xref.title_match_phrase("Act") is None
    assert article_xref.title_match_phrase("This Act") is None
    assert article_xref.title_match_phrase("SMART Act") is None
    assert article_xref.title_match_phrase("Credit Card Competition Act")


def test_short_act_alias_is_visible_but_not_relevance_evidence() -> None:
    category = {
        "title_aliases": [
            "SMART Act",
            "Credit Card Competition Act",
        ]
    }
    specific, short_act = membership_review.matched_title_aliases(
        "The SMART Act and Credit Card Competition Act were discussed.",
        category,
    )
    assert specific == ["Credit Card Competition Act"]
    assert short_act == ["SMART Act"]


def _article(
    *,
    date: str,
    congress: int | None,
    text: str,
    categories: list[str] | None = None,
) -> dict[str, object]:
    return {
        "article_id": "trusted:test",
        "trusted_before": "2026-07-25",
        "member": "Example Member",
        "bioguide": "E000001",
        "date": date,
        "article_congress": congress,
        "title": "Test article",
        "url": "https://example.test/test",
        "categories_raw": "Interchange",
        "category_ids": categories or ["interchange"],
        "relevance": "test",
        "body_available": True,
        "searchable_text": text,
        "normalized_text": article_xref.normalized_phrase(text),
        "citations": article_xref.extract_citations(text),
    }


def test_unique_bill_number_never_overrides_cross_congress_date() -> None:
    article = _article(
        date="2023-05-01",
        congress=118,
        text="Example Member cosponsored H.R. 7035.",
    )
    record = {
        "canonical_key": "119:hr:7035",
        "display_number": "H.R. 7035",
        "title": "Credit Card Competition Act of 2025",
    }
    match = article_xref.build_match(
        article, record, {"interchange"}, {"hr:7035": {119}}
    )
    assert match is not None
    assert match["congress_compatible"] is False
    assert match["evidence_strength"] == "review"
    assert (
        match["review_signal"]
        == "bill_citation_congress_mismatch_requires_review"
    )


def test_action_in_different_sentence_does_not_attach_to_bill() -> None:
    article = _article(
        date="2025-05-01",
        congress=119,
        text=(
            "Example Member cosponsored H.R. 1. "
            "The article also mentions H.R. 7035."
        ),
    )
    record = {
        "canonical_key": "119:hr:7035",
        "display_number": "H.R. 7035",
        "title": "Credit Card Competition Act of 2025",
    }
    match = article_xref.build_match(
        article, record, {"interchange"}, {"hr:7035": {119}}
    )
    assert match is not None
    assert match["action_types_near_reference"] == []
    assert match["evidence_strength"] == "moderate"


def test_title_only_reference_is_family_level_review() -> None:
    article = _article(
        date="2025-05-01",
        congress=119,
        text=(
            "The Financial Reporting Threshold Modernization Act would "
            "change reporting thresholds."
        ),
        categories=["examination_supervision"],
    )
    record = {
        "canonical_key": "119:hr:9999",
        "display_number": "H.R. 9999",
        "title": "Financial Reporting Threshold Modernization Act",
    }
    match = article_xref.build_match(
        article,
        record,
        {"examination_supervision"},
        {"hr:9999": {119}},
    )
    assert match is not None
    assert match["reference_basis"] == ["exact_normalized_title"]
    assert match["evidence_strength"] == "review"
    assert (
        match["review_signal"]
        == "title_family_reference_requires_bill_identity_review"
    )


def test_multi_bill_action_clause_is_not_strong() -> None:
    article = _article(
        date="2025-05-01",
        congress=119,
        text="Example Member cosponsored H.R. 7035 and H.R. 1.",
    )
    record = {
        "canonical_key": "119:hr:7035",
        "display_number": "H.R. 7035",
        "title": "Credit Card Competition Act of 2025",
    }
    match = article_xref.build_match(
        article, record, {"interchange"}, {"hr:7035": {119}}
    )
    assert match is not None
    assert match["citations_in_action_clause"] == 2
    assert match["evidence_strength"] == "review"
    assert (
        match["review_signal"]
        == "multi_measure_action_clause_requires_review"
    )


def test_reverse_citation_audit_finds_absent_congress_qualified_reference() -> None:
    article = _article(
        date="2025-05-01",
        congress=119,
        text=(
            "Example Member introduced H.R. 9999. "
            "The release also discussed H.R. 7035."
        ),
    )
    result = article_xref.reverse_citation_coverage_audit(
        [article],
        {"119:hr:7035"},
        {116, 117, 118, 119},
    )

    assert len(result["audit"]) == 2
    assert len(result["absent_unambiguous"]) == 1
    assert result["ambiguous_unresolved"] == []
    absent = result["absent_unambiguous"][0]
    assert absent["canonical_key"] == "119:hr:9999"
    assert (
        absent["reference_resolution"]
        == "unambiguous_congress_qualified_absent"
    )
    assert (
        absent["targeted_congress_gov_query"]
        == '(congressId:119 AND cite:"hr9999")'
    )
    assert absent["trusted_before"] == "2026-07-25"
    assert absent["synthetic_billstatus_source"] is False
    assert absent["automated_candidate_creation"] is None
    assert absent["automated_membership_decision"] is None
    assert absent["automated_exclusion_decision"] is None
    assert absent["sponsorship_role_assigned"] is None
    present = next(
        row
        for row in result["audit"]
        if row["canonical_key"] == "119:hr:7035"
    )
    assert present["discovery_union_status"] == "present_in_discovery_union"


def test_reverse_citation_audit_preserves_every_repeated_occurrence() -> None:
    article = _article(
        date="2025-05-01",
        congress=119,
        text=(
            "Example Member introduced H.R. 9999. "
            "Example Member later supported H.R. 9999."
        ),
    )
    result = article_xref.reverse_citation_coverage_audit(
        [article],
        set(),
        {116, 117, 118, 119},
    )

    assert len(result["audit"]) == 2
    assert len(result["absent_unambiguous"]) == 2
    assert {
        row["citation_occurrence_index"]
        for row in result["absent_unambiguous"]
    } == {1, 2}
    assert len(
        {
            row["citation_audit_id"]
            for row in result["absent_unambiguous"]
        }
    ) == 2


def test_reverse_citation_audit_separates_unresolved_congress_references() -> None:
    missing_date = _article(
        date="",
        congress=None,
        text="The release discusses S. 42.",
    )
    outside_scope = _article(
        date="2027-05-01",
        congress=120,
        text="The release discusses H.R. 77.",
    )
    outside_scope["article_id"] = "trusted:outside"
    result = article_xref.reverse_citation_coverage_audit(
        [missing_date, outside_scope],
        set(),
        {116, 117, 118, 119},
    )

    assert result["absent_unambiguous"] == []
    assert len(result["audit"]) == 2
    assert len(result["ambiguous_unresolved"]) == 2
    assert {
        row["ambiguity_reason"]
        for row in result["ambiguous_unresolved"]
    } == {
        "article_date_missing_unparseable_or_outside_supported_congress_window",
        "article_date_maps_outside_required_congress_scope",
    }
    assert all(
        row["canonical_key"] is None
        and row["review_required"] is True
        and row["article_evidence_is_corroborative_not_dispositive"] is True
        for row in result["ambiguous_unresolved"]
    )


def test_reverse_citation_audit_keeps_no_action_absence_unresolved() -> None:
    article = _article(
        date="2025-05-01",
        congress=119,
        text="The release mentions H.R. 9999.",
    )
    result = article_xref.reverse_citation_coverage_audit(
        [article],
        set(),
        {116, 117, 118, 119},
    )

    assert result["absent_unambiguous"] == []
    assert len(result["ambiguous_unresolved"]) == 1
    unresolved = result["ambiguous_unresolved"][0]
    assert unresolved["canonical_key"] == "119:hr:9999"
    assert unresolved["absent_from_discovery_union"] is True
    assert (
        unresolved["ambiguity_reason"]
        == "citation_absent_but_no_local_legislative_action"
    )
    assert unresolved["targeted_congress_gov_query"] is None


def test_reverse_citation_audit_keeps_multi_measure_action_unresolved() -> None:
    article = _article(
        date="2025-05-01",
        congress=119,
        text="Example Member cosponsored H.R. 9999 and H.R. 8888.",
    )
    result = article_xref.reverse_citation_coverage_audit(
        [article],
        set(),
        {116, 117, 118, 119},
    )

    assert result["absent_unambiguous"] == []
    assert len(result["ambiguous_unresolved"]) == 2
    assert all(
        row["ambiguity_reason"]
        == "citation_absent_in_multi_measure_action_clause"
        and row["citations_in_action_clause"] == 2
        for row in result["ambiguous_unresolved"]
    )

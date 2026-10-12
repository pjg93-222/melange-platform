from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

from legislation import build_legislation_search_registry as registry_builder
from legislation import review_legislation_candidate_memberships as membership_review

REGISTRY = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
)
IDENTIFIER_PROBES = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_identifier_probe_registry.json"
)
TRUSTED_SOURCE_PROBES = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_historical_probe_registry.json"
)


def test_advanced_search_registry_is_lossless_and_congress_qualified() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    contract = registry["collection_contract"]

    assert contract["primary_instrument"] == (
        "Congress.gov Advanced Search - Legislation"
    )
    assert contract["congresses"] == [116, 117, 118, 119]
    assert contract["search_scope"] == "All fields including Bill Text"
    assert contract["word_variants"] is True
    assert contract["quorum_used"] is False
    assert "Retain every Advanced Search hit" in contract[
        "lossless_candidate_rule"
    ]
    assert "No automated semantic exclusion" in contract["exclusion_rule"]
    assert set(contract["legislation_types"]) == {
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
    }


def test_outline_registry_counts_and_known_bills_are_stable() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    assert registry["category_count"] == 19
    assert registry["scoring_category_count"] == 18
    assert sum(len(item["identifiers"]) for item in registry["categories"]) == 210
    assert sum(len(item["known_bills"]) for item in registry["categories"]) == 55
    coverage_probe_ids = {
        probe["id"] for probe in registry["coverage_probes"]
    }
    assert "credit_union_headline_recall_net" in coverage_probe_ids
    assert len(registry["coverage_probes"]) == 43
    assert (
        sum(
            bool(probe.get("title_alias"))
            for probe in registry["coverage_probes"]
        )
        == 42
    )


def test_every_outline_identifier_has_an_individual_probe() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    probes = json.loads(IDENTIFIER_PROBES.read_text(encoding="utf-8"))

    source_identifiers = {
        (
            category["id"],
            identifier["source_paragraph"],
            identifier["text"],
        )
        for category in registry["categories"]
        for identifier in category["identifiers"]
    }
    probe_identifiers = {
        (
            probe["parent_category_id"],
            probe["source_paragraph"],
            probe["identifier_text"],
        )
        for probe in probes["categories"]
    }
    assert probes["category_count"] == 210
    assert len({probe["id"] for probe in probes["categories"]}) == 210
    assert probe_identifiers == source_identifiers


def test_recall_probe_is_not_a_scoring_category() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    probe = next(
        probe
        for probe in registry["coverage_probes"]
        if probe["id"] == "credit_union_headline_recall_net"
    )

    assert probe["scoring_category"] is False
    assert probe["record_kind"] == "coverage_probe"
    assert probe["advanced_search_query"] == '("credit union")'
    assert "unclassified candidates" in probe["purpose"]


def test_false_positive_review_is_evidence_backed_and_non_destructive() -> None:
    review_source = (
        ROOT / "fec" / "legislation" / "review_legislation_candidate_memberships.py"
    ).read_text(encoding="utf-8")
    text_source = (
        ROOT / "fec" / "enrich_congress_text_relevance_pages.mjs"
    ).read_text(encoding="utf-8")
    article_source = (
        ROOT / "fec" / "legislation" / "cross_reference_legislation_press_articles.py"
    ).read_text(encoding="utf-8")
    pipeline_source = (
        ROOT / "fec" / "run_legislation_coverage_pipeline.ps1"
    ).read_text(encoding="utf-8")

    assert "automated_triage_does_not_exclude" in review_source
    assert "exclude_false_positive" in review_source
    assert "candidate_retained_in_discovery_corpus" in review_source
    assert "localized_official_text_evidence" in review_source
    assert "coverage_probe_unclassified_requires_review" in review_source
    assert "exclusion_decisions_made: 0" in text_source
    assert "destructive_filtering: false" in text_source
    assert "coverage_probe_classification" in text_source
    assert "rejected_non_specific_act_identifier" in text_source
    assert "distinctive_identifier_tokens" in text_source
    assert "generic_act_language_can_never_establish_relevance" in review_source
    assert "short_act_title_alone_is_not_relevance_evidence" in review_source
    assert "short_act_title_requires_independent_evidence" in review_source
    assert "article_absence_is_never_exclusion_evidence" in article_source
    assert "article_evidence_does_not_assign_legislative_role" in article_source
    assert "automated_membership_decision" in article_source
    assert "billstatus:" in article_source
    assert "cross_reference_legislation_press_articles.py" in pipeline_source


def test_short_act_aliases_are_isolated_non_scoring_probes() -> None:
    registry = registry_builder.build_registry(
        registry_builder.DEFAULT_OUTLINE
    )
    alias_probes = [
        probe
        for probe in registry["coverage_probes"]
        if probe.get("title_alias")
    ]
    short_probes = [
        probe
        for probe in alias_probes
        if probe.get("ambiguous_short_act_alias")
    ]
    assert len(alias_probes) == 42
    assert len(short_probes) == 16
    assert all(probe["membership_non_dispositive"] for probe in alias_probes)
    assert all(not probe["scoring_category"] for probe in alias_probes)
    examination = next(
        category
        for category in registry["categories"]
        if category["id"] == "examination_supervision"
    )
    main_queries = {term["query"] for term in examination["query_terms"]}
    assert '"SMART Act"' not in main_queries
    assert any(
        probe["title_alias"] == "SMART Act" for probe in short_probes
    )


def test_resolved_decisions_require_official_evidence_basis() -> None:
    incomplete = {
        "canonical_key": "119:hr:1",
        "query_id": "credit_union_tax_status",
        "disposition": "exclude_false_positive",
        "reason": "Unrelated.",
        "evidence": "https://www.congress.gov/bill/119th-congress/house-bill/1",
        "reviewer": "reviewer",
        "reviewed_at": "2026-07-25T00:00:00Z",
    }
    try:
        membership_review.validate_decision(incomplete)
    except ValueError as error:
        assert "evidence_basis" in str(error)
    else:
        raise AssertionError("Decision without official evidence basis passed")

    excerpt = "The operative provisions concern a different subject."
    complete = dict(
        incomplete,
        evidence_basis="operative_provisions",
        decision_type="membership_category_relevance",
        reason_code="different_subject",
        reviewer_confidence="high",
        adjudication_state="reviewed",
        evidence_sources_checked=["official_bill_text"],
        official_source_url=(
            "https://www.congress.gov/bill/119th-congress/house-bill/1"
        ),
        official_text_version="Introduced in House",
        official_section="Section 1",
        official_evidence_excerpt=excerpt,
        official_evidence_sha256=hashlib.sha256(
            excerpt.encode("utf-8")
        ).hexdigest(),
        official_retrieved_at="2026-07-25",
        text_unavailable=False,
    )
    membership_review.validate_decision(complete)

    title_only_exclusion = dict(complete, evidence_basis="official_title")
    try:
        membership_review.validate_decision(title_only_exclusion)
    except ValueError as error:
        assert "exclude_false_positive decisions require" in str(error)
    else:
        raise AssertionError("Title-only false-positive exclusion passed")

    identity_mismatch_inclusion = dict(
        complete,
        disposition="include",
        evidence_basis="congress_qualified_identity_mismatch",
    )
    try:
        membership_review.validate_decision(identity_mismatch_inclusion)
    except ValueError as error:
        assert "include decisions require" in str(error)
    else:
        raise AssertionError("Identity-mismatch inclusion passed")

    generic_excerpt = "Act"
    generic_exclusion = dict(
        complete,
        official_evidence_excerpt=generic_excerpt,
        official_evidence_sha256=hashlib.sha256(
            generic_excerpt.encode("utf-8")
        ).hexdigest(),
    )
    try:
        membership_review.validate_decision(generic_exclusion)
    except ValueError as error:
        assert "generic or ambiguous Act language" in str(error)
    else:
        raise AssertionError("Generic Act exclusion evidence passed")

    article_absence_exclusion = dict(
        complete,
        evidence_sources_checked=["official_bill_text", "no ACU article found"],
    )
    try:
        membership_review.validate_decision(article_absence_exclusion)
    except ValueError as error:
        assert "press absence is neutral" in str(error)
    else:
        raise AssertionError("Article-absence exclusion evidence passed")


def test_trusted_source_recall_registry_covers_archive_and_press() -> None:
    registry = json.loads(
        TRUSTED_SOURCE_PROBES.read_text(encoding="utf-8")
    )
    assert registry["schema"] == (
        "acu-congress-trusted-source-recall-probes-v2"
    )
    terms = [
        term
        for probe in registry["categories"]
        for term in probe["query_terms"]
    ]
    keys = {term["canonical_key"] for term in terms}
    assert len(terms) == len(keys)
    assert len(keys) == registry["combined_trusted_recall_record_count"]
    assert sum(
        "historical_archive" in term["source_kinds"] for term in terms
    ) == registry["historical_archive_record_count"]
    assert sum(
        "trusted_press_article" in term["source_kinds"] for term in terms
    ) == registry["trusted_article_congress_qualified_record_count"]
    assert all(
        term["query"]
        == (
            f'(congressId:{term["canonical_key"].split(":")[0]} '
            f'AND cite:"{term["canonical_key"].split(":")[1]}'
            f'{int(term["canonical_key"].split(":")[2])}")'
        )
        for term in terms
    )

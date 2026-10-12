from __future__ import annotations

import copy

from fec.legislation.legislation_culling_integration_contract import (
    EVIDENCE_SCHEMA,
    MEMBERSHIP_SCHEMA,
    REQUIRED_INTEGRATION_GATES,
    evaluate_integration_gate,
    membership_is_finalizable,
    partition_candidates,
    validate_candidate_evidence,
)


SHA = "a" * 64
URL = "https://www.congress.gov/bill/117th-congress/house-bill/1"
TEXT_URL = URL + "/text"


def evidence(*, query_ids=("q1", "q2"), summary_status="available"):
    summary = {
        "status": summary_status,
        "source_url": URL + "/summary",
        "text_sha256": SHA,
    }
    if summary_status == "officially_unavailable":
        summary = {
            "status": summary_status,
            "unavailability_reason": "Congress.gov has no CRS summary",
            "availability_receipt_sha256": SHA,
        }
    return {
        "schema": EVIDENCE_SCHEMA,
        "canonical_key": "117:hr:1",
        "candidate_preserved": True,
        "discovery": {
            "path": "candidate.ndjson",
            "sha256": SHA,
            "record_sha256": SHA,
        },
        "membership_query_ids": list(query_ids),
        "official_evidence": {
            "title": {
                "status": "available",
                "source_url": URL,
                "text_sha256": SHA,
            },
            "summary": summary,
            "full_text": {
                "status": "accepted",
                "expected_url": TEXT_URL,
                "final_url": TEXT_URL,
                "body_sha256": SHA,
                "archive_reverified": True,
                "decompressed_body_sha256": SHA,
                "marker_bounded_text_sha256": SHA,
                "extraction_receipt_sha256": SHA,
                "cookie_values_persisted": False,
                "version_inventory": {
                    "expected_version_count": 1,
                    "reviewed_version_count": 1,
                    "all_versions_reviewed": True,
                },
                "validation": {
                    "status_200": True,
                    "exact_url": True,
                    "html_content_type": True,
                    "not_challenge": True,
                    "expected_main_marker": True,
                },
            },
        },
        "category_evidence": [
            {
                "category_id": f"category_{index:02d}",
                "title_reviewed": True,
                "summary_status_reviewed": True,
                "full_text_reviewed": True,
                "absence_is_neutral": True,
                "evidence_sha256": SHA,
            }
            for index in range(19)
        ],
        "keyword_review": {
            "ruleset_sha256": SHA,
            "title_reviewed": True,
            "summary_reviewed": True,
            "full_text_reviewed": True,
            "absence_is_neutral": True,
        },
        "stage": {
            "status": "available",
            "value": "introduced",
            "actions_sha256": SHA,
        },
        "relationships": {
            "status": "complete",
            "role_provenance_complete": True,
            "sponsor_count": 1,
            "original_cosponsor_count": 2,
            "cosponsor_count": 3,
            "edges_sha256": SHA,
        },
    }


def decision(query_id, disposition, *, confidence="high"):
    return {
        "schema": MEMBERSHIP_SCHEMA,
        "canonical_key": "117:hr:1",
        "query_id": query_id,
        "disposition": disposition,
        "adjudication_state": "human_ratified",
        "automated_final_decision": False,
        "reviewer_confidence": confidence,
        "reason": "Official evidence resolves this membership.",
        "reason_code": "official_evidence_reviewed",
        "reviewer": "reviewer",
        "reviewed_at": "2026-07-26T22:00:00-05:00",
        "decision_evidence_sha256": SHA,
        "evidence_sources_checked": [
            "official_title",
            "official_crs_summary",
            "official_full_text",
        ],
        "absence_used_as_exclusion_evidence": False,
        "full_text_rule_out_complete": True,
    }


def test_evidence_requires_title_summary_full_text_keyword_stage_and_roles():
    readiness = validate_candidate_evidence(evidence())
    assert readiness["semantic_review_ready"] is True
    assert readiness["category_audit_ready"] is True
    assert readiness["integration_evidence_ready"] is True


def test_missing_summary_stays_out_of_final_membership_decisions():
    row = evidence(summary_status="officially_unavailable")
    readiness = validate_candidate_evidence(row)
    assert readiness["summary_explicitly_unavailable"] is True
    assert readiness["semantic_review_ready"] is True
    assert membership_is_finalizable(decision("q1", "include"), row) is False
    reviewed_unavailability = decision("q1", "include")
    reviewed_unavailability["evidence_sources_checked"] = [
        "official_title",
        "official_summary_unavailable",
        "official_full_text",
    ]
    assert membership_is_finalizable(reviewed_unavailability, row) is True


def test_one_valid_include_prevents_global_junk_even_with_other_exclusion():
    row = evidence()
    partition = partition_candidates(
        [row],
        [decision("q1", "include"), decision("q2", "exclude_false_positive")],
    )
    assert partition["keep"] == ["117:hr:1"]
    assert partition["junk"] == []
    assert partition["flagged"] == []


def test_global_junk_requires_every_membership_to_be_high_confidence_exclusion():
    row = evidence()
    complete = partition_candidates(
        [row],
        [
            decision("q1", "exclude_false_positive"),
            decision("q2", "exclude_false_positive"),
        ],
    )
    assert complete["junk"] == ["117:hr:1"]

    incomplete_rows = [
        decision("q1", "exclude_false_positive"),
        decision("q2", "exclude_false_positive", confidence="medium"),
    ]
    incomplete = partition_candidates([row], incomplete_rows)
    assert incomplete["junk"] == []
    assert incomplete["flagged"] == ["117:hr:1"]
    assert incomplete["unresolved_memberships"] == [
        {"canonical_key": "117:hr:1", "query_id": "q2"}
    ]


def test_unresolved_membership_is_flagged_and_never_deleted():
    row = evidence(query_ids=("q1",))
    unresolved = decision("q1", "include")
    unresolved["disposition"] = "unresolved"
    partition = partition_candidates([row], [unresolved])
    assert partition["flagged"] == ["117:hr:1"]
    assert partition["candidate_deletion_performed"] is False
    assert partition["main_corpus_write_authorized"] is False


def test_integration_gate_fails_closed_until_every_gate_passes():
    gates = {name: True for name in REQUIRED_INTEGRATION_GATES}
    gates["full_text_coverage_complete"] = False
    blocked = evaluate_integration_gate(gates, candidate_count=18557, membership_count=64004)
    assert blocked["status"] == "BLOCKED"
    assert blocked["staged_generation_eligible"] is False
    assert blocked["main_corpus_write_performed"] is False

    passed = evaluate_integration_gate(
        {name: True for name in REQUIRED_INTEGRATION_GATES},
        candidate_count=18557,
        membership_count=64004,
    )
    assert passed["status"] == "PASS_STAGED_INTEGRATION_ELIGIBLE"
    assert passed["staged_generation_eligible"] is True
    assert passed["automatic_promotion_allowed"] is False

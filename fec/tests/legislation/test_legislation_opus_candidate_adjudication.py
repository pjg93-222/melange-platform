"""Focused tests for the candidate-level Opus adjudication path.

Covers the deterministic packet producer and the proposal-only runner: the
nineteen-slot contract, the one-proposal-unit-per-candidate rule, all-version
review, evidence-gate flagging without a provider call, policy downgrades,
immutability, non-replay, and deterministic canary selection.
"""

from __future__ import annotations

from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, filename: str):
    path = ROOT / "fec" / "legislation" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PACKETS = _load("opus_packets", "build_legislation_adjudication_packets.py")
RUNNER = _load("opus_runner", "run_legislation_opus_candidate_adjudication.py")

SHA = "a" * 64
CATEGORY_IDS = [
    "cdfi_fund",
    "cfpb_issues_structural_reform",
    "charter_modernization_governance",
    "credit_card_consumer_fee_mandates",
    "credit_union_bank_competitive_dynamics",
    "credit_union_capital_requirements",
    "credit_union_tax_status",
    "data_privacy_cybersecurity",
    "defense_credit_union_issues",
    "examination_supervision",
    "field_of_membership",
    "fraud_prevention",
    "interchange",
    "member_business_lending",
    "mortgage_lending_housing",
    "ncua_budget_share_insurance",
    "payments",
    "technology_emerging_issues",
    "telephone_consumer_protection_act",
]


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def categories() -> list[dict]:
    return [
        {
            "category_id": name,
            "label": name.replace("_", " ").title(),
            "scoring_category": name != "credit_union_bank_competitive_dynamics",
            "query_sha256": SHA,
        }
        for name in CATEGORY_IDS
    ]


def evidence_row(*, summary_settled: bool = True, expected_versions: int = 1) -> dict:
    summary = (
        {
            "status": "officially_unavailable",
            "unavailability_reason": "Congress.gov publishes no CRS summary",
            "availability_receipt_sha256": SHA,
        }
        if summary_settled
        else {"status": "available_pending_congress_gov_fetch"}
    )
    return {
        "schema": "acu-legislation-candidate-evidence-v1",
        "canonical_key": "119:hr:1",
        "candidate_preserved": True,
        "discovery": {"path": "discovery.ndjson", "sha256": SHA, "record_sha256": SHA},
        "membership_query_ids": ["interchange", "payments"],
        "keyword_review": {"status": "not_performed"},
        "official_evidence": {
            "title": {
                "status": "available",
                "text_sha256": SHA,
                "source_url": "https://www.congress.gov/bill/119th-congress/house-bill/1",
            },
            "summary": summary,
            "full_text": {
                "status": "accepted",
                "body_sha256": SHA,
                "decompressed_body_sha256": SHA,
                "expected_url": "https://www.congress.gov/bill/119th-congress/house-bill/1/text",
                "final_url": "https://www.congress.gov/bill/119th-congress/house-bill/1/text",
                "marker_bounded_text_sha256": SHA,
                "extraction_receipt_sha256": SHA,
                "archive_reverified": True,
                "cookie_values_persisted": False,
                "validation": {
                    "status_200": True,
                    "exact_url": True,
                    "html_content_type": True,
                    "not_challenge": True,
                    "expected_main_marker": True,
                },
                "version_inventory": {
                    "expected_version_count": expected_versions,
                    "reviewed_version_count": expected_versions,
                    "all_versions_reviewed": True,
                },
            },
        },
        "stage": {"status": "available", "value": "Introduced", "actions_sha256": SHA},
        "relationships": {
            "status": "complete",
            "role_provenance_complete": True,
            "sponsor_count": 1,
            "original_cosponsor_count": 0,
            "cosponsor_count": 0,
            "edges_sha256": SHA,
        },
        "category_evidence": [],
    }


def worksheet_row(expected_versions: int = 1) -> dict:
    return {
        "canonical_key": "119:hr:1",
        "display_number": "H.R.1",
        "congress": 119,
        "measure_type": "hr",
        "number": 1,
        "record_kind": "bill",
        "official_title": "Interchange Fairness Act",
        "sponsor": "Rep. Example [R-XX-1]",
        "candidate_evidence_sha256": SHA,
        "links": {
            "canonical_url": "https://www.congress.gov/bill/119th-congress/house-bill/1",
            "text_url": "https://www.congress.gov/bill/119th-congress/house-bill/1/text",
        },
        "official_text_version_inventory": {
            "expected_version_count": expected_versions,
            "reviewed_version_count": expected_versions,
            "all_versions_reviewed": True,
        },
    }


def membership_rows() -> list[dict]:
    return [
        {
            "canonical_key": "119:hr:1",
            "query_id": query,
            "queue_row_id": f"qr-{query}",
            "tier": "T1_scoring_category_with_accepted_full_text",
            "membership_cause": {
                "label": query.title(),
                "record_kind": "category",
                "scoring_category": True,
                "membership_non_dispositive": False,
                "ambiguous_short_act_alias": False,
            },
            "query_terms": {"candidate_explicitly_named_in_query": False},
        }
        for query in ("interchange", "payments")
    ]


def original_method_stub() -> SimpleNamespace:
    return SimpleNamespace(
        manifest_sha256="b" * 64,
        # Unit packets use the runner's explicit compatibility registry.
        # Production v2 packets bind a non-empty SHA to the sidecar.
        method_registry_sha256="",
        preservation={
            "letter_row_count": 296,
            "letter_id_count": 7,
            "letter_member_count": 251,
            "amendment_row_count": 160,
        },
        candidate_evidence=lambda _key: None,
    )


def amendment_method_stub(text: str) -> SimpleNamespace:
    body = text.encode("utf-8")
    return SimpleNamespace(
        manifest_sha256="d" * 64,
        method_registry_sha256="",
        preservation={"letter_row_count": 296, "letter_id_count": 7},
        candidate_evidence=lambda _key: {
            "schema": "acu-legislation-original-method-candidate-evidence-v1",
            "canonical_key": "119:samdt:1",
            "measure_kind": "amendment",
            "official_amendment_text": {
                "status": "available",
                "evidence_class": "official_dispositive",
                "text": text,
                "full_text_sha256": sha_of(body),
                "full_text_bytes": len(body),
            },
            "parent_context": {
                "canonical_key": "119:s:1",
                "title": "Parent Act",
                "evidence_class": "official_context_only",
                "direction_inheritance_permitted": False,
            },
            "amendment_offerer": {
                "display": "Sen. Example",
                "role": "amendment_offerer",
                "evidence_class": "role_authority_only",
            },
            "sponsor_floor_remarks": None,
            "floor_speech_rescue": None,
            "press": [],
            "fixed_downstream_weight": 0.5,
        },
    )


def write_archive(tmp_path: Path, fragment: bytes) -> tuple[Path, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    prefix_text = "<html><head></head><body>caf\u00e9" + "x" * 64
    prefix = prefix_text.encode("utf-8")
    body = prefix + fragment
    archive = tmp_path / "page.html.gz"
    with gzip.open(archive, "wb") as handle:
        handle.write(body)
    receipt = {
        "canonical_key": "119:hr:1",
        "archive_path": str(archive),
        "archive_sha256": sha_of(archive.read_bytes()),
        "decompressed_body_sha256": sha_of(body),
        "marker_offset": len(prefix_text),
        "marker_bounded_bytes": len(fragment),
        "marker_bounded_text_sha256": sha_of(fragment),
        "_receipt_path": str(tmp_path / "extract.json"),
    }
    return archive, receipt


def govinfo_receipt(tmp_path: Path, *, code: str, present: bool) -> dict:
    payload = (
        f"<bill><section><text>Section 2 of the Federal Credit Union Act is amended "
        f"({code}).</text></section></bill>"
    ).encode("utf-8")
    object_path = tmp_path / f"{code}.xml"
    if present:
        object_path.write_bytes(payload)
    return {
        "kind": "accepted",
        "canonical_key": "119:hr:1",
        "http_status": 200,
        "version_code": code,
        "version_type": "Introduced in House",
        "version_date": "2025-01-03T05:00:00Z",
        "version_index": 0,
        "govinfo_package": f"BILLS-119hr1{code}",
        "final_url": f"https://www.govinfo.gov/content/pkg/BILLS-119hr1{code}/xml/x.xml",
        "content_sha256": sha_of(payload),
        "content_bytes": len(payload),
        "object_path": str(object_path),
        "identity_validation": {"matched_congress": "119"},
        "_receipt_path": str(tmp_path / f"{code}-receipt.json"),
        "_receipt_sha256": SHA,
    }


def make_packet(tmp_path: Path, **kwargs) -> dict:
    fragment = kwargs.pop("fragment", b"<p>Interchange fees are capped.</p>")
    expected_versions = kwargs.pop("expected_versions", 1)
    objects_present = kwargs.pop("objects_present", False)
    summary_settled = kwargs.pop("summary_settled", True)
    _, extraction = write_archive(tmp_path, fragment)
    receipts = [
        govinfo_receipt(tmp_path, code=f"v{index}", present=objects_present)
        for index in range(expected_versions)
    ]
    return PACKETS.build_packet(
        worksheet=worksheet_row(expected_versions),
        evidence=evidence_row(
            summary_settled=summary_settled, expected_versions=expected_versions
        ),
        memberships=membership_rows(),
        categories=categories(),
        extraction=extraction,
        govinfo_receipts=receipts,
        billstatus=None,
        original_method=original_method_stub(),
        max_text_bytes=kwargs.pop("max_text_bytes", 600_000),
    )


# --------------------------------------------------------------------------
# packet producer
# --------------------------------------------------------------------------


def test_packet_carries_nineteen_slots_and_every_membership(tmp_path):
    packet = make_packet(tmp_path)
    assert packet["category_slot_count"] == 19
    assert len({row["category_id"] for row in packet["category_slots"]}) == 19
    assert packet["membership_count"] == 2
    assert [row["query_id"] for row in packet["memberships"]] == ["interchange", "payments"]
    assert packet["proposal_unit"] == "candidate"
    discovered = [row for row in packet["category_slots"] if row["is_discovered_membership"]]
    assert {row["category_id"] for row in discovered} == {"interchange", "payments"}


def test_congress_gov_text_is_reextracted_and_hash_verified(tmp_path):
    packet = make_packet(tmp_path)
    block = packet["official_evidence"]["congress_gov_full_text"]
    assert block["text_verified"] is True
    assert "Interchange fees are capped." in block["text"]
    assert block["displayed_version_only"] is True
    assert block["all_version_review_supported"] is False


def test_archive_hash_drift_fails_closed(tmp_path):
    _, extraction = write_archive(tmp_path, b"<p>text</p>")
    extraction["decompressed_body_sha256"] = "b" * 64
    packet = PACKETS.build_packet(
        worksheet=worksheet_row(),
        evidence=evidence_row(),
        memberships=membership_rows(),
        categories=categories(),
        extraction=extraction,
        govinfo_receipts=[],
        billstatus=None,
        original_method=original_method_stub(),
        max_text_bytes=600_000,
    )
    block = packet["official_evidence"]["congress_gov_full_text"]
    assert block["text_verified"] is False
    assert block["unavailable_reason"] == "archive_body_hash_drift"
    assert packet["eligibility"]["include_evidence_complete"] is False


def test_absent_official_xml_object_blocks_all_version_review(tmp_path):
    packet = make_packet(tmp_path, expected_versions=2, objects_present=False)
    versions = packet["official_evidence"]["official_text_versions"]
    assert versions["locally_parsed_version_count"] == 0
    assert versions["all_version_review_complete"] is False
    assert versions["single_congress_gov_page_is_not_an_all_version_review"] is True
    assert all(
        row["unavailable_reason"] == "official_xml_object_absent_from_local_store"
        for row in versions["versions"]
    )
    # Include remains possible; only the exclusion route is closed.
    assert packet["eligibility"]["include_evidence_complete"] is True
    assert packet["eligibility"]["exclude_all_version_review_complete"] is False


def test_present_official_xml_objects_complete_the_all_version_review(tmp_path):
    packet = make_packet(tmp_path, expected_versions=2, objects_present=True)
    versions = packet["official_evidence"]["official_text_versions"]
    assert versions["locally_parsed_version_count"] == 2
    assert versions["all_version_review_complete"] is True
    assert packet["eligibility"]["exclude_all_version_review_complete"] is True
    assert "Federal Credit Union Act" in versions["versions"][0]["text"]


def test_object_hash_drift_is_recorded_not_assumed(tmp_path):
    receipts = [govinfo_receipt(tmp_path, code="v0", present=True)]
    Path(receipts[0]["object_path"]).write_bytes(b"<bill>tampered</bill>")
    _, extraction = write_archive(tmp_path, b"<p>text</p>")
    packet = PACKETS.build_packet(
        worksheet=worksheet_row(),
        evidence=evidence_row(),
        memberships=membership_rows(),
        categories=categories(),
        extraction=extraction,
        govinfo_receipts=receipts,
        billstatus=None,
        original_method=original_method_stub(),
        max_text_bytes=600_000,
    )
    versions = packet["official_evidence"]["official_text_versions"]
    assert versions["versions"][0]["unavailable_reason"] == "official_xml_object_hash_drift"
    assert versions["all_version_review_complete"] is False


def test_zero_declared_versions_is_a_blocker(tmp_path):
    packet = make_packet(tmp_path, expected_versions=0)
    assert packet["eligibility"]["include_evidence_complete"] is False
    assert (
        "no_official_text_version_is_declared_for_this_candidate"
        in packet["eligibility"]["blockers"]
    )


def test_amendment_text_without_govinfo_versions_is_dispatchable_and_reviewable(
    tmp_path,
):
    evidence = evidence_row(summary_settled=False, expected_versions=0)
    evidence["canonical_key"] = "119:samdt:1"
    evidence["official_evidence"]["title"]["source_url"] = (
        "https://www.congress.gov/amendment/119th-congress/senate-amendment/1"
    )
    evidence["official_evidence"]["full_text"] = {
        "status": "missing",
        "version_inventory": {
            "expected_version_count": 0,
            "reviewed_version_count": 0,
            "all_versions_reviewed": False,
        },
    }
    worksheet = worksheet_row(expected_versions=0)
    worksheet.update(
        {
            "canonical_key": "119:samdt:1",
            "display_number": "S.Amdt.1",
            "measure_type": "samdt",
            "record_kind": "amendment",
            "official_title": "Amendment to S.1",
        }
    )
    memberships = membership_rows()
    for row in memberships:
        row["canonical_key"] = "119:samdt:1"
    packet = PACKETS.build_packet(
        worksheet=worksheet,
        evidence=evidence,
        memberships=memberships,
        categories=categories(),
        extraction=None,
        govinfo_receipts=[],
        billstatus=None,
        original_method=amendment_method_stub(
            "At the end add a credit-union interchange exemption."
        ),
        max_text_bytes=600_000,
    )
    assert packet["eligibility"]["provider_dispatch_eligible"] is True
    assert packet["eligibility"]["include_evidence_complete"] is True
    assert (
        packet["eligibility"]["exclude_official_text_review_complete"]
        is True
    )
    assert packet["eligibility"]["exclude_all_version_review_complete"] is False
    assert packet["candidate"]["fixed_downstream_weight"] == 0.5
    assert "no_official_text_version_is_declared_for_this_candidate" not in packet[
        "eligibility"
    ]["blockers"]
    prep = RUNNER.prepare_packet(packet)
    assert "official_amendment_text" in prep["envelope"]["allowed_evidence_ids"]
    output = model_output(
        prep,
        disposition="exclude_false_positive",
        nexus="affirmative_false_positive",
        evidence_ids=["official_amendment_text"],
    )
    rows, downgrades = RUNNER.policy_effective_memberships(output, prep)
    assert all(
        row["proposed_disposition"] == "exclude_false_positive"
        for row in rows
    )
    assert downgrades == {}


def test_supplemental_amendment_text_changes_packet_identity(tmp_path):
    base_kwargs = dict(
        worksheet={
            **worksheet_row(expected_versions=0),
            "canonical_key": "119:samdt:1",
            "display_number": "S.Amdt.1",
            "measure_type": "samdt",
            "record_kind": "amendment",
        },
        evidence={
            **evidence_row(summary_settled=False, expected_versions=0),
            "canonical_key": "119:samdt:1",
        },
        memberships=[
            {**row, "canonical_key": "119:samdt:1"} for row in membership_rows()
        ],
        categories=categories(),
        extraction=None,
        govinfo_receipts=[],
        billstatus=None,
        max_text_bytes=600_000,
    )
    base_kwargs["evidence"]["official_evidence"]["full_text"] = {
        "status": "missing",
        "version_inventory": {"expected_version_count": 0},
    }
    first = PACKETS.build_packet(
        **base_kwargs,
        original_method=amendment_method_stub("First operative amendment text."),
    )
    second = PACKETS.build_packet(
        **base_kwargs,
        original_method=amendment_method_stub("Changed operative amendment text."),
    )
    assert first["evidence_sha256"] != second["evidence_sha256"]
    assert first["packet_row_id"] != second["packet_row_id"]


def test_unsettled_summary_is_a_blocker(tmp_path):
    packet = make_packet(tmp_path, summary_settled=False)
    assert packet["eligibility"]["include_evidence_complete"] is False
    assert (
        "official_crs_summary_neither_published_nor_officially_absent"
        in packet["eligibility"]["blockers"]
    )


def test_oversized_evidence_is_a_blocker(tmp_path):
    packet = make_packet(tmp_path, max_text_bytes=8192, fragment=b"<p>" + b"z" * 20000 + b"</p>")
    assert "evidence_text_exceeds_configured_packet_ceiling" in packet["eligibility"]["blockers"]
    assert packet["eligibility"]["provider_dispatch_eligible"] is False


def test_packet_is_deterministic(tmp_path):
    first = make_packet(tmp_path / "a")
    second = make_packet(tmp_path / "b")
    # Archive paths differ by design; identity is bound to evidence content.
    assert first["category_slot_count"] == second["category_slot_count"]
    assert [row["category_id"] for row in first["category_slots"]] == [
        row["category_id"] for row in second["category_slots"]
    ]


def test_immutable_write_rejects_a_differing_body(tmp_path):
    target = tmp_path / "artifact.json"
    PACKETS.exclusive_write(target, b"one")
    PACKETS.exclusive_write(target, b"one")
    with pytest.raises(PACKETS.PacketBuildError, match="immutable artifact collision"):
        PACKETS.exclusive_write(target, b"two")


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def prepared(tmp_path, **kwargs):
    return RUNNER.prepare_packet(make_packet(tmp_path, **kwargs))


def model_output(prep, *, disposition="include", nexus="affirmative_material_nexus", **over):
    ids = [item for item in prep["envelope"]["allowed_evidence_ids"]]
    return {
        "category_slots": [
            {
                "category_id": category_id,
                "title_reviewed": True,
                "summary_status_reviewed": True,
                "full_text_reviewed": True,
                "absence_is_neutral": True,
                "nexus_finding": nexus if category_id in prep["query_ids"] else "no_affirmative_evidence",
                "basis": "Operative provision caps interchange fees for covered institutions.",
                "evidence_ids": ["official_title"],
            }
            for category_id in prep["category_ids"]
        ],
        "memberships": [
            {
                "query_id": query_id,
                "proposed_disposition": disposition,
                "confidence": over.get("confidence", "high"),
                "reason": "The operative provision materially reaches credit unions.",
                "reason_code": "operative_provision_material_nexus",
                "evidence_ids": over.get("evidence_ids", ["official_title", "congress_gov_full_text"]),
                "full_text_rule_out_complete": over.get("full_text_rule_out_complete", True),
                "absence_used_as_exclusion_evidence": over.get("absence_used", False),
                "matched_category_basis": "operative provision",
            }
            for query_id in prep["query_ids"]
        ],
        "uncertainty": "",
    }


def test_prepare_packet_requires_nineteen_slots(tmp_path):
    packet = make_packet(tmp_path)
    packet["category_slots"] = packet["category_slots"][:18]
    with pytest.raises(RUNNER.AdjudicationError, match="nineteen category slots"):
        RUNNER.prepare_packet(packet)


def test_validate_model_output_rejects_unknown_evidence(tmp_path):
    prep = prepared(tmp_path)
    output = model_output(prep)
    output["memberships"][0]["evidence_ids"] = ["press_article:1"]
    with pytest.raises(RUNNER.AdjudicationError, match="unknown evidence"):
        RUNNER.validate_model_output(output, prep)


def test_validate_model_output_rejects_a_missing_membership(tmp_path):
    prep = prepared(tmp_path)
    output = model_output(prep)
    output["memberships"] = output["memberships"][:1]
    with pytest.raises(RUNNER.AdjudicationError, match="membership count differs"):
        RUNNER.validate_model_output(output, prep)


def test_exclude_without_all_version_review_is_downgraded(tmp_path):
    prep = prepared(tmp_path, expected_versions=2, objects_present=False)
    output = model_output(
        prep,
        disposition="exclude_false_positive",
        nexus="affirmative_false_positive",
        evidence_ids=["congress_gov_full_text"],
    )
    rows, downgrades = RUNNER.policy_effective_memberships(output, prep)
    assert all(row["proposed_disposition"] == "flagged" for row in rows)
    assert (
        "exclude_requires_complete_official_operative_text_review"
        in downgrades
    )


def test_exclude_with_all_version_review_survives(tmp_path):
    prep = prepared(tmp_path, expected_versions=2, objects_present=True)
    output = model_output(
        prep,
        disposition="exclude_false_positive",
        nexus="affirmative_false_positive",
        evidence_ids=["congress_gov_full_text"],
    )
    rows, downgrades = RUNNER.policy_effective_memberships(output, prep)
    assert all(row["proposed_disposition"] == "exclude_false_positive" for row in rows)
    assert downgrades == {}


def test_exclude_using_absence_is_downgraded(tmp_path):
    prep = prepared(tmp_path, expected_versions=2, objects_present=True)
    output = model_output(
        prep,
        disposition="exclude_false_positive",
        nexus="affirmative_false_positive",
        evidence_ids=["congress_gov_full_text"],
        absence_used=True,
    )
    rows, downgrades = RUNNER.policy_effective_memberships(output, prep)
    assert all(row["proposed_disposition"] == "flagged" for row in rows)
    assert "absence_can_never_support_an_exclusion" in downgrades


def test_include_without_affirmative_nexus_is_downgraded(tmp_path):
    prep = prepared(tmp_path)
    output = model_output(prep, nexus="no_affirmative_evidence")
    rows, downgrades = RUNNER.policy_effective_memberships(output, prep)
    assert all(row["proposed_disposition"] == "flagged" for row in rows)
    assert "include_requires_affirmative_material_nexus" in downgrades


def test_include_cannot_use_summary_unavailability_as_affirmative_evidence(tmp_path):
    prep = prepared(tmp_path)
    prep["envelope"]["allowed_evidence_ids"].append("official_summary_unavailable")
    output = model_output(
        prep,
        evidence_ids=["official_summary_unavailable"],
    )
    for slot in output["category_slots"]:
        if slot["category_id"] in prep["query_ids"]:
            slot["evidence_ids"] = ["official_summary_unavailable"]
    rows, downgrades = RUNNER.policy_effective_memberships(output, prep)
    assert all(row["proposed_disposition"] == "flagged" for row in rows)
    assert "include_requires_an_official_source_citation" in downgrades


def test_include_requires_complete_category_review_attestations(tmp_path):
    prep = prepared(tmp_path)
    output = model_output(prep)
    for slot in output["category_slots"]:
        if slot["category_id"] in prep["query_ids"]:
            slot["full_text_reviewed"] = False
    rows, downgrades = RUNNER.policy_effective_memberships(output, prep)
    assert all(row["proposed_disposition"] == "flagged" for row in rows)
    assert "include_requires_complete_review_attestations" in downgrades


def test_include_requires_complete_official_dispositive_evidence(tmp_path):
    prep = prepared(tmp_path)
    prep["eligibility"]["include_evidence_complete"] = False
    output = model_output(prep)
    rows, downgrades = RUNNER.policy_effective_memberships(output, prep)
    assert all(row["proposed_disposition"] == "flagged" for row in rows)
    assert (
        "include_requires_complete_official_dispositive_evidence"
        in downgrades
    )


def test_proposal_rows_are_nineteen_plus_every_membership(tmp_path):
    prep = prepared(tmp_path)
    output = model_output(prep)
    rows = RUNNER.proposal_rows(
        prep,
        effective_slots=output["category_slots"],
        effective_memberships=output["memberships"],
        run_id="t",
        model="claude-opus-5",
        effort="max",
        decision_source="automated_test",
    )
    assert len(rows) == 19 + prep["membership_count"]
    categories_out = [r for r in rows if r["schema"] == RUNNER.CATEGORY_PROPOSAL_SCHEMA]
    memberships_out = [r for r in rows if r["schema"] == RUNNER.MEMBERSHIP_PROPOSAL_SCHEMA]
    assert len(categories_out) == 19
    assert len(memberships_out) == prep["membership_count"]
    assert all(r["automated_final_decision"] is False for r in rows)
    assert all(r["adjudication_state"] == RUNNER.PROPOSED_STATE for r in rows)
    assert all(r["adjudication_state"] not in RUNNER.FORBIDDEN_STATES for r in rows)
    assert all(r["human_ratification_required"] is True for r in rows)
    assert all(r["measure_weight"] == 1.0 for r in rows)
    assert all(r["amendment_weight"] is None for r in rows)


def test_amendment_proposal_rows_carry_fixed_half_weight(tmp_path):
    evidence = evidence_row(summary_settled=False, expected_versions=0)
    evidence["canonical_key"] = "119:samdt:1"
    evidence["official_evidence"]["title"]["source_url"] = (
        "https://www.congress.gov/amendment/119th-congress/senate-amendment/1"
    )
    evidence["official_evidence"]["full_text"] = {
        "status": "missing",
        "version_inventory": {"expected_version_count": 0},
    }
    worksheet = {
        **worksheet_row(expected_versions=0),
        "canonical_key": "119:samdt:1",
        "display_number": "S.Amdt.1",
        "measure_type": "samdt",
        "record_kind": "amendment",
    }
    packet = PACKETS.build_packet(
        worksheet=worksheet,
        evidence=evidence,
        memberships=[
            {**row, "canonical_key": "119:samdt:1"}
            for row in membership_rows()
        ],
        categories=categories(),
        extraction=None,
        govinfo_receipts=[],
        billstatus=None,
        original_method=amendment_method_stub(
            "At the end add a credit-union interchange exemption."
        ),
        max_text_bytes=600_000,
    )
    prep = RUNNER.prepare_packet(packet)
    output = model_output(prep)
    rows = RUNNER.proposal_rows(
        prep,
        effective_slots=output["category_slots"],
        effective_memberships=output["memberships"],
        run_id="amendment-test",
        model="claude-opus-5",
        effort="max",
        decision_source="automated_test",
    )
    assert all(row["measure_weight"] == 0.5 for row in rows)
    assert all(row["amendment_weight"] == 0.5 for row in rows)


def test_flagged_output_covers_every_slot_and_membership(tmp_path):
    prep = prepared(tmp_path, summary_settled=False)
    output = RUNNER.flagged_output(prep, ["summary unsettled"])
    assert len(output["category_slots"]) == 19
    assert len(output["memberships"]) == prep["membership_count"]
    assert all(row["proposed_disposition"] == "flagged" for row in output["memberships"])
    assert all(row["nexus_finding"] == "no_affirmative_evidence" for row in output["category_slots"])


class _StubTransport:
    def __init__(self, output, *, cost=Decimal("0.01")):
        self.output = output
        self.cost = cost
        self.calls = 0
        self.kwargs = []

    def review(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        return RUNNER.ProviderResult(
            output=self.output,
            response_metadata={"provider_response_id": "stub"},
            input_tokens=1000,
            output_tokens=500,
            cost_usd=self.cost,
        )


def _run_args(tmp_path, packets_path, **over):
    argv = [
        "--packets", str(packets_path),
        "--out", str(tmp_path / "out"),
        "--run-id", over.pop("run_id", "t1"),
        "--max-cost-usd", over.pop("max_cost", "15"),
        "--model", "claude-opus-5",
        "--effort", "max",
        "--workers", "1",
    ]
    for key, value in over.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return RUNNER.parse_args(argv)


def _write_packets(tmp_path, packets) -> Path:
    path = tmp_path / "packets.ndjson"
    path.write_bytes(b"".join(RUNNER.canonical_bytes(p) for p in packets))
    return path


def test_incomplete_candidate_is_flagged_without_a_provider_call(tmp_path):
    packets_path = _write_packets(tmp_path, [make_packet(tmp_path / "p", summary_settled=False)])
    args = _run_args(tmp_path, packets_path)
    transport = _StubTransport({})
    summary = RUNNER.run(args, transport=transport)
    assert transport.calls == 0
    assert summary["provider_calls_performed"] == 0
    assert summary["effective_dispositions"]["flagged"] == 2
    assert summary["category_proposal_count"] == 19
    assert summary["membership_proposal_count"] == 2
    assert summary["automated_final_decision"] is False
    assert summary["main_corpus_write_performed"] is False


def test_dry_run_makes_no_call_and_leaves_eligible_rows_open(tmp_path):
    packets_path = _write_packets(tmp_path, [make_packet(tmp_path / "p")])
    args = _run_args(tmp_path, packets_path)
    args.dry_run = True
    summary = RUNNER.run(args, transport=_StubTransport({}))
    assert summary["dry_run"] is True
    assert summary["provider_calls_performed"] == 0
    assert summary["dry_run_eligible_not_dispatched"] == ["119:hr:1"]


def test_provider_call_ceiling_stops_before_the_next_call(tmp_path):
    packets = []
    for index in range(3):
        packet = make_packet(tmp_path / f"p{index}")
        packet["canonical_key"] = f"119:hr:{index + 1}"
        packet["evidence_sha256"] = hashlib.sha256(str(index).encode()).hexdigest()
        packets.append(packet)
    packets_path = _write_packets(tmp_path, packets)
    args = _run_args(tmp_path, packets_path, max_provider_calls=2)
    prep = RUNNER.prepare_packet(packets[0])
    transport = _StubTransport(model_output(prep))
    summary = RUNNER.run(args, transport=transport)
    assert transport.calls == 2
    reservation = RUNNER.make_job(RUNNER.prepare_packet(packets[0]), args).reservation
    assert all(
        call["remaining_budget_usd"] == reservation for call in transport.kwargs
    )
    assert summary["provider_calls_dispatched_this_run"] == 2
    assert summary["terminal_statuses"].get("provider_call_ceiling_flagged") == 1


def test_minimum_claude_call_budget_is_reserved_and_passed_to_transport(tmp_path):
    packet = make_packet(tmp_path / "p")
    packets_path = _write_packets(tmp_path, [packet])
    args = _run_args(
        tmp_path,
        packets_path,
        provider="claude-cli",
        minimum_claude_call_budget_usd="2",
    )
    transport = _StubTransport(model_output(RUNNER.prepare_packet(packet)))
    summary = RUNNER.run(args, transport=transport)
    assert transport.calls == 1
    assert transport.kwargs[0]["remaining_budget_usd"] == Decimal("2")
    assert summary["minimum_claude_call_budget_usd"] == "2.000000"


def test_provider_cost_above_call_budget_fails_closed(tmp_path):
    packet = make_packet(tmp_path / "p")
    packets_path = _write_packets(tmp_path, [packet])
    args = _run_args(
        tmp_path,
        packets_path,
        provider="claude-cli",
        minimum_claude_call_budget_usd="2",
    )
    transport = _StubTransport(
        model_output(RUNNER.prepare_packet(packet)),
        cost=Decimal("2.01"),
    )
    summary = RUNNER.run(args, transport=transport)
    assert summary["terminal_statuses"] == {"provider_failed_flagged": 1}
    assert summary["accounted_cost_usd"] == "2.000000"


def test_cli_transport_is_tool_free_and_uses_the_supplied_call_cap():
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(
            {
                "structured_output": {"ok": True},
                "session_id": "session",
                "subtype": "success",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "total_cost_usd": 0.01,
            }
        ),
        stderr="",
    )
    with patch.object(RUNNER.subprocess, "run", return_value=completed) as invoked:
        result = RUNNER.ClaudeCliTransport("claude").review(
            system_prompt="system",
            user_prompt="evidence",
            schema={"type": "object"},
            model="claude-opus-5",
            effort="max",
            max_output_tokens=6000,
            remaining_budget_usd=Decimal("0.525"),
        )
    command = invoked.call_args.args[0]
    assert "--safe-mode" in command
    assert "--disable-slash-commands" in command
    assert command[command.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in command
    assert command[command.index("--max-budget-usd") + 1] == "0.525000"
    assert invoked.call_args.kwargs["input"] == "evidence"
    assert invoked.call_args.kwargs["shell"] is False
    assert result.output == {"ok": True}


def test_call_start_without_receipt_is_never_replayed(tmp_path):
    packets_path = _write_packets(tmp_path, [make_packet(tmp_path / "p")])
    args = _run_args(tmp_path, packets_path)
    jobs = [RUNNER.make_job(RUNNER.prepare_packet(p), args) for p in RUNNER.iter_packets(packets_path)]
    run_dir = args.out.resolve() / "runs" / args.run_id
    paths = RUNNER.artifact_paths(run_dir, jobs[0].dispatch_id)
    RUNNER.exclusive_write(paths["call"], {"schema": RUNNER.CALL_SCHEMA, "prior": True})
    transport = _StubTransport({})
    summary = RUNNER.run(args, transport=transport)
    assert transport.calls == 0
    assert summary["terminal_statuses"].get("indeterminate_prior_call_flagged") == 1


def test_completed_receipt_replay_is_artifact_idempotent(tmp_path):
    packet = make_packet(tmp_path / "p")
    packets_path = _write_packets(tmp_path, [packet])
    args = _run_args(tmp_path, packets_path)
    transport = _StubTransport(model_output(RUNNER.prepare_packet(packet)))
    first = RUNNER.run(args, transport=transport)
    second = RUNNER.run(args, transport=transport)
    assert transport.calls == 1
    assert first["proposal_generation_sha256"] == second["proposal_generation_sha256"]


def test_dispatch_identity_binds_evidence_eligibility(tmp_path):
    packet = make_packet(tmp_path / "p")
    changed = json.loads(json.dumps(packet))
    changed["eligibility"]["include_evidence_complete"] = False
    packets_path = _write_packets(tmp_path, [packet])
    args = _run_args(tmp_path, packets_path)
    original = RUNNER.make_job(RUNNER.prepare_packet(packet), args)
    restricted = RUNNER.make_job(RUNNER.prepare_packet(changed), args)
    assert original.prepared["evidence_sha256"] != restricted.prepared["evidence_sha256"]
    assert original.prompt_sha256 != restricted.prompt_sha256
    assert original.dispatch_id != restricted.dispatch_id


def test_receipt_replay_reapplies_current_policy(tmp_path):
    packet = make_packet(tmp_path / "p")
    packets_path = _write_packets(tmp_path, [packet])
    args = _run_args(tmp_path, packets_path)
    prep = RUNNER.prepare_packet(packet)
    job = RUNNER.make_job(prep, args)
    RUNNER.run(args, transport=_StubTransport(model_output(prep)))
    receipt_path = RUNNER.artifact_paths(
        args.out.resolve() / "runs" / args.run_id,
        job.dispatch_id,
    )["receipt"]
    job.prepared["eligibility"]["include_evidence_complete"] = False
    with pytest.raises(RUNNER.AdjudicationError, match="policy-current"):
        RUNNER.verify_existing_receipt(job, receipt_path)


def test_receipt_replay_rejects_semantically_forged_measure_weight(tmp_path):
    packet = make_packet(tmp_path / "p")
    packets_path = _write_packets(tmp_path, [packet])
    args = _run_args(tmp_path, packets_path)
    prep = RUNNER.prepare_packet(packet)
    job = RUNNER.make_job(prep, args)
    RUNNER.run(args, transport=_StubTransport(model_output(prep)))
    receipt_path = RUNNER.artifact_paths(
        args.out.resolve() / "runs" / args.run_id,
        job.dispatch_id,
    )["receipt"]
    receipt = RUNNER.read_json(receipt_path)
    for row in receipt["proposal_rows"]:
        row["measure_weight"] = 0.5
        row["amendment_weight"] = 0.5
    receipt["proposal_rows_sha256"] = RUNNER.sha256_bytes(
        b"".join(
            RUNNER.canonical_bytes(row) for row in receipt["proposal_rows"]
        )
    )
    receipt_path.write_bytes(RUNNER.canonical_bytes(receipt))
    with pytest.raises(RUNNER.AdjudicationError, match="semantically current"):
        RUNNER.verify_existing_receipt(job, receipt_path)


def test_no_call_ceiling_receipts_can_be_resumed_in_a_new_run(tmp_path):
    packets = []
    for index in range(3):
        packet = make_packet(tmp_path / f"r{index}")
        packet["canonical_key"] = f"119:hr:{index + 1}"
        packet["evidence_sha256"] = hashlib.sha256(str(index).encode()).hexdigest()
        packets.append(packet)
    packets_path = _write_packets(tmp_path, packets)
    first_args = _run_args(
        tmp_path,
        packets_path,
        run_id="first",
        max_provider_calls=1,
    )
    first_transport = _StubTransport(model_output(RUNNER.prepare_packet(packets[0])))
    first = RUNNER.run(first_args, transport=first_transport)
    assert first["terminal_statuses"]["provider_call_ceiling_flagged"] == 2

    second_args = _run_args(tmp_path, packets_path, run_id="second")
    second_args.retry_no_call_run = (
        first_args.out.resolve() / "runs" / first_args.run_id
    )
    second_transport = _StubTransport(model_output(RUNNER.prepare_packet(packets[0])))
    second = RUNNER.run(second_args, transport=second_transport)
    assert second["selected_count"] == 2
    assert second["provider_calls_dispatched_this_run"] == 2
    assert second_transport.calls == 2


def test_prior_call_run_excludes_every_started_candidate(tmp_path):
    packets = []
    for index in range(2):
        packet = make_packet(tmp_path / f"x{index}")
        packet["canonical_key"] = f"119:hr:{index + 1}"
        packet["evidence_sha256"] = hashlib.sha256(str(index).encode()).hexdigest()
        packets.append(packet)
    packets_path = _write_packets(tmp_path, packets)
    packets_sha = RUNNER.sha256_file(packets_path)
    prior = tmp_path / "prior"
    RUNNER.exclusive_write(
        prior / "run_manifest.json",
        {"schema": RUNNER.RUN_SCHEMA, "packets_sha256": packets_sha},
    )
    RUNNER.exclusive_write(
        prior / "call_starts" / "one.json",
        {"schema": RUNNER.CALL_SCHEMA, "canonical_key": "119:hr:1"},
    )

    args = _run_args(tmp_path, packets_path)
    args.exclude_prior_call_run = [prior]
    transport = _StubTransport(model_output(RUNNER.prepare_packet(packets[1])))
    summary = RUNNER.run(args, transport=transport)
    assert transport.calls == 1
    assert summary["selected_count"] == 1
    assert summary["excluded_prior_call_key_count"] == 1
    manifest = RUNNER.read_json(
        args.out.resolve() / "runs" / args.run_id / "run_manifest.json"
    )
    assert manifest["excluded_prior_call_key_count"] == 1
    assert manifest["excluded_prior_call_runs"] == [str(prior.resolve())]


def test_canary_selection_is_deterministic_and_meets_quotas(tmp_path):
    packets = []
    for index in range(30):
        packet = make_packet(
            tmp_path / f"c{index}",
            expected_versions=2 if index % 4 == 0 else 1,
            objects_present=False,
        )
        packet["canonical_key"] = f"119:hr:{100 + index}"
        packet["evidence_sha256"] = hashlib.sha256(str(index).encode()).hexdigest()
        if index % 3 == 0:
            packet["memberships"] = packet["memberships"][:1]
        packets.append(packet)
    packets_path = _write_packets(tmp_path, packets)
    args = _run_args(tmp_path, packets_path, canary_size=25)
    jobs = [RUNNER.make_job(RUNNER.prepare_packet(p), args) for p in RUNNER.iter_packets(packets_path)]
    first = RUNNER.select_canary(jobs, size=25, max_prompt_bytes=args.max_prompt_bytes)
    second = RUNNER.select_canary(jobs, size=25, max_prompt_bytes=args.max_prompt_bytes)
    assert first["canonical_keys"] == second["canonical_keys"]
    assert len(first["canonical_keys"]) == 25
    assert len(first["quota_multi_version"]) == 5
    assert len(first["quota_multi_membership"]) == 5
    by_key = {job.prepared["canonical_key"]: job for job in jobs}
    assert all(not by_key[key].prepared["blockers"] for key in first["canonical_keys"])
    assert all(
        by_key[key].prompt_bytes <= args.max_prompt_bytes for key in first["canonical_keys"]
    )


def test_reservation_is_conservative_and_cap_is_enforced(tmp_path):
    prep = prepared(tmp_path)
    args = _run_args(tmp_path, tmp_path / "packets.ndjson")
    job = RUNNER.make_job(prep, args)
    assert job.reservation > Decimal("0")
    naive = RUNNER.estimated_reservation(
        user_prompt=job.user_prompt,
        max_output_tokens=args.max_output_tokens,
        input_cost_per_million=args.input_rate,
        output_cost_per_million=args.output_rate,
        bytes_per_token=Decimal("1"),
    )
    assert naive > job.reservation

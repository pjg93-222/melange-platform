import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load_module(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = load_module("leadership_era_ledger", "fec/members/build_member_narrative_ledger.py")
playbooks = load_module("leadership_era_playbooks", "fec/members/build_member_meeting_playbooks.py")


def test_leadership_source_era_is_strictly_cut_at_current_role_start():
    roles = {"Leader": {"current_role_assumed_on": "2023-01-03"}}
    assert ledger.leadership_source_era("Leader", "2022-12-31", roles) == "PRE_CURRENT_LEADERSHIP_ROLE"
    assert ledger.leadership_source_era("Leader", "2023-01-03", roles) == "CURRENT_ROLE_CONDITIONED"
    assert ledger.leadership_source_era("Member", "2025-01-01", roles) == "NOT_CURRENT_FORMAL_LEADERSHIP"


def test_role_conditioned_relationship_finding_is_labeled_in_playbook():
    record = {
        "speech_profile": {
            "longform_interview_evidence": {
                "baseline_reviewed_semantic_findings": [],
                "role_conditioned_reviewed_semantic_findings": [
                    {"kind": "individual_conversation_before_decision"}
                ],
            }
        }
    }
    message, derivation = playbooks.opening(record)
    assert derivation == "role_conditioned_longform_relationship_sequence"
    assert "current-role interviews" in message
    assert "pre-role audio" in message


def test_reviewed_relationship_opening_is_concrete_and_bounded():
    record = {
        "speech_profile": {
            "longform_interview_evidence": {
                "baseline_reviewed_semantic_findings": [
                    {
                        "kind": "personal_checkin_before_policy",
                        "meeting_implication": "A person-first opening is supported.",
                    }
                ],
            }
        }
    }
    message, derivation = playbooks.opening(record)
    assert derivation == "reviewed_longform_relationship_sequence"
    assert "Do not start cold" in message
    assert "first minute" in message
    assert "let the member answer" in message
    assert "then pivot" in message
    assert "[action]" in message and "[date]" in message


def test_reviewed_turn_duration_replaces_overlapping_generic_pacing():
    record = {
        "speech_profile": {
            "interaction_habits": {"measures": {}},
            "longform_interview_evidence": {
                "full_turns": [{"duration_seconds": 71, "semantic_review_qualified": True}],
                "baseline_reviewed_semantic_findings": [],
            },
        }
    }
    message, rules = playbooks.room_pacing(record)
    assert message.casefold().count("ask one question at a time") == 1
    assert rules == ["reviewed_longform_one_minute_turn"]


def test_explicit_meeting_phase_routes_reviewed_implication_to_playbook():
    record = {
        "speech_profile": {
            "longform_interview_evidence": {
                "baseline_reviewed_semantic_findings": [{
                    "kind": "asks_for_written_cost_detail",
                    "meeting_phase": "bring",
                    "meeting_implication": "Put the cost assumption and its source on page one.",
                }],
            }
        },
        "behavior": {},
    }
    message, derivation = playbooks.leave_behind(record)
    assert "Put the cost assumption and its source on page one." in message
    assert "reviewed_longform_bring:asks_for_written_cost_detail" in derivation


def test_generic_reviewed_opening_still_contains_explicit_ask_and_date():
    record = {
        "speech_profile": {
            "longform_interview_evidence": {
                "baseline_reviewed_semantic_findings": [{
                    "kind": "district_example_before_policy",
                    "meeting_phase": "open",
                    "meeting_implication": "Begin with one district example before the policy mechanics.",
                }],
            }
        }
    }
    message, derivation = playbooks.opening(record)
    assert "district example" in message
    assert "[action]" in message and "[date]" in message
    assert derivation == "reviewed_longform_open:district_example_before_policy"


def test_visible_longform_example_uses_prerole_turn_for_current_leader():
    record = {
        "leadership_audio_source_policy": {"current_role": "Leader"},
        "speech_profile": {"longform_interview_evidence": {"full_turns": [
            {
                "turn_id": "post", "source_id": "post", "title": "Post-role",
                "source_era": "CURRENT_ROLE_CONDITIONED", "duration_seconds": 300,
                "semantic_review_qualified": True, "baseline_semantic_eligible": False,
                "semantic_findings": [{"finding": "Post-role finding"}],
            },
            {
                "turn_id": "pre", "source_id": "pre", "title": "Pre-role",
                "source_era": "PRE_CURRENT_LEADERSHIP_ROLE", "duration_seconds": 80,
                "semantic_review_qualified": True, "baseline_semantic_eligible": True,
                "semantic_findings": [{
                    "finding": "Pre-role finding", "meeting_implication": "Use one question.",
                    "scope": "one turn",
                }],
            },
        ]}},
    }
    example = playbooks.observed_longform(record)
    assert example["turn_id"] == "pre"
    assert example["summary"] == "One complete reviewed answer ran 80 seconds from floor-taking to the next speaker handoff."
    assert example["findings"][0]["finding"] == "Pre-role finding"


def test_active_evidence_separates_promoted_and_pending_prerole_sources():
    evidence = json.loads(
        (ROOT / "data/members/member_narrative_evidence_v4.json").read_text(encoding="utf-8")
    )
    assert evidence["counts"]["formal_leadership_profiles_with_source_era_policy"] == 27
    assert evidence["counts"]["leadership_prerole_sources_acquired_pending_promotion"] == 26
    assert evidence["counts"]["leadership_prerole_sources_identity_verified_candidate"] == 2
    assert evidence["counts"]["leadership_prerole_sources_human_review_promoted"] == 0
    mike = evidence["members"]["Mike Johnson"]["leadership_audio_source_policy"]
    steve = evidence["members"]["Steve Scalise"]["leadership_audio_source_policy"]
    durbin = evidence["members"]["Richard Durbin"]["leadership_audio_source_policy"]
    assert mike["pre_role_source"]["recorded_at"] == "2022-03-24"
    assert mike["pre_role_source"]["identity_authority"] is True
    assert mike["pre_role_source"]["status"] == "SOURCE_IDENTITY_VERIFIED_SEMANTIC_REVIEW_PENDING"
    assert mike["pre_role_source"]["profile_effect"] == "NONE_UNTIL_FULL_PROMOTION"
    assert mike["pre_role_source"]["candidate_turn_count"] == 1
    assert mike["pre_role_source"]["reviewed_turn_count"] == 0
    baseline = evidence["members"]["Mike Johnson"]["speech_profile"][
        "longform_interview_evidence"
    ]["baseline_reviewed_semantic_findings"]
    assert baseline == []
    assert steve["verified_post_role_source_count"] == 2
    assert steve["pre_role_source"]["identity_authority"] is True
    steve_baseline = evidence["members"]["Steve Scalise"]["speech_profile"][
        "longform_interview_evidence"
    ]["baseline_reviewed_semantic_findings"]
    assert steve_baseline == []
    for name in ("Mike Johnson", "Steve Scalise"):
        turns = evidence["members"][name]["speech_profile"]["longform_interview_evidence"]["full_turns"]
        assert turns and all(turn["semantic_review_qualified"] is False for turn in turns)
    assert durbin["pre_role_source"]["status"] == "HISTORICAL_LONGFORM_SOURCE_GAP"

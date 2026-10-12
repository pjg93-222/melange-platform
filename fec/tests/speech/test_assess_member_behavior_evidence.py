from copy import deepcopy
from itertools import permutations

import pytest

from fec.speech.assess_member_behavior_evidence import assess_behavior


def observation(index, **changes):
    audio_hash, transcript_hash = f"{index:064x}", f"{index + 100:064x}"
    row = {
        "observation_id": f"obs-{index}", "member": "Example Member",
        "behavior_code": "member_initiated_social_question", "polarity": "supports",
        "source_kind": "interview", "initiated_by": "member", "elicitation": "spontaneous",
        "source_id": f"source-{index}", "production_episode_id": f"episode-{index}",
        "independence_group_id": f"production-{index}", "producer_id": "same-publisher",
        "setting": f"setting-{index}", "source_audio_sha256": audio_hash,
        "transcript_sha256": transcript_hash,
        "review": {
            "reviewer_type": "human", "reviewer_id": "reviewer-1", "decision": "accepted",
            "reviewed_at": "2026-09-04T12:00:00Z", "source_audio_sha256": audio_hash,
            "transcript_sha256": transcript_hash, "production_independence_reviewed": True,
            "setting_reviewed": True,
        },
    }
    row.update(changes)
    return row


def assess(rows, bindings=None):
    if bindings is None:
        bindings = {row["source_id"]: {field: row[field] for field in (
            "source_audio_sha256", "transcript_sha256"
        )} for row in rows}
    return assess_behavior(rows, member="Example Member", behavior_code="member_initiated_social_question",
                           initiated_by="member", elicitation="spontaneous", source_bindings=bindings)


def test_two_independent_productions_different_settings_can_share_publisher():
    result = assess([observation(1), observation(2)])
    assert result["status"] == "RECURRING_PUBLIC_PATTERN"
    assert result["independent_supporting_pairs"] == [["obs-1", "obs-2"]]
    assert result["profile_authority"] is False
    assert result["automatic_promotion"] is False


@pytest.mark.parametrize("same_field", ["production_episode_id", "independence_group_id", "setting", "source_audio_sha256", "transcript_sha256"])
def test_reuploads_one_event_or_one_setting_never_establish_recurrence(same_field):
    first, second = observation(1), observation(2)
    second[same_field] = first[same_field]
    if same_field in {"source_audio_sha256", "transcript_sha256"}:
        second["review"][same_field] = first[same_field]
    result = assess([first, second])
    assert result["status"] == "PROVISIONAL"
    assert result["nonindependent_supporting_pairs"][0]["shared_fields"] == [same_field]


def test_duplicate_identical_rows_count_once():
    row = observation(1)
    result = assess([row, deepcopy(row)])
    assert result["status"] == "PROVISIONAL"
    assert result["counts"]["unique_observations"] == 1


@pytest.mark.parametrize("field", ["setting", "independence_group_id", "producer_id", "production_episode_id"])
def test_unknown_context_stays_provisional(field):
    second = observation(2, **{field: "unknown"})
    result = assess([observation(1), second])
    assert result["status"] == "PROVISIONAL"
    assert result["supporting_observation_ids"] == ["obs-1"]
    assert field.upper() + "_MISSING" in result["excluded_observations"][0]["reasons"]


@pytest.mark.parametrize("field", ["setting_reviewed", "production_independence_reviewed"])
def test_context_requires_explicit_review(field):
    second = observation(2)
    second["review"][field] = False
    assert assess([observation(1), second])["status"] == "PROVISIONAL"


def test_host_elicited_response_is_not_pooled_with_member_initiated_question():
    prompted = observation(2, initiated_by="interviewer", elicitation="prompted")
    result = assess([observation(1), prompted])
    assert result["status"] == "PROVISIONAL"
    assert result["supporting_observation_ids"] == ["obs-1"]
    assert "INITIATION_OR_ELICITATION_UNKNOWN_OR_MISMATCHED" in result["excluded_observations"][0]["reasons"]
    assert result["reviewed_interview_observation_ids"] == ["obs-1"]


def test_only_host_elicited_response_is_no_reviewed_observation_of_member_initiation():
    result = assess([observation(1, initiated_by="interviewer", elicitation="prompted")])
    assert result["status"] == "NO_REVIEWED_OBSERVATION"
    assert result["reviewed_interview_observation_ids"] == []
    assert result["counts"]["reviewed_interview_observations"] == 0
    assert "INITIATION_OR_ELICITATION_UNKNOWN_OR_MISMATCHED" in result["excluded_observations"][0]["reasons"]


def test_secondhand_report_never_counts_even_if_review_fields_were_supplied():
    report = observation(2, source_kind="field_report", report_basis="secondhand",
                         reported_frequency=None, reported_date=None)
    result = assess([observation(1), report])
    assert result["status"] == "PROVISIONAL"
    assert result["supporting_observation_ids"] == ["obs-1"]
    assert assess([report])["status"] == "NO_REVIEWED_OBSERVATION"


def test_contradiction_is_visible_and_blocks_unqualified_recurrence():
    result = assess([observation(1), observation(2), observation(3, polarity="contradicts")])
    assert result["status"] == "MIXED_OR_CONTEXT_DEPENDENT"
    assert result["supporting_observation_ids"] == ["obs-1", "obs-2"]
    assert result["contradicting_observation_ids"] == ["obs-3"]
    assert result["counts"]["independent_supporting_pairs"] == 1


@pytest.mark.parametrize("missing_field", ["setting", "independence_group_id"])
def test_accepted_contradiction_with_missing_context_still_blocks_recurrence(missing_field):
    contradiction = observation(3, polarity="contradicts", **{missing_field: "unknown"})
    result = assess([observation(1), observation(2), contradiction])
    assert result["status"] == "MIXED_OR_CONTEXT_DEPENDENT"
    assert result["contradicting_observation_ids"] == ["obs-3"]
    assert result["contradicting_observation_ids_needing_context"] == ["obs-3"]
    assert result["counts"]["reviewed_target_contradicting_observations"] == 1
    assert result["counts"]["qualifying_contradicting_observations"] == 0
    assert result["counts"]["independent_supporting_pairs"] == 1
    assert "CONTRADICTORY_OBSERVATION_CONTEXT_INCOMPLETE" in result["reasons"]
    assert missing_field.upper() + "_MISSING" in result["excluded_observations"][0]["reasons"]


def test_contradictory_host_elicited_response_does_not_contradict_member_initiation():
    unrelated = observation(3, polarity="contradicts", initiated_by="interviewer", elicitation="prompted")
    result = assess([observation(1), observation(2), unrelated])
    assert result["status"] == "RECURRING_PUBLIC_PATTERN"
    assert result["contradicting_observation_ids"] == []
    assert result["reviewed_interview_observation_ids"] == ["obs-1", "obs-2"]


def test_legacy_review_label_without_provenance_is_not_promoted():
    row = observation(1, status="HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED", review={})
    result = assess([row])
    assert result["status"] == "NO_REVIEWED_OBSERVATION"
    assert "ACCEPTED_HUMAN_REVIEW_PROVENANCE_MISSING" in result["excluded_observations"][0]["reasons"]


def test_malformed_review_cannot_be_treated_as_human_review():
    assert assess([observation(1, review=["human"])])["status"] == "NO_REVIEWED_OBSERVATION"


@pytest.mark.parametrize("field", ["source_audio_sha256", "transcript_sha256"])
def test_stale_review_or_current_artifact_binding_fails_closed(field):
    first, second = observation(1), observation(2)
    second["review"][field] = "f" * 64
    result = assess([first, second])
    assert result["status"] == "PROVISIONAL"
    assert field.upper() + "_BINDING_MISSING_OR_STALE" in result["excluded_observations"][0]["reasons"]
    assert assess([first], bindings={})["status"] == "NO_REVIEWED_OBSERVATION"


def test_order_independence():
    rows = [observation(1), observation(2), observation(3, polarity="contradicts"), observation(4, setting="unknown")]
    expected = assess(rows)
    assert all(assess(list(order)) == expected for order in permutations(rows))


def test_empty_observations_and_conflicting_ids():
    assert assess([])["status"] == "NO_REVIEWED_OBSERVATION"
    first, conflict = observation(1), observation(1, setting="different")
    with pytest.raises(ValueError, match="Conflicting observation_id"):
        assess([first, conflict])

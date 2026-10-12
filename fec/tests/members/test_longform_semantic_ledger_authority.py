import copy

import pytest

from fec.members import build_member_narrative_ledger as ledger
from fec.tests.speech import test_build_member_longform_turn_features as fixtures


def case(tmp_path, *, modern):
    item = fixtures.case(tmp_path, modern=modern)
    item.turn["source_era"] = "NOT_CURRENT_FORMAL_LEADERSHIP"
    findings = [{"kind": "response_length", "finding": "One complete public answer lasts several minutes.",
                 "scope": "One interview turn; not a recurring or private-meeting preference."}]
    item.turn["semantic_findings"] = copy.deepcopy(findings)
    if modern:
        item.chain_rows["reviews"]["semantic_findings"] = copy.deepcopy(findings)
    fixtures.save(item)
    return item


def annotated(turn, measured):
    return {**turn, **ledger.longform_semantic_authority(turn, measured)}


def test_legacy_semantics_are_preserved_provisionally_not_reviewed_or_baseline(tmp_path):
    item = case(tmp_path, modern=False)
    features = fixtures.build(item)
    turn = annotated(item.turn, features["turns"])
    groups = ledger.partition_longform_semantics([turn])
    assert turn["semantic_authority"] == "LEGACY_REVIEW_AUTHORITY_UNRESOLVED"
    assert turn["semantic_review_qualified"] is False
    assert not groups["reviewed_semantic_findings"]
    assert not groups["baseline_reviewed_semantic_findings"]
    assert not groups["role_conditioned_reviewed_semantic_findings"]
    retained = groups["provisional_semantic_findings"]
    assert len(retained) == 1
    assert retained[0]["finding"] == item.turn["semantic_findings"][0]["finding"]
    assert retained[0]["human_review_qualified"] is False
    assert retained[0]["baseline_semantic_eligible"] is False


def test_exact_qualified_measured_named_review_chain_admits_descriptive_semantics(tmp_path):
    item = case(tmp_path, modern=True)
    features = fixtures.build(item)
    turn = annotated(item.turn, features["turns"])
    assert features["turns"][0]["authority"] == "HASH_BOUND_NAMED_HUMAN_REVIEW"
    assert turn["semantic_review_qualified"] is True
    groups = ledger.partition_longform_semantics([turn])
    assert len(groups["reviewed_semantic_findings"]) == 1
    assert len(groups["baseline_reviewed_semantic_findings"]) == 1
    assert not groups["provisional_semantic_findings"]
    assert "meeting_implication" not in groups["reviewed_semantic_findings"][0]


def test_absent_measurements_never_infer_human_qualification_from_status(tmp_path):
    item = case(tmp_path, modern=True)
    turn = annotated(item.turn, [])
    assert turn["semantic_review_qualified"] is False
    groups = ledger.partition_longform_semantics([turn])
    assert not groups["reviewed_semantic_findings"]
    assert len(groups["provisional_semantic_findings"]) == 1


@pytest.mark.parametrize("key,value", [
    ("source_audio_sha256", "different-audio"), ("source_transcript_sha256", "different-words"),
    ("turn_audio_sha256", "different-turn"), ("start_sec", 3.0), ("end_sec", 184.0),
    ("source_id", "different-recording"), ("turn_id", "different-turn"),
    ("human_reviewer", "Different reviewer"), ("human_reviewed_at", "2026-09-05T00:00:00+00:00"),
    ("candidate_turn_binding_sha256", "different-binding"),
    ("semantic_findings", [{"kind": "invented", "finding": "Not the reviewed text", "scope": "One turn"}]),
])
def test_feature_cannot_qualify_a_different_source_boundary_review_or_semantic_row(tmp_path, key, value):
    item = case(tmp_path, modern=True)
    features = fixtures.build(item)
    changed = {**copy.deepcopy(item.turn), key: value}
    changed["row_sha256"] = fixtures.mod.row_hash({key: value for key, value in changed.items() if key != "row_sha256"})
    turn = annotated(changed, features["turns"])
    assert turn["semantic_review_qualified"] is False
    assert turn["semantic_authority"] != "HASH_BOUND_NAMED_HUMAN_REVIEW"
    assert turn["semantic_authority_limits"]
    assert not ledger.partition_longform_semantics([turn])["reviewed_semantic_findings"]


def test_qualified_role_conditioned_semantics_do_not_gain_baseline_authority(tmp_path):
    item = case(tmp_path, modern=True)
    item.turn["source_era"] = "CURRENT_ROLE_CONDITIONED"
    fixtures.json_file(item.leadership, {"members": {"Example Member": {"current_role_assumed_on": "2019-01-01"}}})
    fixtures.save(item)
    features = fixtures.build(item)
    turn = annotated(item.turn, features["turns"])
    assert turn["semantic_review_qualified"] is True
    groups = ledger.partition_longform_semantics([turn])
    assert len(groups["reviewed_semantic_findings"]) == 1
    assert len(groups["role_conditioned_reviewed_semantic_findings"]) == 1
    assert not groups["baseline_reviewed_semantic_findings"]


def test_quality_held_or_candidate_feature_remains_provisional(tmp_path):
    item = case(tmp_path, modern=True)
    features = fixtures.build(item)
    measured = {**features["turns"][0], "quality_hold": True, "profile_measurement_eligible": False,
                "baseline_measurement_eligible": False}
    turn = annotated(item.turn, [measured])
    assert turn["semantic_review_qualified"] is False
    assert len(ledger.partition_longform_semantics([turn])["provisional_semantic_findings"]) == 1

import json
from pathlib import Path

import pytest

from fec.members import build_member_narrative_ledger as ledger
from fec.speech import build_member_longform_turn_features as measurements


def fixture(tmp_path: Path, *, qualified=False, era="NOT_CURRENT_FORMAL_LEADERSHIP"):
    source = tmp_path / "turns.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    artifact = tmp_path / "features.json"
    inputs = [{"path": str(source), "sha256": ledger.sha256(source)}]
    document = {
        "schema": "member-longform-turn-features-v1",
        "status": "MEASURED_DELIVERY_NO_AUTOMATIC_PROFILE_PROMOTION",
        "contract": dict(measurements.CONTRACT),
        "feature_columns": dict(measurements.FEATURE_COLUMNS),
        "feature_definitions": dict(measurements.FEATURE_DEFINITIONS),
        "inputs": inputs,
        "turns": [{
            "turn_id": "example-source-01", "member": "Example Member",
            "source_era": era, "source_id": "source", "source_audio_sha256": "source-sha",
            "measured_duration_seconds": 60.0, "quality_hold": False,
            "features": {key: 1.0 for key in measurements.FEATURE_COLUMNS},
            "authority": "HASH_BOUND_NAMED_HUMAN_REVIEW" if qualified else "LEGACY_REVIEW_AUTHORITY_UNRESOLVED",
            "profile_measurement_eligible": qualified,
            "baseline_measurement_eligible": qualified,
            "human_reviewer": "Test Reviewer" if qualified else None,
            "human_reviewed_at": "2026-09-04T20:00:00+00:00" if qualified else None,
            "candidate_turn_binding_sha256": "binding" if qualified else None,
            "turn_coverage": {"turn_binding_sha256": "binding", "continuity_gate_passed": True,
                              "interior_coverage_complete": True} if qualified else None,
        }],
    }
    document["turns"][0]["row_sha256"] = measurements.row_hash(document["turns"][0])
    document["members"] = measurements.aggregate(document["turns"])
    document["counts"] = {"turns": 1, "members": 1, "sources": 1, "minutes": 1.0,
                          "qualified_turns": int(qualified), "baseline_eligible_turns": int(qualified),
                          "candidate_only_turns": int(not qualified), "quality_held_turns": 0}
    artifact.write_text(json.dumps(document), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt_doc = {
        "schema": "member-longform-turn-features-receipt-v1",
        "status": "PASS_MEASUREMENT_ONLY",
        "artifact": str(artifact.resolve()),
        "artifact_sha256": ledger.sha256(artifact),
        "inputs": inputs, "contract": dict(measurements.CONTRACT), "counts": document["counts"],
    }
    receipt.write_text(json.dumps(receipt_doc), encoding="utf-8")
    return artifact, receipt, source


def rewrite(artifact, receipt, mutate, *, rehash_rows=True):
    document = json.loads(artifact.read_text(encoding="utf-8"))
    mutate(document)
    if rehash_rows:
        for turn in document["turns"]:
            turn["row_sha256"] = measurements.row_hash({key: value for key, value in turn.items() if key != "row_sha256"})
    artifact.write_text(json.dumps(document), encoding="utf-8")
    receipt_doc = json.loads(receipt.read_text(encoding="utf-8"))
    receipt_doc["artifact_sha256"] = ledger.sha256(artifact)
    receipt.write_text(json.dumps(receipt_doc), encoding="utf-8")


def test_absent_delivery_is_optional(tmp_path):
    assert ledger.load_longform_delivery(tmp_path / "missing", tmp_path / "receipt") == {}


def test_candidate_measurements_remain_non_authoritative(tmp_path):
    artifact, receipt, _ = fixture(tmp_path)
    result = ledger.load_longform_delivery(artifact, receipt)
    assert result["turns"][0]["profile_measurement_eligible"] is False
    assert result["turns"][0]["baseline_measurement_eligible"] is False


def test_missing_pair_fails_closed(tmp_path):
    artifact, receipt, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match="both exist"):
        ledger.load_longform_delivery(artifact, tmp_path / "missing")


def test_changed_source_fails_closed(tmp_path):
    artifact, receipt, source = fixture(tmp_path)
    source.write_text('{"changed":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="input missing or changed"):
        ledger.load_longform_delivery(artifact, receipt)


def test_changed_artifact_fails_closed(tmp_path):
    artifact, receipt, _ = fixture(tmp_path)
    artifact.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        ledger.load_longform_delivery(artifact, receipt)


def test_candidate_cannot_be_promoted_by_boolean(tmp_path):
    artifact, receipt, _ = fixture(tmp_path)
    rewrite(artifact, receipt, lambda doc: doc["turns"][0].update(profile_measurement_eligible=True))
    with pytest.raises(ValueError, match="cannot gain profile authority"):
        ledger.load_longform_delivery(artifact, receipt)


def test_post_role_measurement_cannot_become_baseline(tmp_path):
    artifact, receipt, _ = fixture(tmp_path, qualified=True, era="CURRENT_ROLE_CONDITIONED")
    with pytest.raises(ValueError, match="source-era"):
        ledger.load_longform_delivery(artifact, receipt)


def test_qualified_pre_role_measurement_is_allowed(tmp_path):
    artifact, receipt, _ = fixture(tmp_path, qualified=True, era="PRE_CURRENT_LEADERSHIP_ROLE")
    assert ledger.load_longform_delivery(artifact, receipt)["turns"][0]["baseline_measurement_eligible"]


def test_interaction_labels_do_not_invent_other_speakers():
    assert "not share of the conversation" in ledger.INTERACTION_DEFINITIONS["gini"]
    assert "not contested back-and-forth" in ledger.INTERACTION_DEFINITIONS["burst"]
    assert "not an observed interruption" in ledger.INTERACTION_DEFINITIONS["cutoff"]


@pytest.mark.parametrize("aggregate_field", ["qualified_baseline", "recurring_communication_pattern_established"])
def test_member_aggregate_cannot_override_candidate_authority(tmp_path, aggregate_field):
    artifact, receipt, _ = fixture(tmp_path)
    def corrupt(document):
        member = document["members"]["Example Member"]
        member[aggregate_field] = (member["candidate_only"] if aggregate_field == "qualified_baseline" else True)
    rewrite(artifact, receipt, corrupt)
    with pytest.raises(ValueError, match="member aggregates"):
        ledger.load_longform_delivery(artifact, receipt)


def test_row_hash_must_match_even_if_outer_receipt_hash_was_updated(tmp_path):
    artifact, receipt, _ = fixture(tmp_path)
    rewrite(artifact, receipt, lambda document: document["turns"][0]["features"].update(pace=999), rehash_rows=False)
    with pytest.raises(ValueError, match="turn row hash"):
        ledger.load_longform_delivery(artifact, receipt)


@pytest.mark.parametrize("field", ["counts", "inputs", "contract", "feature_columns", "feature_definitions"])
def test_receipt_and_measurement_contract_cannot_drift(tmp_path, field):
    artifact, receipt, _ = fixture(tmp_path)
    rewrite(artifact, receipt, lambda document: document.update({field: [] if field == "inputs" else {}}))
    with pytest.raises(ValueError):
        ledger.load_longform_delivery(artifact, receipt)


@pytest.mark.parametrize("field,value", [("quality_hold", True), ("human_reviewer", None),
                                         ("turn_coverage", None), ("human_reviewed_at", "2026-09-04"),
                                         ("candidate_turn_binding_sha256", "other")])
def test_qualified_authority_requires_review_provenance_and_no_quality_hold(tmp_path, field, value):
    artifact, receipt, _ = fixture(tmp_path, qualified=True)
    rewrite(artifact, receipt, lambda document: document["turns"][0].update({field: value}))
    with pytest.raises(ValueError, match="qualified turn lacks"):
        ledger.load_longform_delivery(artifact, receipt)


def test_duplicate_turn_cannot_inflate_aggregates(tmp_path):
    artifact, receipt, _ = fixture(tmp_path)
    rewrite(artifact, receipt, lambda document: document["turns"].append(dict(document["turns"][0])))
    with pytest.raises(ValueError, match="duplicate turn/source"):
        ledger.load_longform_delivery(artifact, receipt)


def test_receipt_counts_are_checked_independently(tmp_path):
    artifact, receipt, _ = fixture(tmp_path)
    receipt_doc = json.loads(receipt.read_text(encoding="utf-8"))
    receipt_doc["counts"]["qualified_turns"] = 1
    receipt.write_text(json.dumps(receipt_doc), encoding="utf-8")
    with pytest.raises(ValueError, match="counts mismatch"):
        ledger.load_longform_delivery(artifact, receipt)

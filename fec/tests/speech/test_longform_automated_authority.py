import copy
import json
from pathlib import Path
import wave

import pytest

from fec.speech import longform_automated_authority as auto
from fec.speech import reconcile_member_longform_candidate_passes as reconcile
from fec.speech import verify_member_longform_automated as verify
from fec.speech import verify_member_longform_speaker_continuity as acoustic
from fec.speech import materialize_member_longform_reviewed_turns as materialize
from fec.speech import verify_member_longform_profile_readiness as readiness
from fec.speech import build_member_longform_turn_features as features
from fec.members import build_member_narrative_ledger as ledger


def fixture(tmp_path, monkeypatch):
    # Synthetic audio and acoustic decisions; never production attestations.
    wav, words_path = tmp_path / "source.wav", tmp_path / "words.jsonl"
    with wave.open(str(wav), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000 * 60)
    words = [{"word": f"token{i}", "start_sec": i, "end_sec": i + .8} for i in range(60)]
    reconcile.SMR.write_jsonl_atomic(words_path, words)
    source = {"member": "Example Member", "source_id": "source", "wav": str(wav),
              "wav_sha256": reconcile.SMR.sha256_file(wav), "transcript": str(words_path),
              "transcript_sha256": reconcile.SMR.sha256_file(words_path),
              "status": "SELECTED_HARD_NAME_CUE_CANDIDATE", "source_era": "NOT_CURRENT_FORMAL_LEADERSHIP",
              "cues": [{"phrase": "Example Member", "start_sec": 0, "end_sec": 1}]}
    selection = tmp_path / "selection.jsonl"
    reconcile.SMR.write_jsonl_atomic(selection, [source])
    claim = {"start_sec": 2, "end_sec": 51.8, "opening_words": "token2 token3 token4 token5",
             "closing_words": "token48 token49 token50 token51", "complete_turn": True,
             "handoff_basis": "Synthetic fixture: host question, full response, host follow-up."}
    raw = {**{key: source[key] for key in reconcile.BINDING_KEYS}, "identity_sequence": "MATCH", "turns": [claim]}
    passes = [tmp_path / "a.json", tmp_path / "b.json"]
    for path in passes:
        reconcile.SMR.write_json_atomic(path, raw)
    candidate_path, candidate_receipt = tmp_path / "candidates.jsonl", tmp_path / "candidates.receipt.json"
    monkeypatch.setattr("sys.argv", ["prog", "--input", str(selection), "--member", source["member"],
        "--source-id", "source", "--pass-a", str(passes[0]), "--pass-b", str(passes[1]),
        "--output", str(candidate_path), "--receipt", str(candidate_receipt)])
    assert reconcile.main() == 0
    candidate = reconcile.SMR.read_jsonl(candidate_path)[0]
    turn = candidate["turns"][0]
    windows = [{"turn_index": 0, "window_index": i, "accepted": True, "anchor_eligible": True,
                "start_sec": start, "end_sec": start + 8,
                "turn_binding_sha256": acoustic.turn_binding_sha256(candidate, turn)}
               for i, start in enumerate(acoustic.candidate_starts(turn))]
    continuity = tmp_path / "continuity.jsonl"
    acoustic_row = {**{key: source[key] for key in reconcile.BINDING_KEYS}, "anchored_member": True,
        "method": "SYNTHETIC_UNIT_TEST_NO_REAL_IDENTITY_INFERENCE", "evaluated_windows": windows,
        "coverage_contract_version": acoustic.CONTINUITY_CONTRACT_VERSION,
        "turn_coverage": [acoustic.summarize_turn_coverage(candidate, turn, 0, windows, budget_exhausted=False)]}
    reconcile.SMR.write_jsonl_atomic(continuity, [acoustic_row])
    reviews, receipt, diagnostics = (tmp_path / name for name in ("reviews.jsonl", "review.receipt.json", "diagnostics.jsonl"))
    argv = ["prog", "--selection", str(selection), "--candidates", str(candidate_path),
            "--candidate-receipt", str(candidate_receipt), "--continuity", str(continuity),
            "--out", str(reviews), "--receipt", str(receipt), "--diagnostics", str(diagnostics)]
    monkeypatch.setattr("sys.argv", argv)
    assert verify.main() == 0
    return dict(locals())


def materialized(item, monkeypatch):
    output, receipt = item["tmp_path"] / "turns.jsonl", item["tmp_path"] / "turns.receipt.json"
    monkeypatch.setattr("sys.argv", ["prog", "--selection", str(item["selection"]),
        "--candidates", str(item["candidate_path"]), "--continuity", str(item["continuity"]),
        "--reviews", str(item["reviews"]), "--out", str(output), "--receipt", str(receipt),
        "--audio-root", str(item["tmp_path"] / "audio")])
    assert materialize.main() == 0
    return output, receipt


def test_actual_auto_chain_materializes_measures_and_bridges_semantics_without_human(tmp_path, monkeypatch):
    item = fixture(tmp_path, monkeypatch)
    output, receipt = materialized(item, monkeypatch)
    turn = reconcile.SMR.read_jsonl(output)[0]
    assert turn["status"] == auto.STATUS
    assert "human_reviewer" not in turn and "human_reviewed_at" not in turn
    assert turn["automated_verifier"] == auto.METHOD
    errors = []
    inputs = readiness.bound_inputs(json.loads(receipt.read_text()), errors)
    assert not errors
    assert readiness.exact_review_errors(turn, inputs) == []
    roles = tmp_path / "roles.json"
    reconcile.SMR.write_json_atomic(roles, {"members": {}})
    artifact = features.build_artifact([(output, receipt)], [item["selection"]], [item["selection"]], roles,
        measure=lambda _: {key: 1.0 for key in features.FEATURE_COLUMNS})
    assert artifact["counts"]["qualified_turns"] == 1
    assert artifact["turns"][0]["authority"] == auto.AUTHORITY
    bound = ledger.longform_semantic_authority(turn, artifact["turns"])
    assert bound["semantic_review_qualified"] is True
    assert bound["semantic_authority"] == auto.AUTHORITY
    assert bound["semantic_claim_status"] == "AUTOMATED_VERIFIED_SOURCE_BOUND"


@pytest.mark.parametrize("change", ["failed_window", "cold", "changed_pass", "fake_human", "invented_semantics"])
def test_automatic_approval_never_waives_evidence_or_invents_human_review(tmp_path, monkeypatch, change):
    item = fixture(tmp_path, monkeypatch)
    if change in {"failed_window", "cold"}:
        record = copy.deepcopy(item["acoustic_row"])
        if change == "failed_window":
            record["evaluated_windows"][-1]["accepted"] = False
        else:
            record["anchored_member"] = False
        reconcile.SMR.write_jsonl_atomic(item["continuity"], [record])
    elif change == "changed_pass":
        item["passes"][0].write_text("{}", encoding="utf-8")
    else:
        review = reconcile.SMR.read_jsonl(item["reviews"])[0]
        if change == "fake_human":
            review["review_attestation"] = "personally_listened_and_reviewed_identity_boundaries_and_semantics"
        else:
            review["semantic_findings"][0]["finding"] = "Invented private persuasion preference."
        reconcile.SMR.write_jsonl_atomic(item["reviews"], [review])
    with pytest.raises((ValueError, materialize.PromotionRefusal)):
        materialized(item, monkeypatch)

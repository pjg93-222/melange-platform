import copy
import json
from pathlib import Path
import wave

import numpy as np
import pytest

from fec.speech import reconcile_member_longform_candidate_passes as mod
from fec.speech import verify_member_longform_speaker_continuity as continuity
from fec.speech import build_member_longform_review_queue as review_queue


def fixture():
    words = [{"word": f"token{index}", "start_sec": float(index), "end_sec": index + .8}
             for index in range(620)]
    source = {"member": "Example Member", "source_id": "recording",
              "wav_sha256": "a" * 64, "transcript_sha256": "b" * 64,
              "cues": [{"start_sec": 0, "end_sec": 1, "phrase": "Example Member"}]}
    claim = {"start_sec": 2.1, "end_sec": 601.7,
             "opening_words": "token2 token3 token4 token5", "closing_words": "token598 token599 token600 token601",
             "complete_turn": True, "handoff_basis": "Host finishes question; target responds; host takes floor next."}
    raw = {**source, "identity_sequence": "MATCH", "turns": [claim]}
    return source, words, raw


def test_exact_two_boundary_agreement_preserves_ten_minute_turn():
    source, words, raw = fixture()
    other = copy.deepcopy(raw)
    other["turns"][0].update(start_sec=2.7, end_sec=601.2,
                              opening_words="token2 token3 token4 token5 token6")
    turns, diagnostics = mod.reconcile(source, words, raw, other)
    assert len(turns) == 1
    assert turns[0]["start_sec"] == 2
    assert turns[0]["end_sec"] == 601.8
    assert turns[0]["duration_seconds"] == 599.8
    assert not diagnostics["disagreements"]


def test_overlapping_different_endpoints_are_not_intersected():
    source, words, raw = fixture()
    other = copy.deepcopy(raw)
    other["turns"][0].update(end_sec=599.8, closing_words="token596 token597 token598 token599")
    turns, diagnostics = mod.reconcile(source, words, raw, other)
    assert turns == []
    assert len(diagnostics["disagreements"]) == 2


@pytest.mark.parametrize("patch, expected", [
    ({"closing_words": "wrong ending words here"}, "CLOSING_ANCHOR_NOT_FOUND_NEAR_TIME"),
    ({"opening_words": "token2 token3 token4"}, "OPENING_ANCHOR_REQUIRES_4_TO_10_TOKENS"),
    ({"start_sec": 50}, "OPENING_ANCHOR_NOT_FOUND_NEAR_TIME"),
    ({"complete_turn": False}, "COMPLETE_TURN_NOT_CONFIDENTLY_CLAIMED"),
    ({"uncertain": True}, "COMPLETE_TURN_NOT_CONFIDENTLY_CLAIMED"),
    ({"handoff_basis": ""}, "BOTH_HANDOFFS_NOT_DESCRIBED"),
    ({"end_sec": float("nan")}, "INVALID_CLAIM_TIMESTAMPS"),
])
def test_invalid_or_uncertain_claims_are_visible_skips(patch, expected):
    source, words, raw = fixture()
    other = copy.deepcopy(raw)
    other["turns"][0].update(patch)
    turns, diagnostics = mod.reconcile(source, words, raw, other)
    assert not turns
    assert diagnostics["b"]["skipped"][0]["reason"] == expected


def test_ambiguous_anchor_near_time_is_rejected():
    source, words, raw = fixture()
    for index in range(2, 10):
        words[index].update(word=f"repeat{(index - 2) % 4}", start_sec=2 + (index - 2) * .2, end_sec=2.1 + (index - 2) * .2)
    raw["turns"][0]["opening_words"] = "repeat0 repeat1 repeat2 repeat3"
    turns, diagnostics = mod.reconcile(source, words, raw, raw)
    assert not turns
    assert diagnostics["a"]["skipped"][0]["reason"] == "OPENING_ANCHOR_AMBIGUOUS_NEAR_TIME"


def test_overlapping_complete_claims_inside_a_pass_are_excluded():
    source, words, raw = fixture()
    other = copy.deepcopy(raw)
    other["turns"].append({**other["turns"][0], "start_sec": 8,
                            "opening_words": "token8 token9 token10 token11"})
    turns, diagnostics = mod.reconcile(source, words, raw, other)
    assert not turns
    assert {item["reason"] for item in diagnostics["b"]["skipped"]} == {"OVERLAPPING_COMPLETE_TURN_CLAIMS"}


def test_source_hash_mismatch_refuses_entire_pass():
    source, words, raw = fixture()
    other = {**raw, "transcript_sha256": "stale"}
    with pytest.raises(ValueError, match="source/hash binding"):
        mod.reconcile(source, words, raw, other)


def test_neutral_identity_pass_does_not_establish_candidate():
    source, words, raw = fixture()
    turns, diagnostic = mod.reconcile(source, words, raw, {**raw, "identity_sequence": "NEUTRAL"})
    assert not turns
    assert diagnostic["b"]["skipped"][0]["reason"] == "NO_CONFIDENT_NAMED_HANDOFF_SEQUENCE"


def test_anchor_text_cannot_include_words_outside_the_resolved_turn():
    source, words, raw = fixture()
    words = [{**word, "start_sec": 3 * index, "end_sec": 3 * index + 2.8} for index, word in enumerate(words)]
    raw["turns"][0].update(start_sec=6, end_sec=11.8, closing_words="token0 token1 token2 token3")
    turns, diagnostics = mod.reconcile(source, words, raw, raw)
    assert not turns
    assert diagnostics["a"]["skipped"][0]["reason"] == "ANCHOR_PHRASE_EXTENDS_OUTSIDE_COMPLETE_TURN"


def downstream_contract_check(tmp_path, monkeypatch, source_path, candidate_path, source):
    """Real downstream orchestration with a mocked embedding backend, not inference."""
    class FakeBackend:
        def embed(self, signal, sample_rate):
            return np.asarray([1.0, 0.0], dtype=np.float32)
    monkeypatch.setattr(continuity, "get_backend", lambda *args, **kwargs: FakeBackend())
    monkeypatch.setattr(continuity.exact, "voice_activity", lambda signal: (1.0, 0.0))
    monkeypatch.setattr(continuity.exact, "overlapping_words", lambda *args: [{}] * 100)
    vectors = tmp_path / "vault.npz"
    np.savez(vectors, members=np.asarray([source["member"], "Other Member"]),
             vectors=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    anchor, calibration = tmp_path / "anchor.json", tmp_path / "calibration.json"
    mod.SMR.write_json_atomic(anchor, {"combined_vectors": str(vectors), "combined_vectors_sha256": mod.SMR.sha256_file(vectors)})
    mod.SMR.write_json_atomic(calibration, {"false_accepts": 0, "all_vaulted_speakers_compared": True,
        "per_claim_thresholds": {source["member"]: {"minimum_target_best_score": .6, "minimum_best_anchor_margin": .1}}})
    canonical, interviews = tmp_path / "canonical.jsonl", tmp_path / "interviews.json"
    mod.SMR.write_jsonl_atomic(canonical, [])
    mod.SMR.write_json_atomic(interviews, {"status": "DISPLAY_ONLY_NO_ENGINE_WIRING", "verified_sources": []})
    acoustic, acoustic_receipt = tmp_path / "acoustic.jsonl", tmp_path / "acoustic.receipt.json"
    monkeypatch.setattr("sys.argv", ["prog", "--input", str(candidate_path), "--output", str(acoustic),
        "--receipt", str(acoustic_receipt), "--anchor-receipt", str(anchor), "--calibration", str(calibration),
        "--canonical", str(canonical), "--verified-interviews", str(interviews), "--max-windows-per-member", "0"])
    assert continuity.main() == 0
    candidate = json.loads(candidate_path.read_text())
    acoustic_row = json.loads(acoustic.read_text())
    coverage = review_queue.qualified_turn_coverage(candidate, acoustic_row, 0, candidate["turns"][0])
    assert coverage and coverage["interior_coverage_complete"]
    assert coverage["planned_windows"] > 18
    queue_out, queue_receipt = tmp_path / "review_queue.jsonl", tmp_path / "review_queue.receipt.json"
    monkeypatch.setattr("sys.argv", ["prog", "--selection", str(source_path), "--candidates", str(candidate_path),
        "--continuity", str(acoustic), "--out", str(queue_out), "--receipt", str(queue_receipt),
        "--audio-root", str(tmp_path / "review-audio")])
    assert review_queue.main() == 0
    row = json.loads(queue_out.read_text())
    assert row["status"] == "IDENTITY_BOUNDARY_SEMANTIC_REVIEW_REQUIRED"
    assert row["turn_coverage"]["turn_binding_sha256"] == continuity.turn_binding_sha256(candidate, candidate["turns"][0])
    assert row["wav_sha256"] == source["wav_sha256"]
    assert row["transcript_sha256"] == source["transcript_sha256"]
    assert row["duration_seconds"] == candidate["turns"][0]["duration_seconds"]


def test_main_validates_physical_sources_and_emits_only_unverified_candidates(tmp_path, monkeypatch):
    source, words, raw = fixture()
    wav = tmp_path / "recording.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * 16000 * 620)
    transcript = tmp_path / "words.jsonl"
    mod.SMR.write_jsonl_atomic(transcript, words)
    source.update(wav=str(wav), wav_sha256=mod.SMR.sha256_file(wav), transcript=str(transcript),
                  transcript_sha256=mod.SMR.sha256_file(transcript), status="SELECTED_HARD_NAME_CUE_CANDIDATE")
    source_path, a, b, output, receipt = (tmp_path / name for name in ("source.jsonl", "a.json", "b.json", "out.jsonl", "receipt.json"))
    mod.SMR.write_jsonl_atomic(source_path, [source])
    # Synthetic fixture claims only; this unit test makes no model request.
    raw.update({key: source[key] for key in mod.BINDING_KEYS})
    for path in (a, b):
        path.write_text(json.dumps(raw), encoding="utf-8")
    arguments = ["prog", "--input", str(source_path), "--member", source["member"],
                                    "--source-id", source["source_id"], "--pass-a", str(a), "--pass-b", str(b),
                                    "--output", str(output), "--receipt", str(receipt)]
    monkeypatch.setattr("sys.argv", arguments)
    mod.SMR.write_jsonl_atomic(source_path, [{**source, "status": "HARD_IN_BAND_NAME_CUE_CANDIDATE"}])
    with pytest.raises(SystemExit):
        mod.main()
    assert not output.exists() and not receipt.exists()
    mod.SMR.write_jsonl_atomic(source_path, [{**source, "wav_sha256": "stale-audio-hash"}])
    with pytest.raises(ValueError, match="wav_hash_mismatch"):
        mod.main()
    assert not output.exists() and not receipt.exists()
    mod.SMR.write_jsonl_atomic(source_path, [source])
    assert mod.main() == 0
    row = json.loads(output.read_text())
    report = json.loads(receipt.read_text())
    assert row["status"] == "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED"
    assert row["speaker_identity_authority"] is False
    assert row["human_semantic_review"] is False
    assert row["independent_models_claimed"] is False
    assert report["contract"]["maximum_turn_seconds"] is None
    assert report["inputs"]["identity_cues_sha256"] == mod.SMR.sha256_file(source_path)
    assert report["output"]["sha256"] == mod.SMR.sha256_file(output)
    downstream_contract_check(tmp_path, monkeypatch, source_path, output, source)
    monkeypatch.setattr("sys.argv", arguments)
    with pytest.raises(SystemExit):
        mod.main()

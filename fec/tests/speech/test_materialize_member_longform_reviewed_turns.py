import hashlib
import json
from pathlib import Path
import wave

import pytest

from fec.speech import materialize_member_longform_reviewed_turns as mod
from fec.speech import verify_member_longform_speaker_continuity as checks


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_files(tmp_path: Path):
    wav = tmp_path / "source.wav"
    with wave.open(str(wav), "wb") as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16_000)
        target.writeframes(b"\x01\x00" * (16_000 * 60))
    transcript = tmp_path / "words.jsonl"
    words = [{"word": f"word{index}", "start_sec": 1 + index * .4, "end_sec": 1.3 + index * .4} for index in range(30)]
    write_jsonl(transcript, words)
    selection = tmp_path / "selection.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    continuity = tmp_path / "continuity.jsonl"
    reviews = tmp_path / "reviews.jsonl"
    out = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    write_jsonl(selection, [{
        "member": "Example Member", "source_id": "source", "status": "SELECTED_HARD_NAME_CUE_CANDIDATE",
        "source_era": "NOT_CURRENT_FORMAL_LEADERSHIP", "cues": [{"phrase": "Example Member"}],
        "wav": str(wav), "wav_sha256": sha(wav), "transcript": str(transcript),
        "transcript_sha256": sha(transcript),
    }])
    write_jsonl(candidates, [{
        "member": "Example Member", "source_id": "source", "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED",
        "wav_sha256": sha(wav), "transcript_sha256": sha(transcript),
        "turns": [{"start_sec": 1, "end_sec": 45, "duration_seconds": 44}],
    }])
    candidate = json.loads(candidates.read_text(encoding="utf-8"))
    turn = candidate["turns"][0]
    windows = [{"turn_index": 0, "window_index": index, "accepted": True,
                "start_sec": start, "end_sec": start + 8, "anchor_eligible": True,
                "turn_binding_sha256": checks.turn_binding_sha256(candidate, turn)}
               for index, start in enumerate(checks.candidate_starts(turn))]
    write_jsonl(continuity, [{
        "member": "Example Member", "source_id": "source", "wav_sha256": sha(wav),
        "transcript_sha256": sha(transcript), "method": "cold-test", "anchored_member": False,
        "supplemental_member": False, "evaluated_windows": windows,
        "coverage_contract_version": checks.CONTINUITY_CONTRACT_VERSION,
        "turn_coverage": [checks.summarize_turn_coverage(candidate, turn, 0, windows, budget_exhausted=False)],
    }])
    review = {
        "turn_id": "example-member-source-01", "member": "Example Member", "source_id": "source",
        "start_sec": 1, "end_sec": 45, "review_status": "ACCEPT", "reviewer_type": "HUMAN",
        "reviewer_name": "Test Reviewer",
        "reviewed_at": "2026-09-04T20:00:00+00:00",
        "wav_sha256": sha(wav), "transcript_sha256": sha(transcript),
        "candidate_turn_binding_sha256": checks.turn_binding_sha256(candidate, turn),
        "review_attestation": "personally_listened_and_reviewed_identity_boundaries_and_semantics",
        "identity_review": "ACCEPT", "identity_review_note": "Named handoff and voice confirmed.",
        "boundary_review": "ACCEPT", "boundary_review_note": "Both handoffs confirmed.",
        "semantic_review": "ACCEPT", "semantic_findings": [{
            "kind": "long_answer", "finding": "The answer ran twelve seconds.",
            "meeting_implication": "Ask one question at a time.",
            "meeting_phase": "run_the_room",
            "scope": "One reviewed public interview turn.",
        }],
    }
    write_jsonl(reviews, [review])
    return selection, candidates, continuity, reviews, out, receipt, tmp_path / "turns"


def run(paths, monkeypatch):
    selection, candidates, continuity, reviews, out, receipt, audio_root = paths
    monkeypatch.setattr("sys.argv", [
        "prog", "--selection", str(selection), "--candidates", str(candidates),
        "--continuity", str(continuity), "--reviews", str(reviews), "--out", str(out),
        "--receipt", str(receipt), "--audio-root", str(audio_root),
    ])
    return mod.main()


def test_materializes_fully_bound_review(tmp_path, monkeypatch):
    paths = fixture_files(tmp_path)
    assert run(paths, monkeypatch) == 0
    row = json.loads(paths[4].read_text(encoding="utf-8").strip())
    assert row["status"] == "HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED"
    assert row["source_identity_tier"] == "LITERAL_NAMED_HANDOFF_PLUS_COLD_START_REJECTION_CONTINUITY"
    assert Path(row["turn_audio_wav"]).is_file()


def test_refuses_nonhuman_review(tmp_path, monkeypatch):
    paths = fixture_files(tmp_path)
    review = json.loads(paths[3].read_text(encoding="utf-8").strip())
    review["reviewer_type"] = "MODEL"
    write_jsonl(paths[3], [review])
    with pytest.raises(mod.PromotionRefusal, match="reviewer_not_human"):
        run(paths, monkeypatch)


def test_three_early_windows_cannot_approve_complete_turn(tmp_path, monkeypatch):
    paths = fixture_files(tmp_path)
    acoustic = json.loads(paths[2].read_text(encoding="utf-8"))
    acoustic["evaluated_windows"] = acoustic["evaluated_windows"][:3]
    write_jsonl(paths[2], [acoustic])
    with pytest.raises(mod.PromotionRefusal, match="complete_turn_continuity_not_verified"):
        run(paths, monkeypatch)


def test_changed_candidate_boundary_invalidates_acoustic_approval(tmp_path, monkeypatch):
    paths = fixture_files(tmp_path)
    candidate = json.loads(paths[1].read_text(encoding="utf-8"))
    candidate["turns"][0]["end_sec"] = 46
    review = json.loads(paths[3].read_text(encoding="utf-8"))
    review["end_sec"] = 46
    write_jsonl(paths[1], [candidate])
    write_jsonl(paths[3], [review])
    with pytest.raises(mod.PromotionRefusal, match="complete_turn_continuity_not_verified"):
        run(paths, monkeypatch)


def test_rejects_stale_human_review_even_with_same_source_name(tmp_path, monkeypatch):
    paths = fixture_files(tmp_path)
    review = json.loads(paths[3].read_text(encoding="utf-8"))
    review["transcript_sha256"] = "old-transcript"
    write_jsonl(paths[3], [review])
    with pytest.raises(mod.PromotionRefusal, match="human_review_source_hash_binding"):
        run(paths, monkeypatch)


def test_descriptive_findings_do_not_require_meeting_advice(tmp_path, monkeypatch):
    paths = fixture_files(tmp_path)
    review = json.loads(paths[3].read_text(encoding="utf-8"))
    review["semantic_findings"] = [{"kind": "answer_structure", "finding": "One uninterrupted response.",
                                     "scope": "One reviewed public interview turn."}]
    write_jsonl(paths[3], [review])
    assert run(paths, monkeypatch) == 0


def test_false_pre_role_label_is_rejected_before_audio_write(tmp_path, monkeypatch):
    paths = fixture_files(tmp_path)
    selection = json.loads(paths[0].read_text(encoding="utf-8"))
    selection.update(current_role="Leader", source_era="PRE_CURRENT_LEADERSHIP_ROLE",
                     recorded_at="2026-01-01", current_role_assumed_on="2023-01-03")
    write_jsonl(paths[0], [selection])
    with pytest.raises(mod.PromotionRefusal, match="leader_recording_date_not_pre_current_role"):
        run(paths, monkeypatch)
    assert not paths[6].exists()


def test_multiple_sources_for_same_member_are_retained():
    result = mod.source_index([{"member": "M", "source_id": "A"}, {"member": "M", "source_id": "B"}], "test")
    assert len(result) == 2


def test_unselected_source_gap_does_not_block_other_members():
    assert mod.source_index([{"member": "M", "source_id": None,
                              "status": "LEADERSHIP_PREROLE_SOURCE_GAP"}], "selection") == {}


def test_review_chain_change_during_materialization_is_refused(tmp_path, monkeypatch):
    paths = fixture_files(tmp_path)
    original = mod.cut_wav
    def change_review(*args):
        result = original(*args)
        paths[3].write_text(paths[3].read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return result
    monkeypatch.setattr(mod, "cut_wav", change_review)
    with pytest.raises(mod.PromotionRefusal, match="input_changed_during_materialization:reviews"):
        run(paths, monkeypatch)
    assert not paths[4].exists()

import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/verify_member_longform_speaker_continuity.py"
spec = importlib.util.spec_from_file_location("verify_member_longform_speaker_continuity", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_candidate_windows_have_no_turn_duration_ceiling():
    starts = module.candidate_starts({"start_sec": 10, "end_sec": 1010})
    assert len(starts) > 100
    assert starts[0] == 12
    assert starts[-1] + module.WINDOW_SECONDS <= 1008


def test_three_consecutive_passes_are_required_within_one_turn():
    rows = [
        {"turn_index": 0, "window_index": 0, "accepted": True},
        {"turn_index": 0, "window_index": 1, "accepted": True},
        {"turn_index": 1, "window_index": 0, "accepted": True},
        {"turn_index": 1, "window_index": 1, "accepted": True},
        {"turn_index": 1, "window_index": 2, "accepted": True},
    ]
    assert module.maximum_consecutive_passes(rows) == 3


def test_gate_cannot_change_profiles_without_review():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"speaker_identity_authority": False' in text
    assert '"turn_boundary_reviewed": False' in text
    assert '"profile_effect": "NONE_UNTIL_HUMAN_TURN_REVIEW"' in text
    assert "SUPPLEMENTAL_SPEAKER_CONTINUITY_PASSED_REVIEW_REQUIRED" in text
    assert "supplemental_three_event_continuity_is_not_zero_false_accept_calibration" in text


SOURCE = {"member": "Example Member", "source_id": "episode-1",
          "wav_sha256": "audio-sha", "transcript_sha256": "transcript-sha"}


def passing_windows(turn, *, turn_index=0, source=None):
    source = source or SOURCE
    starts = module.candidate_starts(turn)
    return [{"turn_index": turn_index, "window_index": index,
             "turn_binding_sha256": module.turn_binding_sha256(source, turn),
             "start_sec": start, "end_sec": start + module.WINDOW_SECONDS,
             "anchor_eligible": abs(start - (starts[0] + index * module.WINDOW_SECONDS)) < 1e-6,
             "accepted": True} for index, start in enumerate(starts)]


def summarize(turn, rows, *, budget=False, source=None):
    return module.summarize_turn_coverage(source or SOURCE, turn, 0, rows, budget_exhausted=budget)


def test_default_has_no_silent_member_window_cap():
    assert module.MAX_WINDOWS_PER_MEMBER == 0
    assert module.WINDOW_SECONDS == 8.0
    assert module.TURN_INSET_SECONDS == 2.0
    assert module.REQUIRED_CONSECUTIVE_WINDOWS == 3


def test_complete_multiminute_turn_requires_windows_beyond_first_144_seconds():
    turn = {"start_sec": 10, "end_sec": 610}
    rows = passing_windows(turn)
    incomplete = summarize(turn, rows[:18], budget=True)
    assert incomplete["status"] == "INCOMPLETE_WINDOW_BUDGET"
    assert incomplete["interior_coverage_complete"] is False
    assert incomplete["continuity_gate_passed"] is False
    assert incomplete["interior_coverage_gaps"] == [[156.0, 608.0]]
    complete = summarize(turn, rows)
    assert complete["planned_windows"] > 18
    assert complete["continuity_gate_passed"] is True
    assert complete["interior_coverage_gaps"] == []
    assert complete["full_turn_acoustically_verified"] is False
    assert complete["boundary_margins_require_human_review"] is True


def test_three_early_windows_cannot_verify_a_long_turn_even_without_budget_flag():
    turn = {"start_sec": 0, "end_sec": 600}
    result = summarize(turn, passing_windows(turn)[:3])
    assert result["maximum_consecutive_passing_windows"] == 3
    assert result["continuity_gate_passed"] is False
    assert result["interior_coverage_complete"] is False


@pytest.mark.parametrize("failed_index", [3, 25, -1])
def test_one_rejected_window_anywhere_blocks_complete_turn_gate(failed_index):
    turn = {"start_sec": 0, "end_sec": 600}
    rows = passing_windows(turn)
    rows[failed_index]["accepted"] = False
    result = summarize(turn, rows)
    assert result["interior_coverage_complete"] is True
    assert result["continuity_gate_passed"] is False
    assert result["status"] == "CONTINUITY_REJECTED_WINDOWS"
    assert result["accepted_interior_coverage_gaps"]


def test_residual_tail_is_covered_with_calibrated_length_but_not_an_extra_anchor():
    turn = {"start_sec": 0, "end_sec": 31}
    rows = passing_windows(turn)
    assert module.candidate_starts(turn) == [2.0, 10.0, 18.0, 21.0]
    assert rows[-1]["anchor_eligible"] is False
    assert summarize(turn, rows)["maximum_consecutive_passing_windows"] == 3
    assert summarize(turn, rows)["continuity_gate_passed"] is True
    assert summarize(turn, rows[:-1])["interior_coverage_gaps"] == [[26.0, 29.0]]


def test_overlapping_tail_cannot_manufacture_three_nonoverlapping_anchors():
    turn = {"start_sec": 0, "end_sec": 27}
    rows = passing_windows(turn)
    assert len(rows) == 3
    rows[-1]["anchor_eligible"] = True  # stored claims do not override geometry
    result = summarize(turn, rows)
    assert result["interior_coverage_complete"] is True
    assert result["maximum_consecutive_passing_windows"] == 2
    assert result["continuity_gate_passed"] is False
    assert result["status"] == "TURN_TOO_SHORT_FOR_CALIBRATED_CONTINUITY"


@pytest.mark.parametrize("end", [3, 4, 5, 11])
def test_short_valid_turns_are_explicitly_unsupported_not_errors(end):
    turn = {"start_sec": 0, "end_sec": end}
    assert module.candidate_starts(turn) == []
    result = summarize(turn, [])
    assert result["continuity_gate_passed"] is False
    assert result["status"] == "TURN_TOO_SHORT_FOR_CALIBRATED_CONTINUITY"


@pytest.mark.parametrize("turn", [{"start_sec": -1, "end_sec": 30},
                                  {"start_sec": 30, "end_sec": 30},
                                  {"start_sec": 0, "end_sec": float("inf")}])
def test_invalid_turn_geometry_is_refused(turn):
    with pytest.raises(ValueError):
        module.candidate_starts(turn)


@pytest.mark.parametrize("change", ["source_id", "wav_sha256", "transcript_sha256", "member"])
def test_windows_cannot_be_reused_with_changed_source_bindings(change):
    turn = {"start_sec": 0, "end_sec": 60}
    changed_source = {**SOURCE, change: "changed"}
    result = summarize(turn, passing_windows(turn), source=changed_source)
    assert result["continuity_gate_passed"] is False
    assert result["evaluated_windows"] == 0


def test_shifted_boundaries_duplicate_windows_and_wrong_indexes_are_refused():
    turn = {"start_sec": 0, "end_sec": 60}
    rows = passing_windows(turn)
    assert summarize({"start_sec": 1, "end_sec": 61}, rows)["continuity_gate_passed"] is False
    assert summarize(turn, rows + [dict(rows[0])])["continuity_gate_passed"] is False
    rows[-1]["window_index"] = 900
    assert summarize(turn, rows)["continuity_gate_passed"] is False


def test_reference_windows_are_counted_by_independent_production():
    refs = [{"event_id": "other-episode", "wav": f"clip-{index}.wav"} for index in range(3)]
    groups, audit = module.independent_reference_groups(refs, SOURCE)
    assert groups == [[0, 1, 2]]
    assert audit["independent_productions_available"] == 1
    assert audit["duplicate_production_reference_windows"] == 2


def test_same_source_aliases_hash_and_transitive_alias_bridges_are_excluded():
    refs = [
        {"event_id": "episode-1"},
        {"event_id": "different-id", "source_audio_sha256": "audio-sha"},
        {"event_id": "bridge-a"},
        {"event_id": "bridge-b"},
        {"event_id": "bridge-a", "source_id": "bridge-b"},
        {"source_id": "bridge-b", "source_audio_sha256": "audio-sha"},
        {"event_id": "independent"},
        {"wav": "anonymous-clip.wav"},
    ]
    groups, audit = module.independent_reference_groups(refs, SOURCE)
    assert groups == [[6]]
    assert audit["same_production_reference_windows_excluded"] == 6
    assert audit["unidentified_production_reference_windows_excluded"] == 1


def test_same_source_path_or_youtube_url_cannot_be_independent_reference(tmp_path):
    source = {**SOURCE, "wav": str(tmp_path / "source.wav"),
              "url": "https://www.youtube.com/watch?v=Video123"}
    refs = [{"event_id": "alias-one", "source_wav": str(tmp_path / "source.wav")},
            {"event_id": "alias-two", "url": "https://youtu.be/Video123"},
            {"event_id": "independent"}]
    groups, audit = module.independent_reference_groups(refs, source)
    assert groups == [[2]]
    assert audit["same_production_reference_windows_excluded"] == 2


def test_existing_score_thresholds_remain_unchanged():
    assert module.COLD_OTHER_SPEAKER_FLOOR == 0.595722615
    assert module.COLD_SEED_COHERENCE_FLOOR == 0.595722615
    assert module.SUPPLEMENTAL_REFERENCE_MARGIN == 0.03
    assert module.SUPPLEMENTAL_REFERENCE_SCORE_FLOOR == 0.55


def test_reference_selection_detects_full_audio_copies_and_never_embeds_them(tmp_path, monkeypatch):
    class Backend:
        calls = 0

        def embed(self, signal, sample_rate):
            self.calls += 1
            return np.asarray([1.0, 0.0], dtype=np.float32)

    backend = Backend()
    monkeypatch.setattr(module.exact, "read_pcm16", lambda path: np.zeros(module.exact.WINDOW_FRAMES, dtype=np.int16))
    references = []
    for index, full_audio in enumerate([b"candidate", b"production-a", b"production-a", b"production-b", b"production-c"]):
        original = tmp_path / f"source-{index}.wav"
        original.write_bytes(full_audio)
        clip = tmp_path / f"clip-{index}.wav"
        clip.write_bytes(b"clip")
        references.append({"event_id": f"apparently-distinct-event-{index}", "source_wav": str(original),
                           "wav": str(clip), "sha256": hashlib.sha256(b"clip").hexdigest()})
    source = {**SOURCE, "wav_sha256": hashlib.sha256(b"candidate").hexdigest()}
    vectors, selected, audit = module.load_independent_references(references, source, backend, {}, {})
    assert len(vectors) == len(selected) == backend.calls == 3
    assert len({row["source_audio_sha256"] for row in selected}) == 3
    assert audit["same_source_references_excluded_after_audio_hash_check"] == 1
    assert audit["duplicate_references_excluded_after_audio_hash_check"] == 1


@pytest.mark.parametrize("budget,source_count", [(0, 1), (18, 1), (26, 2)])
def test_main_budget_and_receipt_contract(tmp_path, monkeypatch, budget, source_count):
    class Backend:
        def embed(self, signal, sample_rate):
            return np.asarray([1.0, 0.0], dtype=np.float32)

    monkeypatch.setattr(module, "get_backend", lambda *args, **kwargs: Backend())
    monkeypatch.setattr(module.exact, "read_pcm16",
                        lambda path: np.zeros(200 * module.exact.SAMPLE_RATE, dtype=np.int16))
    monkeypatch.setattr(module.exact, "voice_activity", lambda signal: (1.0, 0.0))
    monkeypatch.setattr(module.exact, "overlapping_words", lambda words, start, end: [{}] * 100)
    wav = tmp_path / "recording.wav"
    wav.write_bytes(b"fake WAV for mocked decoder")
    transcript = tmp_path / "words.jsonl"
    transcript.write_text('{}\n', encoding="utf-8")
    candidates = tmp_path / "candidates.jsonl"
    rows = [{**SOURCE, "source_id": f"episode-{index}", "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED",
             "wav": str(wav), "wav_sha256": module.SMR.sha256_file(wav),
             "transcript": str(transcript), "transcript_sha256": module.SMR.sha256_file(transcript),
             "turns": [{"start_sec": 0, "end_sec": 200}]} for index in range(source_count)]
    candidates.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    vectors = tmp_path / "vault.npz"
    np.savez(vectors, members=np.asarray([SOURCE["member"], "Other Member"]),
             vectors=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    anchor = tmp_path / "anchor.json"
    anchor.write_text(json.dumps({"combined_vectors": str(vectors),
                                 "combined_vectors_sha256": module.SMR.sha256_file(vectors)}), encoding="utf-8")
    calibration = tmp_path / "calibration.json"
    calibration.write_text(json.dumps({"false_accepts": 0, "all_vaulted_speakers_compared": True,
                                      "per_claim_thresholds": {SOURCE["member"]: {
                                          "minimum_target_best_score": 0.6,
                                          "minimum_best_anchor_margin": 0.1}}}), encoding="utf-8")
    canonical = tmp_path / "canonical.jsonl"
    canonical.write_text("", encoding="utf-8")
    interviews = tmp_path / "interviews.json"
    interviews.write_text(json.dumps({"status": "DISPLAY_ONLY_NO_ENGINE_WIRING", "verified_sources": []}), encoding="utf-8")
    output, receipt = tmp_path / "output.jsonl", tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--input", str(candidates), "--output", str(output),
                                     "--receipt", str(receipt), "--anchor-receipt", str(anchor),
                                     "--calibration", str(calibration), "--canonical", str(canonical),
                                     "--verified-interviews", str(interviews),
                                     "--max-windows-per-member", str(budget)])
    assert module.main() == 0
    actual = module.SMR.read_jsonl(output, required=True)
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_data["coverage_contract_version"] == module.CONTINUITY_CONTRACT_VERSION
    assert receipt_data["contract"]["complete_turn_interior_coverage_required"] is True
    assert receipt_data["contract"]["max_windows_per_member"] == budget
    assert receipt_data["counts"]["evaluated_windows"] == (25 if budget == 0 else budget)
    if budget == 0:
        assert actual[0]["speaker_continuity_passed"] is True
        assert actual[0]["planned_windows"] == 25
        assert actual[0]["interior_coverage_complete"] is True
    else:
        assert actual[-1]["status"] == "INCOMPLETE_WINDOW_BUDGET"
        assert actual[-1]["speaker_continuity_passed"] is False
        assert actual[-1]["turn_coverage"][0]["continuity_gate_passed"] is False
        assert receipt_data["counts"]["incomplete_window_budget"] == 1
    if source_count == 2:
        assert len(actual[0]["evaluated_windows"]) == 25
        assert len(actual[1]["evaluated_windows"]) == 1

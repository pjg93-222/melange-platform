import copy
import hashlib
import json
from pathlib import Path
import wave

import pytest

from fec.speech import build_member_longform_review_queue as mod


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_nearest_preceding_cue_is_bound_to_turn():
    cues = [
        {"phrase": "old cue", "start_sec": 10, "end_sec": 11},
        {"phrase": "nearest cue", "start_sec": 40, "end_sec": 42},
        {"phrase": "later cue", "start_sec": 70, "end_sec": 72},
    ]
    assert mod.nearest_preceding_cue(cues, 50)["phrase"] == "nearest cue"
    assert mod.nearest_preceding_cue(cues, 400) is None


def make_queue_fixture(tmp_path, monkeypatch, *, change=None, two_sources=False, late_turn=False):
    wav = tmp_path / "source.wav"
    with wave.open(str(wav), "wb") as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16_000)
        target.writeframes(b"\x01\x00" * (16_000 * (500 if late_turn else 200)))
    transcript = tmp_path / "words.jsonl"
    write_jsonl(transcript, [
        {"word": "before", "start_sec": 0, "end_sec": 1},
        {"word": "answer", "start_sec": 10, "end_sec": 11},
        {"word": "continues", "start_sec": 20, "end_sec": 21},
        {"word": "after", "start_sec": 70, "end_sec": 71},
    ])
    digest = sha(transcript)
    selection = tmp_path / "selection.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    continuity = tmp_path / "continuity.jsonl"
    out = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    write_jsonl(selection, [{
        "member": "Example Member", "source_id": "abc", "status": "SELECTED_HARD_NAME_CUE_CANDIDATE",
        "selection": "primary", "source_era": "NOT_CURRENT_FORMAL_LEADERSHIP",
        "transcript": str(transcript), "transcript_sha256": digest,
        "wav": str(wav), "wav_sha256": sha(wav),
        "cues": [{"phrase": "Example Member", "start_sec": 1, "end_sec": 2}],
    }])
    candidate = {
        "member": "Example Member", "source_id": "abc", "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED",
        "transcript_sha256": digest, "wav_sha256": sha(wav), "turns": [
            {"start_sec": 10, "end_sec": 40, "duration_seconds": 30, "opening_words": "answer continues now for sure"},
            {"start_sec": 50, "end_sec": 110, "duration_seconds": 60, "opening_words": "answer continues now for sure"},
            {"start_sec": 120, "end_sec": 180, "duration_seconds": 60, "opening_words": "answer continues now for sure"},
        ],
    }
    if late_turn:
        candidate["turns"][2].update({"start_sec": 420, "end_sec": 480})
    windows = []
    for turn_index, turn in enumerate(candidate["turns"]):
        starts = mod.continuity_checks.candidate_starts(turn)
        for window_index, start in enumerate(starts):
            windows.append({"turn_index": turn_index, "window_index": window_index, "accepted": True,
                            "turn_binding_sha256": mod.continuity_checks.turn_binding_sha256(candidate, turn),
                            "start_sec": start, "end_sec": start + 8,
                            "anchor_eligible": abs(start - (starts[0] + window_index * 8)) < 1e-6})
    acoustic = {
        "member": "Example Member", "source_id": "abc", "wav_sha256": sha(wav),
        "transcript_sha256": digest, "method": "test", "anchored_member": True,
        "supplemental_member": False, "evaluated_windows": windows,
        "coverage_contract_version": mod.continuity_checks.CONTINUITY_CONTRACT_VERSION,
        "budget_exhausted": False,
    }
    acoustic["turn_coverage"] = [
        mod.continuity_checks.summarize_turn_coverage(candidate, turn, index, windows, budget_exhausted=False)
        for index, turn in enumerate(candidate["turns"])]
    if change:
        change(candidate, acoustic)
    candidate_rows, acoustic_rows = [candidate], [acoustic]
    if two_sources:
        selected = [json.loads(line) for line in selection.read_text(encoding="utf-8").splitlines()]
        selected.append({**selected[0], "source_id": "def"})
        write_jsonl(selection, selected)
        second_candidate, second_acoustic = copy.deepcopy(candidate), copy.deepcopy(acoustic)
        second_candidate["source_id"] = second_acoustic["source_id"] = "def"
        for window in second_acoustic["evaluated_windows"]:
            window["turn_binding_sha256"] = mod.continuity_checks.turn_binding_sha256(
                second_candidate, second_candidate["turns"][window["turn_index"]])
        second_acoustic["turn_coverage"] = [mod.continuity_checks.summarize_turn_coverage(
            second_candidate, turn, index, second_acoustic["evaluated_windows"], budget_exhausted=False)
            for index, turn in enumerate(second_candidate["turns"])]
        candidate_rows.append(second_candidate)
        acoustic_rows.append(second_acoustic)
    write_jsonl(candidates, candidate_rows)
    write_jsonl(continuity, acoustic_rows)
    monkeypatch.setattr("sys.argv", [
        "prog", "--selection", str(selection), "--candidates", str(candidates),
        "--continuity", str(continuity), "--out", str(out), "--receipt", str(receipt),
        "--audio-root", str(tmp_path / "review-audio"),
    ])
    assert mod.main() == 0
    return ([json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()],
            json.loads(receipt.read_text(encoding="utf-8"))), candidate, acoustic


def test_every_coverage_qualified_turn_is_retained_not_just_the_longest(tmp_path, monkeypatch):
    (rows, receipt), _, _ = make_queue_fixture(tmp_path, monkeypatch)
    assert [row["turn_index"] for row in rows] == [0, 1, 2]
    assert [row["duration_seconds"] for row in rows] == [30, 60, 60]
    assert len({row["turn_id"] for row in rows}) == 3
    assert len({row["review_audio_wav"] for row in rows}) == 3
    assert receipt["counts"]["members"] == 1
    assert receipt["counts"]["rows"] == 3
    assert receipt["contract"]["one_candidate_turn_per_member"] is False
    assert receipt["contract"]["all_cue_linked_coverage_qualified_turns_are_retained"] is True
    row = rows[0]
    assert row["status"] == "IDENTITY_BOUNDARY_SEMANTIC_REVIEW_REQUIRED"
    assert row["review_template"]["reviewer_type"] == "HUMAN"
    assert Path(row["review_audio_wav"]).is_file()
    assert row["selected_identity_cue"]["phrase"] == "Example Member"
    assert row["identity_cue_to_turn_gap_seconds"] == 8.0
    assert row["turn_start_in_review_audio_sec"] == 10.0
    assert row["turn_end_in_review_audio_sec"] == 40.0
    assert row["turn_coverage"]["maximum_consecutive_passing_windows"] == 3
    assert row["consecutive_acoustic_windows"] == 3
    template = row["review_template"]
    assert template["wav_sha256"] == row["wav_sha256"]
    assert template["transcript_sha256"] == row["transcript_sha256"]
    assert template["candidate_turn_binding_sha256"] == row["turn_coverage"]["turn_binding_sha256"]
    assert template["review_audio_sha256"] == row["review_audio_sha256"]
    assert set(template["semantic_findings"][0]) == {"kind", "finding", "scope"}


def test_middle_rejected_window_excludes_only_that_turn(tmp_path, monkeypatch):
    def reject_middle(candidate, acoustic):
        rejected = next(window for window in acoustic["evaluated_windows"]
                        if window["turn_index"] == 1 and window["window_index"] == 3)
        rejected["accepted"] = False
        rejected["refusal_reasons"] = ["half_voice_inconsistent"]
        acoustic["turn_coverage"][1] = mod.continuity_checks.summarize_turn_coverage(
            candidate, candidate["turns"][1], 1, acoustic["evaluated_windows"], budget_exhausted=False)
    (rows, _), _, _ = make_queue_fixture(tmp_path, monkeypatch, change=reject_middle)
    assert [row["turn_index"] for row in rows] == [0, 2]


@pytest.mark.parametrize("missing", ["coverage_contract_version", "turn_coverage"])
def test_legacy_early_window_artifacts_are_not_review_qualified(tmp_path, monkeypatch, missing):
    (rows, _), _, _ = make_queue_fixture(tmp_path, monkeypatch,
                                       change=lambda candidate, acoustic: acoustic.pop(missing))
    assert len(rows) == 1
    assert rows[0]["status"] == "NO_CUE_LINKED_TURN_WITH_SPEAKER_CONTINUITY"
    assert rows[0]["review_required"] is False


def test_explicit_budget_leaves_completed_earlier_turns_reviewable(tmp_path, monkeypatch):
    def limit_third_turn(candidate, acoustic):
        acoustic["budget_exhausted"] = True
        acoustic["evaluated_windows"] = [window for window in acoustic["evaluated_windows"]
                                         if window["turn_index"] != 2 or window["window_index"] < 3]
        acoustic["turn_coverage"] = [mod.continuity_checks.summarize_turn_coverage(
            candidate, turn, index, acoustic["evaluated_windows"], budget_exhausted=True)
            for index, turn in enumerate(candidate["turns"])]
    (rows, _), _, acoustic = make_queue_fixture(tmp_path, monkeypatch, change=limit_third_turn)
    assert [row["turn_index"] for row in rows] == [0, 1]
    assert acoustic["turn_coverage"][2]["status"] == "INCOMPLETE_WINDOW_BUDGET"


@pytest.mark.parametrize("change", ["stored_summary", "candidate_boundary", "candidate_transcript",
                                   "window_bounds", "duplicate_window"])
def test_coverage_binding_and_recomputed_summary_reject_tampering(tmp_path, monkeypatch, change):
    (_, _), candidate, acoustic = make_queue_fixture(tmp_path, monkeypatch)
    turn = candidate["turns"][0]
    assert mod.qualified_turn_coverage(candidate, acoustic, 0, turn) is not None
    if change == "stored_summary":
        acoustic["turn_coverage"][0]["planned_windows"] += 1
    elif change == "candidate_boundary":
        turn["end_sec"] += 1
    elif change == "candidate_transcript":
        candidate["transcript_sha256"] = "changed transcript"
    elif change == "window_bounds":
        acoustic["evaluated_windows"][0]["start_sec"] += 1
    else:
        acoustic["evaluated_windows"].append(dict(acoustic["evaluated_windows"][0]))
    assert mod.qualified_turn_coverage(candidate, acoustic, 0, turn) is None


def test_multiple_sources_for_same_member_keep_all_separate_turns(tmp_path, monkeypatch):
    (rows, receipt), _, _ = make_queue_fixture(tmp_path, monkeypatch, two_sources=True)
    assert len(rows) == 6
    assert {(row["source_id"], row["turn_index"]) for row in rows} == {
        (source_id, index) for source_id in ("abc", "def") for index in (0, 1, 2)}
    assert len({row["turn_id"] for row in rows}) == 6
    assert receipt["counts"]["members"] == 1
    assert receipt["counts"]["rows"] == 6


def test_ambiguous_duplicate_member_source_rows_are_refused():
    row = {"member": "Member", "source_id": "same-source"}
    with pytest.raises(SystemExit, match="duplicate candidate member/source binding"):
        mod.index_sources([row, dict(row)], "candidate")
    assert len(mod.index_sources([row, {**row, "source_id": "other-source"}], "candidate")) == 2


def test_late_answer_reports_cue_link_restriction_not_absent_audio(tmp_path, monkeypatch):
    (rows, receipt), _, _ = make_queue_fixture(tmp_path, monkeypatch, late_turn=True)
    assert [row["turn_index"] for row in rows] == [0, 1]
    diagnostics = rows[0]["source_turn_review_diagnostics"]
    assert diagnostics["whole_interior_coverage_qualified_turns"] == 3
    assert diagnostics["coverage_qualified_turns_without_nearby_name_cue"] == 1
    assert diagnostics["turns_without_complete_passing_interior_coverage"] == 0
    assert diagnostics["cue_link_restriction_is_not_evidence_of_missing_audio"] is True
    assert receipt["contract"]["maximum_preceding_name_cue_gap_seconds"] == 300.0

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_longform_turns_are_complete_variable_length_semantic_units():
    artifact_path = ROOT / "data/speech/member_interview_long_turns_v1.jsonl"
    receipt = json.loads(
        (ROOT / "data/speech/member_interview_long_turns_v1.receipt.json").read_text(encoding="utf-8")
    )
    rows = load_jsonl(artifact_path)

    assert receipt["status"] == "PASS"
    assert receipt["artifact_sha256"] == hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert receipt["counts"]["turns"] == 7
    assert receipt["counts"]["sources"] == 6
    assert receipt["counts"]["members"] == 3
    assert receipt["counts"]["turns_over_one_minute"] == 5
    assert receipt["counts"]["turns_over_two_minutes"] == 1
    assert receipt["counts"]["longest_turn_seconds"] == 135.0
    assert receipt["contract"]["maximum_turn_seconds"] is None
    assert receipt["contract"]["identity_windows_are_anchors_not_turns"] is True

    for row in rows:
        assert row["status"] == "HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED"
        assert row["contract"]["maximum_turn_seconds"] is None
        assert row["contract"]["short_identity_windows_define_turn_length"] is False
        turn_audio = Path(row["turn_audio_wav"])
        assert turn_audio.is_file()
        assert hashlib.sha256(turn_audio.read_bytes()).hexdigest() == row["turn_audio_sha256"]


def test_scalise_relationship_sequence_comes_from_complete_reviewed_turns():
    rows = load_jsonl(ROOT / "data/speech/member_interview_long_turns_v1.jsonl")
    steve = [row for row in rows if row["member"] == "Steve Scalise"]
    kinds = {
        finding["kind"] for row in steve for finding in row.get("semantic_findings") or []
    }
    transcript = " ".join(row["transcript_text"] for row in steve).casefold()
    assert "individual_conversation_before_decision" in kinds
    assert "familiarity_and_trust_in_working_relationships" in kinds
    assert "individual conversations" in transcript
    assert "trust" in transcript


import json
from pathlib import Path
import shutil
import subprocess
import wave

import pytest

from fec.speech import snapshot_member_longform_ready_sources as mod


def fixture_inputs(tmp_path, *, leader=False):
    root = tmp_path / "live"
    root.mkdir()
    wav = root / "audio.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * 16000 * 60)
    words = [{"word": value, "start_sec": i + .1, "end_sec": i + .8}
             for i, value in enumerate(["Example", "Member"] + [f"token{i}" for i in range(58)])]
    transcript = root / "words.jsonl"
    mod.SMR.write_jsonl_atomic(transcript, words)
    acquired = {"member": "Example Member", "source_id": "source-one", "url": "https://example.test/one",
                "wav": str(wav), "wav_sha256": mod.SMR.sha256_file(wav),
                "status": "ACQUIRED_IDENTITY_UNVERIFIED", "recorded_at": "2020-01-01"}
    row = {**acquired, "status": "LOCAL_ASR_READY_IDENTITY_UNVERIFIED", "transcript": str(transcript),
           "transcript_sha256": mod.SMR.sha256_file(transcript), "word_count": len(words)}
    docs = {
        "roster": {"members": {"Example Member": {}}},
        "roles": {"members": {"Example Member": {"current_role_assumed_on": "2023-01-03"}} if leader else {}},
        "leader_sources": [{"member": "Example Member", "source_id": "source-one", "recorded_at": "2020-01-01"}] if leader else [],
        "primary_queue": [{"member": "Example Member", "primary": {"source_id": "source-one"}}],
        "primary_acquisition": [acquired], "primary_transcripts": [row],
        "leader_acquisition": [acquired] if leader else [], "leader_transcripts": [row] if leader else [],
        "rejected_sources": [], "quality_holds": [],
    }
    paths = {}
    for key, doc in docs.items():
        paths[key] = root / (key + ".jsonl")
        if isinstance(doc, dict):
            mod.SMR.write_json_atomic(paths[key], doc)
        else:
            mod.SMR.write_jsonl_atomic(paths[key], doc)
    return paths, acquired, row


def test_snapshot_copies_words_and_leaves_canonical_files_unchanged(tmp_path):
    paths, acquired, row = fixture_inputs(tmp_path)
    before = {key: value.read_bytes() for key, value in paths.items()}
    run = tmp_path / "run"
    receipt = mod.build_snapshot(run, paths, ["Example Member"])
    assert receipt["counts"]["ready"] == 1
    assert {key: value.read_bytes() for key, value in paths.items()} == before
    candidate = mod.SMR.read_jsonl(run / "ready_sources.jsonl")[0]
    assert Path(candidate["transcript"]).is_relative_to(run / "words")
    assert candidate["transcript_sha256"] == row["transcript_sha256"]
    assert candidate["wav"] == acquired["wav"]
    assert candidate["speaker_identity_authority"] is False
    assert candidate["human_semantic_review"] is False
    assert mod.verify_snapshot(run)["status"] == "PASS_IMMUTABLE_CANDIDATE_SNAPSHOT"
    with pytest.raises(ValueError, match="already exists"):
        mod.build_snapshot(run, paths, ["Example Member"])


def test_live_asr_atomic_replacement_does_not_change_frozen_cohort(tmp_path, monkeypatch):
    paths, _, _ = fixture_inputs(tmp_path)
    original = mod.quality.inspect
    def progress(row, acquired):
        mod.SMR.write_jsonl_atomic(paths["primary_transcripts"], [])
        return original(row, acquired)
    monkeypatch.setattr(mod.quality, "inspect", progress)
    run = tmp_path / "run"
    receipt = mod.build_snapshot(run, paths, ["Example Member"])
    assert receipt["counts"]["ready"] == 1
    assert receipt["inputs"]["primary_transcripts"]["live_unchanged_at_finish"] is False
    assert mod.verify_snapshot(run)["counts"]["ready"] == 1


def test_authoritative_queue_change_during_snapshot_fails_closed(tmp_path, monkeypatch):
    paths, _, _ = fixture_inputs(tmp_path)
    original = mod.quality.inspect
    def changed_queue(row, acquired):
        mod.SMR.write_jsonl_atomic(paths["primary_queue"], [])
        return original(row, acquired)
    monkeypatch.setattr(mod.quality, "inspect", changed_queue)
    run = tmp_path / "run"
    with pytest.raises(ValueError, match="authoritative input changed"):
        mod.build_snapshot(run, paths, ["Example Member"])
    assert not (run / "snapshot.receipt.json").exists()


@pytest.mark.parametrize("case, reason", [
    ("stale", "STALE_TRANSCRIPT_SOURCE_BINDING"),
    ("queue", "CURRENT_PRIMARY_QUEUE_MISMATCH"),
    ("rejected", "SUPERSEDED_OR_REJECTED_SOURCE"),
    ("hold", "QUALITY_HOLD_REQUIRES_SEPARATE_REPAIR_REVIEW"),
    ("physical", "PHYSICAL_OR_LINEAGE_VALIDATION_FAILED"),
    ("quality", "DIAGNOSTIC_RISK_REQUIRES_REVIEW"),
])
def test_snapshot_exclusions_are_not_silent_approvals(tmp_path, case, reason):
    paths, acquired, row = fixture_inputs(tmp_path)
    if case == "stale":
        mod.SMR.write_jsonl_atomic(paths["primary_transcripts"], [{**row, "wav_sha256": "wrong"}])
    elif case == "queue":
        mod.SMR.write_jsonl_atomic(paths["primary_queue"], [{"member": row["member"], "primary": {"source_id": "different"}}])
    elif case == "rejected":
        mod.SMR.write_jsonl_atomic(paths["rejected_sources"], [{"member": row["member"], "source_id": acquired["source_id"]}])
    elif case == "hold":
        # A repaired transcript with another hash is not enough to clear a hold.
        mod.SMR.write_jsonl_atomic(paths["quality_holds"], [{**row, "transcript_sha256": "old-held-words"}])
    elif case == "physical":
        Path(row["transcript"]).write_text("changed", encoding="utf-8")
    elif case == "quality":
        mod.SMR.write_jsonl_atomic(paths["primary_transcripts"], [{**row, "word_count": 1}])
    run = tmp_path / "run"
    receipt = mod.build_snapshot(run, paths, [row["member"]])
    assert receipt["status"] == "NO_READY_SOURCES"
    assert receipt["counts"]["exclusion_reasons"] == {reason: 1}
    with pytest.raises(ValueError, match="does not authorize"):
        mod.verify_snapshot(run)


def test_leader_uses_only_reviewed_pre_role_source(tmp_path):
    paths, _, _ = fixture_inputs(tmp_path, leader=True)
    run = tmp_path / "run"
    assert mod.build_snapshot(run, paths, ["Example Member"])["counts"]["ready"] == 1
    row = mod.SMR.read_jsonl(run / "ready_sources.jsonl")[0]
    assert row["source_era"] == "PRE_CURRENT_LEADERSHIP_ROLE"
    role = {"current_role_assumed_on": "2020-01-01"}
    mod.SMR.write_json_atomic(paths["roles"], {"members": {"Example Member": role}})
    receipt = mod.build_snapshot(tmp_path / "late", paths, ["Example Member"])
    assert receipt["counts"]["exclusion_reasons"] == {"LEADERSHIP_PREROLE_CUTOFF_VIOLATION": 1}


def test_snapshot_verification_rejects_word_file_mutation(tmp_path):
    paths, _, _ = fixture_inputs(tmp_path)
    run = tmp_path / "run"
    mod.build_snapshot(run, paths, None)
    word_path = next((run / "words").glob("*.jsonl"))
    word_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="word file"):
        mod.verify_snapshot(run)


@pytest.mark.parametrize("overrun, ready", [(0.09, 1), (0.101, 0), (1.734, 0)])
def test_snapshot_uses_classifier_physical_end_tolerance_without_clipping(tmp_path, overrun, ready):
    paths, _, row = fixture_inputs(tmp_path)
    transcript = Path(row["transcript"])
    words = mod.SMR.read_jsonl(transcript)
    words[-1]["end_sec"] = 60.0 + overrun
    mod.SMR.write_jsonl_atomic(transcript, words)
    row["transcript_sha256"] = mod.SMR.sha256_file(transcript)
    mod.SMR.write_jsonl_atomic(paths["primary_transcripts"], [row])
    original = transcript.read_bytes()
    run = tmp_path / "run"
    receipt = mod.build_snapshot(run, paths, [row["member"]])
    assert receipt["counts"]["ready"] == ready
    assert transcript.read_bytes() == original
    diagnostics = mod.SMR.read_jsonl(run / "source_diagnostics.jsonl")
    flags = {flag["code"] for flag in diagnostics[0]["quality"]["flags"]}
    assert ("TIMESTAMPS_BEYOND_AUDIO" in flags) is (not bool(ready))


def test_runner_is_candidate_only_and_uses_unlimited_continuity():
    script = mod.ROOT / "fec/speech/continue_member_longform_snapshot_candidates.ps1"
    text = script.read_text(encoding="utf-8")
    assert "[int]$CpuThreads = 2" in text
    assert "'--max-windows-per-member', '0'" in text
    assert "whole-turn-interior-coverage-v2" in text
    assert "--verify-run-root" in text
    assert "--preflight-only" in text
    assert text.index("'--preflight-only'") < text.index("Invoke-CandidatePython -Arguments $CandidateClassifierArgs")
    assert text.index("--verify-run-root") < text.index("'-u', '-m', 'fec.speech.classify_member_longform_full_turns_local'")
    for prohibited in ("fec.speech.acquire_member_longform_audio", "materialize_member_longform_reviewed_turns",
                       "build_member_meeting_playbooks", "make_member_desk", "draft_member_longform_reviews"):
        assert prohibited not in text
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell:
        escaped = str(script).replace("'", "''")
        command = "$t=$null; $e=$null; [System.Management.Automation.Language.Parser]::ParseFile('" + escaped + "',[ref]$t,[ref]$e) | Out-Null; if($e.Count){$e | Out-String; exit 1}"
        result = subprocess.run([shell, "-NoProfile", "-Command", command], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

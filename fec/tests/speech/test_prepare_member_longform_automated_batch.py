import json
from pathlib import Path

import pytest

from fec.speech import prepare_member_longform_automated_batch as mod
from fec.tests.speech.test_snapshot_member_longform_ready_sources import fixture_inputs


@pytest.mark.parametrize("kind,expected", [
    ("asr", "REUSE_ASR_FOR_AUTOMATED_PROCESSING"),
    ("caption", "RETRANSCRIBE_ORIGINAL_AUDIO"),
    ("stale", "WAITING_FOR_EXISTING_COLLECTION_WORKER"),
    ("bad_end", "RETRANSCRIBE_ORIGINAL_AUDIO"),
])
def test_isolated_roster_batch_preserves_live_inputs_and_schedules_real_sources(tmp_path, monkeypatch, kind, expected):
    paths, acquired, transcript = fixture_inputs(tmp_path)
    if kind == "caption":
        transcript["status"] = "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED"
    if kind == "stale":
        transcript["wav_sha256"] = "previous-source"
    if kind == "bad_end":
        words = mod.SMR.read_jsonl(Path(transcript["transcript"]))
        words[-1]["end_sec"] = 61.7
        mod.SMR.write_jsonl_atomic(Path(transcript["transcript"]), words)
        transcript["transcript_sha256"] = mod.SMR.sha256_file(Path(transcript["transcript"]))
    mod.SMR.write_jsonl_atomic(paths["primary_transcripts"], [transcript])
    run = tmp_path / "snapshot"
    mod.snapshot.build_snapshot(run, paths, None)
    # A zero-ready source snapshot is still a valid input for repair planning;
    # tests use a directly checked frozen-input receipt, not identity approval.
    monkeypatch.setattr(mod.snapshot, "verify_snapshot", lambda _: None)
    monkeypatch.setattr(mod.selector, "DEFAULT_PRIMARY_TRANSCRIPTS", paths["primary_transcripts"])
    before = {key: path.read_bytes() for key, path in paths.items()}
    target = tmp_path / "batch"
    monkeypatch.setattr("sys.argv", ["prog", "--snapshot", str(run), "--out-root", str(target)])
    assert mod.main() == 0
    report = json.loads((target / "prepare.receipt.json").read_text())
    assert report["active_members"] == 1
    assert report["counts"] == {expected: 1}
    assert {key: path.read_bytes() for key, path in paths.items()} == before
    assert len(mod.SMR.read_jsonl(target / "acquisitions.jsonl")) == 1
    assert report["contract"]["live_manifests_written"] is False
    with pytest.raises(ValueError, match="new isolated"):
        mod.main()


def test_acquisition_only_check_does_not_require_or_invent_transcript(tmp_path):
    paths, acquired, _ = fixture_inputs(tmp_path)
    queue = mod.SMR.read_jsonl(paths["primary_queue"])[0]
    assert mod.snapshot.source_problem("Example Member", acquired, None, queue, None, None, [], [], acquisition_only=True) is None
    assert mod.snapshot.source_problem("Example Member", acquired, None, queue, None, None, [], []) == "TRANSCRIPT_PENDING"
    assert mod.snapshot.source_problem("Example Member", acquired, None, queue, None, None,
        [{"member": "Example Member", "source_id": acquired["source_id"]}], [], acquisition_only=True) == "SUPERSEDED_OR_REJECTED_SOURCE"

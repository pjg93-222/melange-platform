import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import types
import wave

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/transcribe_member_longform_local_asr.py"
spec = importlib.util.spec_from_file_location("member_longform_local_asr", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_pending_rows_only_selects_explicit_local_asr_gaps():
    rows = [
        {"member": "One", "status": "LOCAL_ASR_REQUIRED"},
        {"member": "Two", "status": "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED"},
        {"member": "Three", "status": "TRANSCRIPT_ERROR"},
    ]
    assert [row["member"] for row in module.pending_rows(rows, set(), False)] == ["One"]
    assert [row["member"] for row in module.pending_rows(rows, {"Three"}, True)] == ["Three"]


def test_source_mismatch_is_automatically_retranscribed():
    rows = [{
        "member": "One", "status": "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED",
        "source_id": "old", "wav_sha256": "old-hash",
    }]
    acquisitions = {"One": {"source_id": "new", "wav_sha256": "new-hash"}}
    assert module.pending_rows(rows, set(), False, acquisitions) == rows
    assert module.transcript_matches_acquisition(rows[0], acquisitions["One"]) is False


def test_source_match_requires_both_id_and_hash():
    row = {"source_id": "same", "wav_sha256": "hash-a"}
    assert module.transcript_matches_acquisition(
        row, {"source_id": "same", "wav_sha256": "hash-a"}
    ) is True
    assert module.transcript_matches_acquisition(
        row, {"source_id": "same", "wav_sha256": "hash-b"}
    ) is False


def test_local_asr_output_is_explicitly_non_authoritative():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"speaker_identity_authority": False' in text
    assert '"transcript_identity_authority": False' in text
    assert '"transcript_establishes_speaker_identity": False' in text


def test_wav_duration_seconds_uses_header_without_decoding(tmp_path):
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(b"\x00\x00" * 32_000)
    assert module.wav_duration_seconds(path) == 2.0


def test_automatic_language_is_default_and_is_not_english(tmp_path):
    settings = module.asr_request_settings(tmp_path, "auto")
    assert settings["requested_language"] is None
    assert settings["task"] == "transcribe_original_language"
    assert 'parser.add_argument("--language", default="auto"' in SCRIPT.read_text(encoding="utf-8")


def test_language_changes_cannot_reuse_the_same_cache(tmp_path):
    auto = module.asr_request_settings(tmp_path, "auto")
    spanish = module.asr_request_settings(tmp_path, "es")
    english = module.asr_request_settings(tmp_path, "en")
    assert spanish["requested_language"] == "es"
    assert len({module.request_cache_key(x) for x in (auto, spanish, english)}) == 3


def test_model_path_changes_cache_binding(tmp_path):
    assert module.request_cache_key(module.asr_request_settings(tmp_path / "one", "auto")) != module.request_cache_key(module.asr_request_settings(tmp_path / "two", "auto"))


def test_stale_ready_rows_remain_visible_in_completion_check():
    acquisitions = {"One": {"source_id": "new", "wav_sha256": "new-hash"}}
    old = [{"member": "One", "status": "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED",
            "source_id": "old", "wav_sha256": "old-hash"}]
    assert module.source_binding_mismatches(old, acquisitions) == ["One"]
    assert module.source_binding_mismatches([], acquisitions) == ["One"]
    assert module.source_binding_mismatches([{"member": "One", **acquisitions["One"]}], acquisitions) == []


def test_refresh_selects_ready_rows_only_for_explicit_members():
    rows = [{"member": "One", "status": "LOCAL_ASR_READY_IDENTITY_UNVERIFIED"},
            {"member": "Two", "status": "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED"}]
    assert module.pending_rows(rows, {"Two"}, False, refresh=True) == [rows[1]]
    with pytest.raises(ValueError, match="explicit members"):
        module.pending_rows(rows, set(), False, refresh=True)


def test_refresh_cli_requires_explicit_members_before_reading_inputs(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["asr", "--refresh"])
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 2


def test_conditioning_change_uses_a_different_cache_key(tmp_path):
    conditioned = module.asr_request_settings(tmp_path, "auto")
    repair = module.asr_request_settings(tmp_path, "auto", False)
    assert conditioned["condition_on_previous_text"] is True
    assert repair["condition_on_previous_text"] is False
    assert module.request_cache_key(conditioned) != module.request_cache_key(repair)


def test_targeted_refresh_controls_backend_and_preserves_prior_transcripts(tmp_path, monkeypatch):
    wav = tmp_path / "source.wav"
    wav.write_bytes(b"synthetic-audio-hash-fixture")
    old = tmp_path / "old.words.jsonl"
    old.write_text("original transcript remains available", encoding="utf-8")
    original_bytes = old.read_bytes()
    source = {"member": "One", "source_id": "source-one", "status": "ACQUIRED_IDENTITY_UNVERIFIED",
              "wav": str(wav), "wav_sha256": hashlib.sha256(wav.read_bytes()).hexdigest()}
    acquisition, transcript, receipt = [tmp_path / name for name in ("acquisition.jsonl", "manifest.jsonl", "receipt.json")]
    acquisition.write_text(json.dumps(source) + "\n", encoding="utf-8")
    transcript.write_text(json.dumps({**source, "status": "LOCAL_ASR_READY_IDENTITY_UNVERIFIED",
                                    "transcript": str(old)}) + "\n", encoding="utf-8")
    calls = []
    class FakeModel:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            words = [types.SimpleNamespace(word=f" token{i}", start=float(i), end=i + .25,
                                           probability=.99) for i in range(25)]
            return [types.SimpleNamespace(words=words)], types.SimpleNamespace(language="es")
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=lambda *a, **k: FakeModel()))
    monkeypatch.setattr(module, "install_av_policy_shim", lambda: None)
    monkeypatch.setattr(module, "pcm16_audio", lambda path: "fake decoded audio")
    monkeypatch.setattr(sys, "argv", ["asr", "--acquisition", str(acquisition), "--transcripts", str(transcript),
                        "--receipt", str(receipt), "--model", str(tmp_path / "model"),
                        "--transcript-root", str(tmp_path / "asr"), "--refresh", "--member", "One",
                        "--no-condition-on-previous-text", "--language", "auto"])
    result_paths = []
    for _ in range(2):
        assert module.main() == 0
        row = module.SMR.read_jsonl(transcript, required=True)[0]
        result_paths.append(row["transcript"])
        assert row["detected_language"] == "es"
        assert row["asr_request_settings"]["condition_on_previous_text"] is False
        assert row["transcript_identity_authority"] is False
    assert len(calls) == 2
    assert all(call["condition_on_previous_text"] is False and call["language"] is None for call in calls)
    assert result_paths[0] != result_paths[1]
    assert all(Path(path).exists() for path in result_paths)
    assert old.read_bytes() == original_bytes

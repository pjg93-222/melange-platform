import json
import sys
import types
from pathlib import Path

from fec.speech import source_media_recovery as SMR
from fec.speech import transcribe_local_youtube_holdings as T


def _run(tmp_path, monkeypatch, manifest, existing=None):
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=lambda *a, **k: object()))
    census = tmp_path / "census.jsonl"
    census.write_text(json.dumps({"member": "A", "verified_audio_profile": False}) + "\n")
    man = tmp_path / "manifest.jsonl"
    man.write_text("".join(json.dumps(r) + "\n" for r in manifest))
    out = tmp_path / "out.jsonl"
    if existing:
        out.write_text("".join(json.dumps(r) + "\n" for r in existing))
    calls = []

    def fake_load(acq, model, audio):
        calls.append(acq["wav"])
        tr = tmp_path / "t.words.jsonl"
        tr.write_text("{}\n")
        return [{}], {"transcript": str(tr), "transcript_sha256": SMR.sha256_file(tr)}

    monkeypatch.setattr(SMR, "load_or_create_asr_transcript", fake_load)
    monkeypatch.setattr(T, "pcm16_audio", lambda p: None)
    rc = T.main_with(["--manifest", str(man), "--census", str(census), "--model", str(tmp_path),
                      "--asr-root", str(tmp_path / "asr"), "--output", str(out),
                      "--receipt", str(tmp_path / "r.json")])
    return rc, json.loads((tmp_path / "r.json").read_text()), calls


def test_missing_wav_is_incomplete(tmp_path, monkeypatch):
    rows = [{"member": "A", "video_id": "v1", "wav": str(tmp_path / "gone.wav")}]
    rc, receipt, _ = _run(tmp_path, monkeypatch, rows)
    assert rc != 0
    assert receipt["status"] == "INCOMPLETE_MISSING_SOURCE_WAV"
    assert receipt["coverage"]["missing_wav"] == 1


def test_stale_wav_hash_is_reprocessed(tmp_path, monkeypatch):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"new")
    existing = [{"member": "A", "video_id": "v1", "source_wav": str(wav), "source_audio_sha256": "bad",
                 "transcript": str(tmp_path / "nope"), "transcript_sha256": "x"}]
    rows = [{"member": "A", "video_id": "v1", "wav": str(wav)}]
    rc, receipt, calls = _run(tmp_path, monkeypatch, rows, existing)
    assert rc == 0 and calls and receipt["status"] == "PASS_LOCALIZATION_ONLY"
    assert receipt["coverage"]["missing_wav"] == 0


def test_missing_transcript_is_reprocessed(tmp_path, monkeypatch):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"same")
    existing = [{"member": "A", "video_id": "v1", "source_wav": str(wav.resolve()),
                 "source_audio_sha256": SMR.sha256_file(wav),
                 "transcript": str(tmp_path / "gone.words.jsonl"), "transcript_sha256": "x"}]
    rows = [{"member": "A", "video_id": "v1", "wav": str(wav)}]
    _, _, calls = _run(tmp_path, monkeypatch, rows, existing)
    assert calls


def test_repointed_manifest_wav_is_reprocessed(tmp_path, monkeypatch):
    old_wav, new_wav = tmp_path / "old.wav", tmp_path / "new.wav"
    old_wav.write_bytes(b"old")
    new_wav.write_bytes(b"new")
    tr = tmp_path / "keep.words.jsonl"
    tr.write_text("{}\n")
    existing = [{"member": "A", "video_id": "v1", "source_wav": str(old_wav.resolve()),
                 "source_audio_sha256": SMR.sha256_file(old_wav),
                 "transcript": str(tr), "transcript_sha256": SMR.sha256_file(tr)}]
    rows = [{"member": "A", "video_id": "v1", "wav": str(new_wav)}]
    _, _, calls = _run(tmp_path, monkeypatch, rows, existing)
    assert calls

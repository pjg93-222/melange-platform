import importlib.util
import hashlib
import json
import wave
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/acquire_member_longform_audio.py"
spec = importlib.util.spec_from_file_location("longform_acquire", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_safe_name_is_stable_and_bounded():
    assert module.safe_name("Pablo Hernández Rivera") == "pablo-hern-ndez-rivera"
    assert len(module.safe_name("X" * 200)) == 80


def test_wav_details_enforces_locked_pcm_contract(tmp_path):
    path = tmp_path / "good.wav"
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(b"\x00\x00" * 16_000)
    assert module.wav_details(path) == (1.0, 16_000)


def test_acquisition_does_not_establish_identity():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"speaker_identity_authority": False' in text
    assert '"acquisition_establishes_speaker_identity": False' in text
    assert "promotion_requires_asr_in_band_name_full_turns_and_speaker_gate" in text


def test_provider_resolution_rows_can_be_retried_after_url_resolution():
    provider = {"status": "PROVIDER_MEDIA_RESOLUTION_REQUIRED"}
    error = {"status": "ACQUISITION_ERROR"}
    assert module.retry_existing(
        provider, retry_errors=False, retry_provider_resolution=True
    ) is True
    assert module.retry_existing(
        error, retry_errors=True, retry_provider_resolution=False
    ) is True
    assert module.retry_existing(
        provider, retry_errors=False, retry_provider_resolution=False
    ) is False


def test_acquire_one_reuses_an_existing_normalized_candidate(tmp_path):
    member = "Test Member"
    video_id = "abc123"
    target = tmp_path / f"{module.safe_name(member)}__{video_id}.wav"
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * 16_000)
    row = {
        "member": member,
        "acquisition_route": "youtube_audio_download",
        "primary": {
            "source_type": "youtube", "video_id": video_id,
            "url": "https://example.invalid/video", "title": "Example", "channel": "Test",
        },
        "alternatives": [],
    }
    result = module.acquire_one(row, tmp_path, {})
    assert result["status"] == "ACQUIRED_IDENTITY_UNVERIFIED"
    assert result["reused_existing_normalized_wav"] is True
    assert result["measured_duration_seconds"] == 1.0
    assert result["wav_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_refresh_flag_is_scoped_to_explicit_members():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'parser.error("--refresh requires at least one --member")' in text


def test_normalized_url_ignores_fragment_query_order_and_default_port():
    left = "HTTPS://Example.COM:443/episode/?b=two&a=one#clip"
    right = "https://example.com/episode?a=one&b=two"
    assert module.normalized_url(left) == module.normalized_url(right)


def test_resume_accepts_a_normal_fallthrough_alternative():
    queue_row = {
        "member": "Test Member",
        "primary": {
            "source_type": "youtube",
            "video_id": "primary-id",
            "url": "https://www.youtube.com/watch?v=primary-id",
        },
        "alternatives": [{
            "source_type": "youtube",
            "video_id": "fallback-id",
            "url": "https://www.youtube.com/watch?v=fallback-id",
        }],
    }
    existing = {
        "status": "ACQUIRED_IDENTITY_UNVERIFIED",
        "source_id": "fallback-id",
        "url": "https://www.youtube.com/watch?v=fallback-id",
    }
    assert module.acquisition_matches_queue(existing, queue_row) is True
    assert module.should_acquire(
        queue_row, existing, retry_errors=False, retry_provider_resolution=False
    ) is False


def test_reviewed_replacement_rejects_an_old_alternative_and_forces_reacquisition():
    queue_row = {
        "member": "Test Member",
        "primary": {
            "source_type": "youtube",
            "video_id": "reviewed-id",
            "url": "https://www.youtube.com/watch?v=reviewed-id",
            "reviewed_replacement": True,
        },
        "alternatives": [{
            "source_type": "youtube",
            "video_id": "stale-id",
            "url": "https://www.youtube.com/watch?v=stale-id",
        }],
    }
    existing = {
        "status": "ACQUIRED_IDENTITY_UNVERIFIED",
        "source_id": "stale-id",
        "url": "https://www.youtube.com/watch?v=stale-id",
    }
    assert module.acquisition_matches_queue(existing, queue_row) is False
    assert module.should_acquire(
        queue_row, existing, retry_errors=False, retry_provider_resolution=False
    ) is True


def test_reviewed_replacement_matches_normalized_primary_audio_url():
    queue_row = {
        "member": "Test Member",
        "primary": {
            "source_type": "apple_directory_audio",
            "url": "https://podcasts.example/episode",
            "audio_url": "HTTPS://Media.Example:443/show.mp3?b=2&a=1#ignored",
            "reviewed_replacement": True,
        },
        "alternatives": [],
    }
    existing = {
        "status": "ACQUIRED_IDENTITY_UNVERIFIED",
        "source_id": "legacy-key",
        "audio_url": "https://media.example/show.mp3?a=1&b=2",
    }
    assert module.acquisition_matches_queue(existing, queue_row) is True
    assert module.should_acquire(
        queue_row, existing, retry_errors=False, retry_provider_resolution=False
    ) is False


def test_direct_audio_source_key_remains_resume_matchable_without_stored_audio_url():
    candidate = {
        "source_type": "apple_directory_audio",
        "url": "https://podcasts.example/episode",
        "audio_url": "https://media.example/show.mp3?token=stable",
        "reviewed_replacement": True,
    }
    queue_row = {"member": "Test Member", "primary": candidate, "alternatives": []}
    existing = {
        "status": "ACQUIRED_IDENTITY_UNVERIFIED",
        "source_id": module.candidate_source_key(candidate),
        "url": candidate["url"],
    }
    assert module.acquisition_matches_queue(existing, queue_row) is True


def _candidate_row():
    return {
        "member": "Test Member", "acquisition_route": "youtube_audio_download",
        "primary": {"source_type": "youtube", "video_id": "reviewed-id",
                    "url": "https://www.youtube.com/watch?v=reviewed-id",
                    "reviewed_replacement": True},
        "alternatives": [{"source_type": "youtube", "video_id": "wrong-person",
                          "url": "https://www.youtube.com/watch?v=wrong-person"}],
    }


def _write_wav(path, sample=b"\x00\x00"):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(sample * 16_000)


def test_reviewed_primary_cannot_fall_through_to_an_unreviewed_source(tmp_path, monkeypatch):
    attempted = []
    def unavailable(candidate, target):
        attempted.append(candidate["video_id"])
        raise RuntimeError("unavailable")
    monkeypatch.setattr(module, "acquire_youtube", unavailable)
    with pytest.raises(RuntimeError, match="all downloadable alternatives failed"):
        module.acquire_one(_candidate_row(), tmp_path, {})
    assert attempted == ["reviewed-id"]


def test_invalid_existing_wav_is_retained(tmp_path):
    target = tmp_path / "test-member__reviewed-id.wav"
    original = b"not a WAV; retain for inspection"
    target.write_bytes(original)
    with pytest.raises(RuntimeError, match="all downloadable alternatives failed"):
        module.acquire_one(_candidate_row(), tmp_path, {})
    assert target.read_bytes() == original


def test_resume_rechecks_missing_or_modified_media(tmp_path):
    row = _candidate_row()
    target = tmp_path / "test-member__reviewed-id.wav"
    _write_wav(target)
    existing = {"status": "ACQUIRED_IDENTITY_UNVERIFIED", "source_id": "reviewed-id",
                "wav": str(target), "wav_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    kwargs = {"retry_errors": True, "retry_provider_resolution": True, "verify_media": True}
    assert module.should_acquire(row, existing, **kwargs) is False
    _write_wav(target, b"\x01\x00")
    assert module.should_acquire(row, existing, **kwargs) is True
    existing["wav"] = str(tmp_path / "missing.wav")
    assert module.should_acquire(row, existing, **kwargs) is True


def test_hash_mismatch_is_not_laundered_on_a_second_failed_retry(tmp_path, monkeypatch):
    row = _candidate_row()
    target = tmp_path / "test-member__reviewed-id.wav"
    _write_wav(target)
    prior = {"member": row["member"], "status": "ACQUIRED_IDENTITY_UNVERIFIED",
             "source_id": "reviewed-id", "wav": str(target),
             "wav_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    _write_wav(target, b"\x02\x00")
    altered_bytes = target.read_bytes()
    queue, output, receipt, verified = [tmp_path / name for name in ("queue.jsonl", "out.jsonl", "receipt.json", "verified.jsonl")]
    queue.write_text(json.dumps(row) + "\n", encoding="utf-8")
    output.write_text(json.dumps(prior) + "\n", encoding="utf-8")
    verified.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["acquire", "--queue", str(queue), "--out", str(output),
                        "--receipt", str(receipt), "--verified-sources", str(verified),
                        "--media-root", str(tmp_path), "--resume", "--retry-errors"])
    for _ in range(2):
        assert module.main() == 0
        result = module.read_jsonl(output)[0]
        assert result["status"] == "ACQUISITION_ERROR"
        assert result["previous_acquisition"]["wav_sha256"] == prior["wav_sha256"]
        assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "PARTIAL_VISIBLE"
        assert target.read_bytes() == altered_bytes


def test_receipt_reconciliation_detects_wrong_source_and_invalid_audio(tmp_path):
    row = _candidate_row()
    target = tmp_path / "wrong.wav"
    _write_wav(target)
    existing = {"member": row["member"], "status": "ACQUIRED_IDENTITY_UNVERIFIED",
                "source_id": "wrong-person", "wav": str(target), "wav_sha256": "incorrect"}
    report = module.reconcile_acquisitions([row], [existing])
    assert report["source_mismatch_members"] == [row["member"]]
    assert report["invalid_media"] == {row["member"]: "WAV hash mismatch"}

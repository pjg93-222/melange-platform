import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/transcribe_member_longform_captions.py"
spec = importlib.util.spec_from_file_location("longform_captions", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_json3_caption_events_become_timestamped_words():
    payload = {"events": [{"tStartMs": 1000, "dDurationMs": 2000,
                           "segs": [{"utf8": "Hello there"}, {"utf8": "member", "tOffsetMs": 1000}]}]}
    words = module.json3_words(payload)
    assert [row["word"] for row in words] == ["Hello", "there", "member"]
    assert words[0]["start_sec"] == 1.0
    assert words[-1]["end_sec"] == 3.0
    assert all(row["timing_method"] == "caption_segment_linear_interpolation" for row in words)
    assert all(row["timing_is_estimated"] is True for row in words)
    assert all(row["word_alignment_verified"] is False for row in words)


def test_original_english_caption_is_preferred_over_translated_track():
    info = {
        "automatic_captions": {
            "en": [{"ext": "json3", "url": "translated"}],
            "en-orig": [{"ext": "json3", "url": "original"}],
        }
    }
    assert module.english_json3(info) == ("automatic", "en-orig", "original")


def test_transcript_api_word_expansion_uses_snippet_duration(monkeypatch):
    class Snippet:
        text = "hello there member"
        start = 10.0
        duration = 3.0

    class Fetched(list):
        is_generated = True

    fetched = Fetched([Snippet()] * 20)

    class Api:
        def fetch(self, video_id, languages):
            assert video_id == "abc"
            assert "en" in languages
            return fetched

    import youtube_transcript_api
    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", lambda: Api())
    kind, words = module.transcript_api_words("abc")
    assert kind == "automatic"
    assert len(words) == 60
    assert words[0]["start_sec"] == 10.0
    assert words[2]["end_sec"] == 13.0
    assert all(row["timing_method"] == "caption_snippet_linear_interpolation" for row in words)
    assert all(row["timing_is_estimated"] is True for row in words)
    assert all(row["word_alignment_verified"] is False for row in words)


def test_caption_process_preserves_timing_limits_in_transcript_ledger(tmp_path, monkeypatch):
    words = module.json3_words({"events": [{
        "tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "hello member"}]
    }]})
    monkeypatch.setattr(module, "transcript_api_words", lambda _source: ("manual", words))
    monkeypatch.setattr(module, "ytdlp_caption_words", lambda _url: ("manual", words))
    result = module.process({
        "source_id": "example", "source_type": "youtube",
        "status": "ACQUIRED_IDENTITY_UNVERIFIED", "url": "https://example.test/interview"
    }, tmp_path, {})
    assert result["timing_is_estimated"] is True
    assert result["word_alignment_verified"] is False
    assert result["timing_methods"] == ["caption_segment_linear_interpolation"]
    assert result["speaker_identity_authority"] is False


def test_only_definitive_caption_absence_routes_to_local_asr():
    NoTranscriptFound = type("NoTranscriptFound", (Exception,), {})
    RequestBlocked = type("RequestBlocked", (Exception,), {})
    assert module.caption_definitively_absent(NoTranscriptFound()) is True
    assert module.caption_definitively_absent(RequestBlocked()) is False


def test_caption_contract_does_not_promote_identity():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"caption_establishes_speaker_identity": False' in text
    assert '"transcript_identity_authority": False' in text
    assert "promotion_requires_in_band_name_full_turns_and_speaker_gate" in text


def test_changed_acquisition_invalidates_resumed_transcript():
    acquired = {"source_id": "new", "wav_sha256": "new-hash"}
    existing = {"source_id": "old", "wav_sha256": "old-hash", "status": "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED"}
    assert module.transcript_needs_processing(acquired, existing, False, False) is True
    assert module.transcript_needs_processing(acquired, acquired, False, False) is False


def test_member_filter_without_resume_keeps_other_members_rows(tmp_path, monkeypatch):
    import json
    import sys
    acquisition = tmp_path / "acq.jsonl"
    acquisition.write_text(json.dumps({
        "member": "A", "status": "ACQUIRED_IDENTITY_UNVERIFIED", "source_id": "s"}) + "\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    keep = {"member": "B", "status": "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED"}
    out.write_text(json.dumps(keep) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "x", "--acquisition", str(acquisition), "--existing-asr", str(tmp_path / "none.jsonl"),
        "--out", str(out), "--receipt", str(tmp_path / "r.json"),
        "--member", "NoSuchMember"])
    assert module.main() == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["member"] for row in rows] == ["B"]

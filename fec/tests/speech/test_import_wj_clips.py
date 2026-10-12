from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from fec.speech import import_wj_clips as WJ


class FakeIdentity:
    def expected_guest(self, title, date, names):
        return "Target Member"

    def attribute(self, label, title, date, expected=None):
        return SimpleNamespace(member="Target Member" if label == "Target Label" else None)


def transcript(tmp_path: Path, turns: list[dict]) -> Path:
    root = tmp_path / "transcripts"
    root.mkdir()
    (root / "1.json").write_text(
        json.dumps(
            {
                "title": "Representative Target Member",
                "date": "2026-01-01",
                "url": "https://www.c-span.org/program/1",
                "turns": turns,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_duplicate_offset_is_resolved_after_official_speaker_label(tmp_path):
    root = transcript(
        tmp_path,
        [
            {"offset": 10, "name": "Target Label", "text": "one two three four five six"},
            {"offset": 10, "name": "GUEST", "text": "parser debris"},
            {"offset": 30, "name": "Host", "text": "next turn"},
        ],
    )
    binding = WJ.exact_transcript_binding(
        root,
        {"pid": "1", "member": "Target Member", "start": 10, "end": 18, "manifest_version": "v1"},
        FakeIdentity(),
    )
    assert binding["member_start"] == 10
    assert binding["speaker_label"] == "Target Label"


def test_v1_in_turn_excerpt_may_end_before_but_not_after_next_turn(tmp_path):
    root = transcript(
        tmp_path,
        [
            {"offset": 10, "name": "Target Label", "text": "one two three four five six"},
            {"offset": 30, "name": "Host", "text": "next turn"},
        ],
    )
    WJ.exact_transcript_binding(
        root,
        {"pid": "1", "member": "Target Member", "start": 10, "end": 18, "manifest_version": "v1"},
        FakeIdentity(),
    )
    with pytest.raises(WJ.ImportRefusal, match="transcript_interval_crosses_next_turn"):
        WJ.exact_transcript_binding(
            root,
            {"pid": "1", "member": "Target Member", "start": 10, "end": 35, "manifest_version": "v1"},
            FakeIdentity(),
        )


def test_v2_pad_uses_next_turn_boundary_even_when_pad_exceeds_twenty_seconds(tmp_path):
    root = transcript(
        tmp_path,
        [
            {"offset": 5, "name": "Host", "text": "introduction"},
            {"offset": 27, "name": "Target Label", "text": "one two three four five six"},
            {"offset": 104, "name": "Host", "text": "next turn"},
        ],
    )
    binding = WJ.exact_transcript_binding(
        root,
        {
            "pid": "1",
            "member": "Target Member",
            "start": 0,
            "end": 104,
            "src": "gap",
            "manifest_version": "v2",
        },
        FakeIdentity(),
    )
    assert binding["member_start"] == 27


def test_v2_end_jitter_and_wrong_member_label_fail_closed(tmp_path):
    root = transcript(
        tmp_path,
        [
            {"offset": 27, "name": "Target Label", "text": "one two three four five six"},
            {"offset": 104, "name": "Host", "text": "next turn"},
        ],
    )
    with pytest.raises(
        WJ.ImportRefusal,
        match="padded_member_start_not_unique|transcript_end_not_next_turn",
    ):
        WJ.exact_transcript_binding(
            root,
            {
                "pid": "1", "member": "Target Member", "start": 0, "end": 103,
                "src": "gap", "manifest_version": "v2",
            },
            FakeIdentity(),
        )
    with pytest.raises(WJ.ImportRefusal, match="padded_member_start_not_unique"):
        WJ.exact_transcript_binding(
            root,
            {
                "pid": "1", "member": "Perturbed Member", "start": 0, "end": 104,
                "src": "gap", "manifest_version": "v2",
            },
            FakeIdentity(),
        )


def test_duplicate_official_member_rows_at_same_offset_remain_ambiguous(tmp_path):
    root = transcript(
        tmp_path,
        [
            {"offset": 10, "name": "Target Label", "text": "one two three four five six"},
            {"offset": 10, "name": "Target Label", "text": "duplicated official row"},
            {"offset": 30, "name": "Host", "text": "next turn"},
        ],
    )
    with pytest.raises(WJ.ImportRefusal, match="transcript_start_not_unique"):
        WJ.exact_transcript_binding(
            root,
            {"pid": "1", "member": "Target Member", "start": 10, "end": 18, "manifest_version": "v1"},
            FakeIdentity(),
        )


def write_wav(path: Path, seconds: float) -> None:
    frames = round(seconds * 16_000)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes((b"\x01\x00" * frames))


def test_physically_short_file_is_safe_subset_but_long_file_is_refused(tmp_path):
    short = tmp_path / "short.wav"
    write_wav(short, 93.25)
    rate, payload, rms, end = WJ.read_member_payload(short, 33.0, 13.0, 110.0)
    assert rate == 16_000
    assert len(payload) // 2 == round(73.25 * 16_000)
    assert end == pytest.approx(106.25)
    assert rms > 0

    long = tmp_path / "long.wav"
    write_wav(long, 98.0)
    with pytest.raises(WJ.ImportRefusal, match="source_duration_mismatch"):
        WJ.read_member_payload(long, 33.0, 13.0, 110.0)


def test_member_payload_physically_discards_leading_pad(tmp_path):
    path = tmp_path / "padded.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x01\x00" * (20 * 16_000))
        handle.writeframes(b"\x02\x00" * (12 * 16_000))
    _, payload, _, end = WJ.read_member_payload(path, 20.0, 0.0, 32.0)
    assert payload[:200] == b"\x02\x00" * 100
    assert b"\x01\x00" not in payload[:200]
    assert end == pytest.approx(32.0)


def test_unpadded_source_with_tolerated_trailing_audio_requires_exact_cut(tmp_path):
    source = tmp_path / "source.wav"
    write_wav(source, 8.5)

    rate, payload, _, end = WJ.read_member_payload(source, 10.0, 10.0, 18.0)

    assert end == pytest.approx(18.0)
    assert len(payload) // 2 == 8 * 16_000
    assert not WJ.source_is_exact_payload(source, rate, payload)

    output = tmp_path / "canonical.wav"
    WJ.write_trimmed(output, rate, payload)
    assert WJ.source_is_exact_payload(output, rate, payload)
    with wave.open(str(output), "rb") as handle:
        assert handle.getnframes() == 8 * 16_000


def test_load_candidates_keeps_longer_duration_capture_across_v1_v2_families(tmp_path):
    # CONSTRAINTS (2026-08-25) / AUDIOA-10: a dual-family logical turn keeps the
    # longer-duration capture, value-blind -- not v1 unconditionally. v1 here is an
    # unpadded 8-second excerpt; v2 carries the same raw (pid, member, start, end) key
    # but a pad_start that makes its actual capture span 28 seconds.
    root = tmp_path / "wj_audio"
    root.mkdir()
    (root / "turn_manifest.jsonl").write_text(
        json.dumps({"pid": "1", "member": "Target Member", "start": 100.0, "end": 108.0}) + "\n",
        encoding="utf-8",
    )
    (root / "turn_manifest_v2.jsonl").write_text(
        json.dumps({
            "pid": "1", "member": "Target Member", "start": 100.0, "end": 108.0,
            "pad_start": 80.0, "src": "gap",
        }) + "\n",
        encoding="utf-8",
    )
    candidates, census = WJ.load_candidates(root)
    assert len(candidates) == 1
    assert candidates[0]["manifest_version"] == "v2"
    assert WJ.capture_duration(candidates[0]) == pytest.approx(28.0)
    assert census["v1_rows"] == 1
    assert census["v2_rows"] == 1
    assert census["logical_overlap"] == 1
    assert census["unique_candidates"] == 1


def test_load_candidates_keeps_earlier_seen_version_on_exact_duration_tie(tmp_path):
    # On an exact duration tie the earlier-seen version (v1) stays -- a stable,
    # arbitrary tiebreak, not a version preference (see load_candidates()'s docstring
    # comment).
    root = tmp_path / "wj_audio"
    root.mkdir()
    (root / "turn_manifest.jsonl").write_text(
        json.dumps({"pid": "1", "member": "Target Member", "start": 100.0, "end": 108.0}) + "\n",
        encoding="utf-8",
    )
    (root / "turn_manifest_v2.jsonl").write_text(
        json.dumps({"pid": "1", "member": "Target Member", "start": 100.0, "end": 108.0}) + "\n",
        encoding="utf-8",
    )
    candidates, _census = WJ.load_candidates(root)
    assert len(candidates) == 1
    assert candidates[0]["manifest_version"] == "v1"


def test_is_longer_than_existing_ties_favor_the_existing_capture():
    # AUDIOA-10 write-time gate: an already-manifested shorter twin must not block a
    # longer candidate, but a duration tie (or an existing capture that is already at
    # least as long) keeps the existing row rather than adding a duplicate.
    assert WJ.is_longer_than_existing(None, 5.0) is True
    assert WJ.is_longer_than_existing(10.0, 12.0) is True
    assert WJ.is_longer_than_existing(10.0, 10.0) is False
    assert WJ.is_longer_than_existing(10.0, 8.0) is False

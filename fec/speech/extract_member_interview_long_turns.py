#!/usr/bin/env python3
"""Materialize complete reviewed member turns from long-form interviews.

An eight-second identity window is not a conversational turn. A turn begins
when the reviewed member takes the floor and ends when the interviewer or
another speaker takes it back. Turns have no duration ceiling: seconds, several
minutes, or longer are all valid when the reviewed boundary supports them.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import wave

from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES = ROOT / "data/speech/member_interview_sources_20260903.jsonl"
DEFAULT_ASR = ROOT / "data/speech/member_interview_asr_20260903.jsonl"
DEFAULT_VERIFIED = ROOT / "data/speech/member_interview_descriptive_v1.json"
DEFAULT_REVIEWS = ROOT / "data/speech/member_interview_reviewed_turns_20260903.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_interview_long_turns_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_interview_long_turns_v1.receipt.json"
DEFAULT_AUDIO_ROOT = Path(r"C:\melange-data\member_interview_long_turns_20260903")
MINIMUM_TURN_SECONDS = 5.0


class TurnRefusal(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def row_hash(row: dict) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def overlapping_words(words: list[dict], start: float, end: float) -> list[dict]:
    return [row for row in words
            if float(row["end_sec"]) > start and float(row["start_sec"]) < end]


def materialize_turn(source_wav: Path, target: Path, start: float, end: float) -> str:
    with wave.open(str(source_wav), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        if channels != 1 or width != 2 or rate != 16_000:
            raise TurnRefusal(f"source_wav_contract:{source_wav}")
        start_frame = round(start * rate)
        end_frame = round(end * rate)
        if start_frame < 0 or end_frame > source.getnframes():
            raise TurnRefusal(f"turn_out_of_audio_bounds:{source_wav}:{start}:{end}")
        source.setpos(start_frame)
        payload = source.readframes(end_frame - start_frame)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + ".part" + target.suffix)
    with wave.open(str(temporary), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(payload)
    os.replace(temporary, target)
    return SMR.sha256_file(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--asr", type=Path, default=DEFAULT_ASR)
    parser.add_argument("--verified", type=Path, default=DEFAULT_VERIFIED)
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEWS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    args = parser.parse_args()

    sources = {str(row["video_id"]): row for row in SMR.read_jsonl(args.sources, required=True)}
    asr = {str(row["video_id"]): row for row in SMR.read_jsonl(args.asr, required=True)}
    reviews = SMR.read_jsonl(args.reviews, required=True)
    verified_doc = json.loads(args.verified.read_text(encoding="utf-8-sig"))
    if verified_doc.get("status") != "DISPLAY_ONLY_NO_ENGINE_WIRING":
        raise TurnRefusal("verified_source_artifact_not_display_only")
    verified = {str(row["source_id"]): row for row in verified_doc.get("verified_sources") or []}

    seen_turns = set()
    rows = []
    generated_at = utc_now()
    for review in reviews:
        turn_id = str(review["turn_id"])
        source_id = str(review["source_id"])
        member = str(review["member"])
        if turn_id in seen_turns:
            raise TurnRefusal(f"duplicate_turn_id:{turn_id}")
        seen_turns.add(turn_id)
        source = sources.get(source_id)
        transcript_row = asr.get(source_id)
        verified_source = verified.get(source_id)
        if not source or not transcript_row or not verified_source:
            raise TurnRefusal(f"turn_source_not_verified:{turn_id}")
        if source["member"] != member or transcript_row["member"] != member or verified_source["member"] != member:
            raise TurnRefusal(f"turn_member_binding_mismatch:{turn_id}")

        start = float(review["start_sec"])
        end = float(review["end_sec"])
        duration = end - start
        if duration < MINIMUM_TURN_SECONDS:
            raise TurnRefusal(f"turn_below_minimum:{turn_id}")
        transcript = Path(str(transcript_row["transcript"]))
        if SMR.sha256_file(transcript) != transcript_row["transcript_sha256"]:
            raise TurnRefusal(f"turn_transcript_hash_mismatch:{turn_id}")
        words = SMR.read_jsonl(transcript, required=True)
        selected = overlapping_words(words, start, end)
        if len(selected) < 20:
            raise TurnRefusal(f"turn_too_few_words:{turn_id}")
        text = " ".join(str(row["word"]).strip() for row in selected)

        source_wav = Path(str(source["wav"]))
        if SMR.sha256_file(source_wav) != source["source_audio_sha256"]:
            raise TurnRefusal(f"turn_source_audio_hash_mismatch:{turn_id}")
        out_wav = args.audio_root / f"{turn_id}.wav"
        audio_sha256 = materialize_turn(source_wav, out_wav, start, end)
        anchor_overlap = sum(
            float(window["start_sec"]) >= start and float(window["end_sec"]) <= end
            for window in verified_source.get("windows") or []
        )
        row = {
            "schema": "member-interview-long-turn-v1",
            "status": "HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED",
            "turn_id": turn_id,
            "source_id": source_id,
            "member": member,
            "title": source["title"],
            "channel": source["channel"],
            "url": source["url"],
            "start_sec": start,
            "end_sec": end,
            "duration_seconds": round(duration, 3),
            "duration_minutes": round(duration / 60.0, 3),
            "word_count": len(selected),
            "transcript_text": text,
            "boundary_review": review["boundary_review"],
            "semantic_findings": review.get("semantic_findings") or [],
            "source_identity_method": verified_source["identity_method"],
            "source_identity_anchor_windows": int(verified_source["accepted_windows"]),
            "identity_anchor_windows_inside_turn": anchor_overlap,
            "turn_audio_wav": str(out_wav.resolve()),
            "turn_audio_sha256": audio_sha256,
            "source_audio_sha256": source["source_audio_sha256"],
            "source_transcript_sha256": transcript_row["transcript_sha256"],
            "generated_at": generated_at,
            "contract": {
                "complete_reviewed_turn_not_fixed_window": True,
                "maximum_turn_seconds": None,
                "short_identity_windows_define_turn_length": False,
                "semantic_findings_require_review": True,
                "automatic_trait_or_rank_effect": False,
            },
        }
        row["row_sha256"] = row_hash(row)
        rows.append(row)

    SMR.write_jsonl_atomic(args.out, rows)
    durations = [row["duration_seconds"] for row in rows]
    receipt = {
        "schema": "member-interview-long-turn-receipt-v1",
        "status": "PASS",
        "generated_at": generated_at,
        "artifact": str(args.out.resolve()),
        "artifact_sha256": SMR.sha256_file(args.out),
        "inputs": {
            "sources_sha256": SMR.sha256_file(args.sources),
            "asr_sha256": SMR.sha256_file(args.asr),
            "verified_sources_sha256": SMR.sha256_file(args.verified),
            "reviewed_turns_sha256": SMR.sha256_file(args.reviews),
        },
        "counts": {
            "turns": len(rows),
            "members": len({row["member"] for row in rows}),
            "sources": len({row["source_id"] for row in rows}),
            "turns_over_one_minute": sum(value >= 60.0 for value in durations),
            "turns_over_two_minutes": sum(value >= 120.0 for value in durations),
            "total_turn_minutes": round(sum(durations) / 60.0, 3),
            "longest_turn_seconds": max(durations) if durations else 0.0,
        },
        "contract": {
            "turn_definition": "member takes the floor through handoff to another speaker",
            "minimum_turn_seconds": MINIMUM_TURN_SECONDS,
            "maximum_turn_seconds": None,
            "identity_windows_are_anchors_not_turns": True,
            "long_silences_do_not_automatically_end_a_turn": True,
            "question_marks_inside_member_answer_are_allowed": True,
            "automatic_trait_or_rank_effect": False,
        },
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

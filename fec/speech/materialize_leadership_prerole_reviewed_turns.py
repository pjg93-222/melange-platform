#!/usr/bin/env python3
"""Materialize human-reviewed turns from pre-current-role leadership audio.

The input chain is deliberately strict: acquired/hash-bound audio, timestamped
ASR, a literal in-band full-name cue, an exact full-turn candidate, anchored
speaker continuity under the all-vault zero-false-accept calibration, and a
human identity/boundary/semantic review must all agree.  The resulting turns
are descriptive profile evidence only; they never alter traits, ranks, stance,
allocation, or persuasion-engine outputs.
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
DEFAULT_ACQUISITION = ROOT / "data/speech/member_leadership_prerole_acquisition_v1.jsonl"
DEFAULT_TRANSCRIPTS = Path(r"C:\melange-data\member_leadership_prerole_transcripts_v1.jsonl")
DEFAULT_CUES = Path(r"C:\melange-data\member_leadership_prerole_identity_cues_v1.jsonl")
DEFAULT_CANDIDATES = ROOT / "data/speech/member_leadership_prerole_full_turn_candidates_v1.jsonl"
DEFAULT_CONTINUITY = ROOT / "data/speech/member_leadership_prerole_speaker_continuity_v1.jsonl"
DEFAULT_REVIEWS = ROOT / "data/speech/member_leadership_prerole_human_reviews_v1.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_leadership_prerole_long_turns_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_leadership_prerole_long_turns_v1.receipt.json"
DEFAULT_AUDIO_ROOT = Path(r"C:\melange-data\member_leadership_prerole_turns_v1")
MINIMUM_TURN_SECONDS = 5.0
REQUIRED_CONSECUTIVE_WINDOWS = 3


class PromotionRefusal(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def row_hash(row: dict) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def maximum_consecutive_passes(rows: list[dict], turn_index: int) -> int:
    selected = sorted(
        (row for row in rows if int(row.get("turn_index", -1)) == turn_index),
        key=lambda row: int(row.get("window_index", -1)),
    )
    best = current = 0
    prior = None
    for row in selected:
        index = int(row.get("window_index", -1))
        if row.get("accepted") is True:
            current = current + 1 if prior is not None and index == prior + 1 else 1
            best = max(best, current)
        else:
            current = 0
        prior = index
    return best


def matching_turn(candidate: dict, review: dict) -> tuple[int, dict]:
    start = float(review["start_sec"])
    end = float(review["end_sec"])
    for index, turn in enumerate(candidate.get("turns") or []):
        if abs(float(turn["start_sec"]) - start) <= 0.001 and abs(float(turn["end_sec"]) - end) <= 0.001:
            return index, turn
    raise PromotionRefusal(f"review_not_bound_to_candidate:{review.get('turn_id')}")


def overlapping_words(words: list[dict], start: float, end: float) -> list[dict]:
    return [row for row in words
            if float(row["end_sec"]) > start and float(row["start_sec"]) < end]


def materialize_turn(source_wav: Path, target: Path, start: float, end: float) -> str:
    with wave.open(str(source_wav), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16_000):
            raise PromotionRefusal(f"source_wav_contract:{source_wav}")
        start_frame = round(start * 16_000)
        end_frame = round(end * 16_000)
        if start_frame < 0 or end_frame > source.getnframes() or end_frame <= start_frame:
            raise PromotionRefusal(f"turn_out_of_audio_bounds:{source_wav}:{start}:{end}")
        source.setpos(start_frame)
        payload = source.readframes(end_frame - start_frame)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + ".part" + target.suffix)
    with wave.open(str(temporary), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(payload)
    os.replace(temporary, target)
    return SMR.sha256_file(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition", type=Path, default=DEFAULT_ACQUISITION)
    parser.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--cues", type=Path, default=DEFAULT_CUES)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--continuity", type=Path, default=DEFAULT_CONTINUITY)
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEWS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    args = parser.parse_args()

    acquisitions = {row["member"]: row for row in SMR.read_jsonl(args.acquisition, required=True)}
    transcripts = {row["member"]: row for row in SMR.read_jsonl(args.transcripts, required=True)}
    cues = {row["member"]: row for row in SMR.read_jsonl(args.cues, required=True)}
    candidates = {row["member"]: row for row in SMR.read_jsonl(args.candidates, required=True)}
    continuity = {row["member"]: row for row in SMR.read_jsonl(args.continuity, required=True)}
    reviews = SMR.read_jsonl(args.reviews, required=True)

    rows = []
    seen_turns = set()
    generated_at = utc_now()
    for review in reviews:
        turn_id = str(review["turn_id"])
        member = str(review["member"])
        if turn_id in seen_turns:
            raise PromotionRefusal(f"duplicate_turn_id:{turn_id}")
        seen_turns.add(turn_id)
        if review.get("identity_review") != "ACCEPT" or review.get("boundary_review") in {None, ""}:
            raise PromotionRefusal(f"human_review_incomplete:{turn_id}")

        acquisition = acquisitions.get(member) or {}
        transcript_row = transcripts.get(member) or {}
        cue_row = cues.get(member) or {}
        candidate = candidates.get(member) or {}
        continuity_row = continuity.get(member) or {}
        source_id = str(review["source_id"])
        bound_rows = (acquisition, transcript_row, cue_row, candidate, continuity_row)
        if any(str(row.get("source_id")) != source_id for row in bound_rows):
            raise PromotionRefusal(f"source_binding_mismatch:{turn_id}")
        if acquisition.get("status") not in {"ACQUIRED_IDENTITY_UNVERIFIED", "ACQUIRED_IDENTITY_VERIFIED"}:
            raise PromotionRefusal(f"acquisition_not_ready:{turn_id}")
        if acquisition.get("source_era") != "PRE_CURRENT_LEADERSHIP_ROLE":
            raise PromotionRefusal(f"source_not_pre_current_role:{turn_id}")
        if transcript_row.get("status") not in {
            "LOCAL_ASR_READY_IDENTITY_UNVERIFIED", "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED",
            "REUSED_VERIFIED_LOCAL_ASR",
        }:
            raise PromotionRefusal(f"transcript_not_ready:{turn_id}")
        if cue_row.get("status") != "HARD_IN_BAND_NAME_CUE_CANDIDATE" or not cue_row.get("cues"):
            raise PromotionRefusal(f"literal_name_cue_absent:{turn_id}")
        if candidate.get("status") != "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED":
            raise PromotionRefusal(f"full_turn_candidate_absent:{turn_id}")
        accepted_continuity_statuses = {
            "ANCHORED_SPEAKER_CONTINUITY_PASSED_REVIEW_REQUIRED",
            "SUPPLEMENTAL_SPEAKER_CONTINUITY_PASSED_REVIEW_REQUIRED",
        }
        if (continuity_row.get("status") not in accepted_continuity_statuses
                or continuity_row.get("speaker_continuity_passed") is not True
                or not (continuity_row.get("anchored_member") is True
                        or continuity_row.get("supplemental_member") is True)):
            raise PromotionRefusal(f"speaker_gate_not_passed:{turn_id}")

        wav = Path(str(acquisition["wav"])).resolve()
        transcript = Path(str(transcript_row["transcript"])).resolve()
        if SMR.sha256_file(wav) != acquisition.get("wav_sha256"):
            raise PromotionRefusal(f"wav_hash_mismatch:{turn_id}")
        if SMR.sha256_file(transcript) != transcript_row.get("transcript_sha256"):
            raise PromotionRefusal(f"transcript_hash_mismatch:{turn_id}")
        if any(row.get("wav_sha256") != acquisition.get("wav_sha256") for row in (cue_row, candidate, continuity_row)):
            raise PromotionRefusal(f"downstream_wav_hash_mismatch:{turn_id}")
        if any(row.get("transcript_sha256") != transcript_row.get("transcript_sha256")
               for row in (cue_row, candidate, continuity_row)):
            raise PromotionRefusal(f"downstream_transcript_hash_mismatch:{turn_id}")

        turn_index, candidate_turn = matching_turn(candidate, review)
        run = maximum_consecutive_passes(continuity_row.get("evaluated_windows") or [], turn_index)
        if run < REQUIRED_CONSECUTIVE_WINDOWS:
            raise PromotionRefusal(f"selected_turn_speaker_run_below_gate:{turn_id}:{run}")
        start = float(candidate_turn["start_sec"])
        end = float(candidate_turn["end_sec"])
        if end - start < MINIMUM_TURN_SECONDS:
            raise PromotionRefusal(f"turn_below_minimum:{turn_id}")
        words = SMR.read_jsonl(transcript, required=True)
        selected = overlapping_words(words, start, end)
        if len(selected) < 20:
            raise PromotionRefusal(f"turn_too_few_words:{turn_id}")
        text = " ".join(str(row["word"]).strip() for row in selected)
        out_wav = args.audio_root / f"{turn_id}.wav"
        turn_audio_sha256 = materialize_turn(wav, out_wav, start, end)
        semantic_findings = review.get("semantic_findings") or []
        if any(not finding.get("meeting_implication") or not finding.get("scope")
               for finding in semantic_findings):
            raise PromotionRefusal(f"semantic_finding_scope_missing:{turn_id}")

        row = {
            "schema": "member-interview-long-turn-v1",
            "status": "HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED",
            "turn_id": turn_id,
            "source_id": source_id,
            "member": member,
            "title": acquisition.get("title"),
            "channel": acquisition.get("channel"),
            "url": acquisition.get("url"),
            "recorded_at": acquisition.get("recorded_at"),
            "published": acquisition.get("published"),
            "source_era": acquisition.get("source_era"),
            "current_role_assumed_on": acquisition.get("current_role_assumed_on"),
            "start_sec": start,
            "end_sec": end,
            "duration_seconds": round(end - start, 3),
            "duration_minutes": round((end - start) / 60.0, 3),
            "word_count": len(selected),
            "transcript_text": text,
            "boundary_review": review["boundary_review"],
            "identity_review": review.get("identity_review_note"),
            "semantic_findings": semantic_findings,
            "source_identity_method": (
                "literal-in-band-full-name-plus-" + str(continuity_row.get("method"))
                + "-plus-human-review"
            ),
            "source_identity_anchor_windows": run,
            "identity_anchor_windows_inside_turn": run,
            "turn_audio_wav": str(out_wav.resolve()),
            "turn_audio_sha256": turn_audio_sha256,
            "source_audio_sha256": acquisition["wav_sha256"],
            "source_transcript_sha256": transcript_row["transcript_sha256"],
            "generated_at": generated_at,
            "contract": {
                "complete_reviewed_turn_not_fixed_window": True,
                "maximum_turn_seconds": None,
                "semantic_findings_require_review": True,
                "automatic_trait_or_rank_effect": False,
                "leadership_baseline_requires_pre_current_role": True,
            },
        }
        row["row_sha256"] = row_hash(row)
        rows.append(row)

    SMR.write_jsonl_atomic(args.out, rows)
    durations = [float(row["duration_seconds"]) for row in rows]
    receipt = {
        "schema": "member-leadership-prerole-long-turn-receipt-v1",
        "status": "PASS",
        "generated_at": generated_at,
        "artifact": str(args.out.resolve()),
        "artifact_sha256": SMR.sha256_file(args.out),
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
            "maximum_turn_seconds": None,
            "all_sources_pre_current_role": True,
            "identity_requires_literal_name_anchored_continuity_and_human_review": True,
            "automatic_trait_or_rank_effect": False,
        },
        "inputs": {
            "acquisition_sha256": SMR.sha256_file(args.acquisition),
            "transcripts_sha256": SMR.sha256_file(args.transcripts),
            "cues_sha256": SMR.sha256_file(args.cues),
            "candidates_sha256": SMR.sha256_file(args.candidates),
            "continuity_sha256": SMR.sha256_file(args.continuity),
            "reviews_sha256": SMR.sha256_file(args.reviews),
        },
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

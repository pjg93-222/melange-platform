#!/usr/bin/env python3
"""Locate in-band full-name cue candidates in roster-wide transcripts.

The screen accepts exact transcript forms, approved common first-name forms,
and conservative phonetic ASR renderings.  It only produces candidates for
later audio, full-turn, and speaker-continuity review; transcript text is not
identity proof or proof that the audible wording was exact.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
from pathlib import Path

from fec.speech import classify_local_youtube_turns as turns
from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRANSCRIPTS = ROOT / "data/speech/member_longform_transcripts_v1.jsonl"
DEFAULT_OUTPUT = ROOT / "data/speech/member_longform_identity_cues_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_identity_cues_v1.receipt.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def find_name_cues(words: list[dict], member: str, limit: int = 8) -> list[dict]:
    member_tokens = turns.normalized(member).split()
    if len(member_tokens) < 2:
        return []
    firsts = {member_tokens[0]}
    for group in turns.FIRST_NAME_EQUIVALENTS:
        if member_tokens[0] in group:
            firsts.update(group)
    surname = member_tokens[-1]
    flattened: list[tuple[str, int]] = []
    for word_index, row in enumerate(words):
        flattened.extend((token, word_index) for token in turns.normalized(str(row.get("word") or "")).split())
    candidates: list[dict] = []
    for token_start, (token, word_start) in enumerate(flattened):
        compact_similarity = max(
            difflib.SequenceMatcher(
                None, first + surname, token, autojunk=False
            ).ratio()
            for first in firsts
        )
        if len(token) >= 7 and compact_similarity >= 0.8:
            candidates.append({
                "phrase": str(words[word_start].get("word") or "").strip(),
                "start_sec": float(words[word_start]["start_sec"]),
                "end_sec": float(words[word_start]["end_sec"]),
                "word_start": word_start,
                "word_end": word_start,
                "first_match": "compact_full_name_asr",
                "surname_similarity": round(compact_similarity, 3),
            })
        exact_or_approved_first = token in firsts
        phonetic_first = (
            len(token) >= 3
            and difflib.SequenceMatcher(
                None, member_tokens[0], token, autojunk=False
            ).ratio() >= 0.7
        )
        if not exact_or_approved_first and not phonetic_first:
            continue
        for token_end in range(token_start + 1, min(token_start + 10, len(flattened))):
            matched = False
            for token_last in range(token_end, min(token_end + 3, len(flattened))):
                candidate_surname = "".join(
                    flattened[index][0] for index in range(token_end, token_last + 1)
                )
                word_end = flattened[token_last][1]
                surname_similarity = difflib.SequenceMatcher(
                    None, surname, candidate_surname, autojunk=False
                ).ratio()
                conservative_phonetic_surname = (
                    exact_or_approved_first
                    and len(surname) >= 5
                    and len(candidate_surname) >= 5
                    and surname[0] == candidate_surname[0]
                    and surname[-1] == candidate_surname[-1]
                    and surname_similarity >= 0.5
                )
                if surname_similarity < 0.65 and not conservative_phonetic_surname:
                    continue
                chunk = words[word_start:word_end + 1]
                phrase = " ".join(str(row.get("word") or "").strip() for row in chunk).strip()
                candidates.append({
                    "phrase": phrase,
                    "start_sec": float(chunk[0]["start_sec"]),
                    "end_sec": float(chunk[-1]["end_sec"]),
                    "word_start": word_start,
                    "word_end": word_end,
                    "first_match": (
                        "exact_or_approved_alias" if exact_or_approved_first else "phonetic_asr"
                    ),
                    "surname_similarity": round(surname_similarity, 3),
                    "surname_token_count": token_last - token_end + 1,
                })
                matched = True
                break
            if matched:
                break
    cues: list[dict] = []
    occupied: set[int] = set()
    for candidate in sorted(
        candidates,
        key=lambda row: (row["word_end"] - row["word_start"], row["word_start"]),
    ):
        indexes = set(range(candidate["word_start"], candidate["word_end"] + 1))
        if indexes & occupied:
            continue
        cues.append(candidate)
        occupied.update(indexes)
        if len(cues) >= limit:
            break
    return sorted(cues, key=lambda row: row["start_sec"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--member", action="append", default=[])
    args = parser.parse_args()

    source_rows = SMR.read_jsonl(args.transcripts, required=True)
    wanted = set(args.member)
    selected = [
        row for row in source_rows
        if row.get("transcript") and (not wanted or row.get("member") in wanted)
    ]
    output: list[dict] = []
    for row in selected:
        transcript = Path(str(row["transcript"]))
        if SMR.sha256_file(transcript) != row.get("transcript_sha256"):
            raise SystemExit(f"transcript hash mismatch: {row['member']}")
        words = SMR.read_jsonl(transcript, required=True)
        cues = find_name_cues(words, str(row["member"]))
        output.append({
            "schema": "member-longform-identity-cue-v1",
            "status": "HARD_IN_BAND_NAME_CUE_CANDIDATE" if cues else "NO_FULL_NAME_CUE_FOUND",
            "member": row["member"],
            "source_id": row.get("source_id"),
            "source_type": row.get("source_type"),
            "title": row.get("title"),
            "channel": row.get("channel"),
            "url": row.get("url"),
            "recorded_at": row.get("recorded_at"),
            "published": row.get("published"),
            "source_era": row.get("source_era"),
            "current_role_assumed_on": row.get("current_role_assumed_on"),
            "wav": row.get("wav"),
            "wav_sha256": row.get("wav_sha256"),
            "transcript": str(transcript.resolve()),
            "transcript_sha256": row["transcript_sha256"],
            "cues": cues,
            "speaker_identity_authority": False,
            "generated_at": utc_now(),
        })
    output.sort(key=lambda row: row["member"])
    SMR.write_jsonl_atomic(args.output, output)
    counts = {
        "transcripts_screened": len(output),
        "members_with_name_cues": sum(bool(row["cues"]) for row in output),
        "members_without_name_cues": sum(not row["cues"] for row in output),
        "literal_cues": sum(len(row["cues"]) for row in output),
    }
    receipt = {
        "schema": "member-longform-identity-cue-receipt-v1",
        "status": "PASS_CANDIDATE_SCREEN_ONLY",
        "generated_at": utc_now(),
        "counts": counts,
        "contract": {
            "literal_name_cue_establishes_identity": False,
            "phonetic_asr_name_form_establishes_literal_audio_wording": False,
            "accepted_transcript_forms": (
                "exact, approved common first-name equivalent, or conservative phonetic ASR candidate"
            ),
            "metadata_used_as_identity_evidence": False,
            "next_gate": "audio identity review, complete-target-turn review, and speaker continuity",
        },
        "inputs": {"transcripts_sha256": SMR.sha256_file(args.transcripts)},
        "output": {"path": str(args.output.resolve()), "sha256": SMR.sha256_file(args.output)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

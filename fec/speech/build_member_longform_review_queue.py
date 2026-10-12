#!/usr/bin/env python3
"""Build a complete-turn review queue from long-form candidates.

The queue is deliberately non-promoting.  It packages the literal identity
cue, surrounding transcript, complete-turn claim, and per-turn acoustic run so
an identity/boundary/semantic reviewer can make a source-bound decision.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import wave

from fec.speech import source_media_recovery as SMR
from fec.speech import verify_member_longform_speaker_continuity as continuity_checks


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SELECTION = ROOT / "data/speech/member_longform_profile_source_selection_v1.jsonl"
DEFAULT_CANDIDATES = Path(r"C:\melange-data\member_longform_full_turn_candidates_local_v1.jsonl")
DEFAULT_CONTINUITY = ROOT / "data/speech/member_longform_speaker_continuity_v1.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_longform_review_queue_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_review_queue_v1.receipt.json"
DEFAULT_AUDIO_ROOT = Path(r"C:\melange-data\member_longform_review_queue_audio_v1")
REQUIRED_CONSECUTIVE_WINDOWS = 3
REVIEW_CONTEXT_SECONDS = 5.0
MAX_PRECEDING_CUE_GAP_SECONDS = 300.0


def index_sources(rows: list[dict], label: str) -> dict[tuple[str, str], dict]:
    """Preserve multiple productions per member and refuse ambiguous duplicates."""
    indexed = {}
    for row in rows:
        key = (str(row.get("member") or ""), str(row.get("source_id") or ""))
        if key in indexed:
            raise SystemExit(f"duplicate {label} member/source binding: {key}")
        indexed[key] = row
    return indexed


def qualified_turn_coverage(candidate: dict, acoustic: dict, index: int, turn: dict) -> dict | None:
    """Recompute complete interior coverage; an early identity run is insufficient."""
    if acoustic.get("coverage_contract_version") != continuity_checks.CONTINUITY_CONTRACT_VERSION:
        return None
    binding = continuity_checks.turn_binding_sha256(candidate, turn)
    stored = [item for item in acoustic.get("turn_coverage") or []
              if item.get("turn_index") == index and item.get("turn_binding_sha256") == binding]
    if len(stored) != 1:
        return None
    measured = continuity_checks.summarize_turn_coverage(
        candidate, turn, index, acoustic.get("evaluated_windows") or [],
        budget_exhausted=bool(acoustic.get("budget_exhausted")),
    )
    if not measured.get("continuity_gate_passed") or measured != stored[0]:
        return None
    return measured


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return cleaned or "member"


def consecutive_run(rows: list[dict], turn_index: int) -> int:
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


def text_between(words: list[dict], start: float, end: float) -> str:
    return " ".join(
        str(row.get("word") or "").strip()
        for row in words
        if float(row["end_sec"]) > start and float(row["start_sec"]) < end
    ).strip()


def nearest_preceding_cue(cues: list[dict], start: float) -> dict | None:
    eligible = [
        cue for cue in cues
        if float(cue.get("end_sec") or 0.0) <= start + 0.5
        and 0.0 <= start - float(cue.get("end_sec") or 0.0) <= MAX_PRECEDING_CUE_GAP_SECONDS
    ]
    return max(eligible, key=lambda cue: float(cue.get("end_sec") or 0.0)) if eligible else None


def cut_review_audio(
    source_path: Path, target_path: Path, start: float, end: float, cue_start: float
) -> dict:
    with wave.open(str(source_path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16_000):
            raise SystemExit(f"source WAV violates review contract: {source_path}")
        source_duration = source.getnframes() / 16_000.0
        clip_start = max(0.0, min(start, cue_start) - REVIEW_CONTEXT_SECONDS)
        clip_end = min(source_duration, end + REVIEW_CONTEXT_SECONDS)
        first = round(clip_start * 16_000)
        last = round(clip_end * 16_000)
        source.setpos(first)
        payload = source.readframes(last - first)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(target_path.stem + ".part" + target_path.suffix)
    with wave.open(str(temporary), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(payload)
    os.replace(temporary, target_path)
    return {
        "review_audio_wav": str(target_path.resolve()),
        "review_audio_sha256": SMR.sha256_file(target_path),
        "review_audio_source_start_sec": round(clip_start, 3),
        "review_audio_source_end_sec": round(clip_end, 3),
        "turn_start_in_review_audio_sec": round(start - clip_start, 3),
        "turn_end_in_review_audio_sec": round(end - clip_start, 3),
        "identity_cue_start_in_review_audio_sec": round(cue_start - clip_start, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--continuity", type=Path, default=DEFAULT_CONTINUITY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    args = parser.parse_args()

    selections = SMR.read_jsonl(args.selection, required=True)
    candidates = index_sources(SMR.read_jsonl(args.candidates, required=True), "candidate")
    continuity = index_sources(SMR.read_jsonl(args.continuity, required=True), "continuity")
    output: list[dict] = []
    for selection in selections:
        member = str(selection["member"])
        base = {
            "schema": "member-longform-review-queue-v1",
            "member": member,
            "source_id": selection.get("source_id"),
            "selection": selection.get("selection"),
            "source_era": selection.get("source_era"),
            "current_role": selection.get("current_role"),
            "current_role_assumed_on": selection.get("current_role_assumed_on"),
            "profile_effect": "NONE_UNTIL_REVIEW_AND_MATERIALIZATION",
            "generated_at": utc_now(),
        }
        if selection.get("status") != "SELECTED_HARD_NAME_CUE_CANDIDATE":
            output.append({**base, "status": selection.get("status"), "review_required": False})
            continue
        source_key = (member, str(selection.get("source_id") or ""))
        candidate = candidates.get(source_key)
        acoustic = continuity.get(source_key)
        bound = (candidate, acoustic)
        if any(not row or str(row.get("source_id")) != str(selection.get("source_id")) for row in bound):
            output.append({**base, "status": "DOWNSTREAM_SOURCE_BINDING_MISSING", "review_required": False})
            continue
        if candidate.get("status") != "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED":
            output.append({**base, "status": "NO_COMPLETE_TURN_CANDIDATE", "review_required": False})
            continue
        if any(row.get("wav_sha256") != selection.get("wav_sha256") for row in bound):
            raise SystemExit(f"downstream WAV hash mismatch: {member}")
        if any(row.get("transcript_sha256") != selection.get("transcript_sha256") for row in bound):
            raise SystemExit(f"downstream transcript hash mismatch: {member}")
        ranked = []
        cues = selection.get("cues") or []
        diagnostics = {
            "proposed_turns": len(candidate.get("turns") or []),
            "whole_interior_coverage_qualified_turns": 0,
            "coverage_qualified_turns_without_nearby_name_cue": 0,
            "turns_without_complete_passing_interior_coverage": 0,
            "maximum_preceding_name_cue_gap_seconds": MAX_PRECEDING_CUE_GAP_SECONDS,
            "cue_link_restriction_is_not_evidence_of_missing_audio": True,
        }
        for index, turn in enumerate(candidate.get("turns") or []):
            coverage = qualified_turn_coverage(candidate, acoustic, index, turn)
            cue = nearest_preceding_cue(cues, float(turn["start_sec"]))
            if coverage is None:
                diagnostics["turns_without_complete_passing_interior_coverage"] += 1
                continue
            diagnostics["whole_interior_coverage_qualified_turns"] += 1
            if cue is None:
                diagnostics["coverage_qualified_turns_without_nearby_name_cue"] += 1
                continue
            run = coverage["maximum_consecutive_passing_windows"]
            gap = float(turn["start_sec"]) - float(cue["end_sec"])
            ranked.append((gap, -float(turn.get("duration_seconds") or 0.0), -run, index, turn, cue))
        ranked.sort()
        base["source_turn_review_diagnostics"] = diagnostics
        if not ranked:
            output.append({
                **base,
                "status": "NO_CUE_LINKED_TURN_WITH_SPEAKER_CONTINUITY",
                "review_required": False,
            })
            continue
        transcript = Path(str(selection["transcript"])).resolve()
        if SMR.sha256_file(transcript) != selection.get("transcript_sha256"):
            raise SystemExit(f"transcript hash mismatch: {member}")
        words = SMR.read_jsonl(transcript, required=True)
        wav = Path(str(selection.get("wav") or "")).resolve()
        if not wav.is_file() or SMR.sha256_file(wav) != selection.get("wav_sha256"):
            raise SystemExit(f"source WAV missing or hash mismatch: {member}")
        for cue_gap, _neg_duration, neg_run, turn_index, turn, identity_cue in ranked:
            output.append(review_item(base, selection, candidate, acoustic, words, transcript, wav,
                                      args.audio_root, cue_gap, -neg_run, turn_index, turn, identity_cue))

    SMR.write_jsonl_atomic(args.out, output)
    status_counts: dict[str, int] = {}
    for row in output:
        status_counts[str(row["status"])] = status_counts.get(str(row["status"]), 0) + 1
    receipt = {
        "schema": "member-longform-review-queue-receipt-v1",
        "status": "PASS_REVIEW_QUEUE_ONLY",
        "generated_at": utc_now(),
        "counts": {"members": len({row["member"] for row in output}), "rows": len(output), **status_counts},
        "contract": {
            "one_candidate_turn_per_member": False,
            "all_cue_linked_coverage_qualified_turns_are_retained": True,
            "selected_turn_is_linked_to_preceding_in_band_name_cue": True,
            "maximum_preceding_name_cue_gap_seconds": MAX_PRECEDING_CUE_GAP_SECONDS,
            "cue_link_restriction_is_not_evidence_of_missing_audio": True,
            "turn_definition": "member takes the floor through the next speaker handoff",
            "maximum_turn_seconds": None,
            "minimum_consecutive_acoustic_windows": REQUIRED_CONSECUTIVE_WINDOWS,
            "coverage_contract_version": continuity_checks.CONTINUITY_CONTRACT_VERSION,
            "review_audio_begins_before_selected_name_cue": True,
            "review_audio_boundary_context_seconds": REVIEW_CONTEXT_SECONDS,
            "queue_confers_identity_or_profile_authority": False,
        },
        "inputs": {
            "selection_sha256": SMR.sha256_file(args.selection),
            "candidates_sha256": SMR.sha256_file(args.candidates),
            "continuity_sha256": SMR.sha256_file(args.continuity),
        },
        "output": {"path": str(args.out.resolve()), "sha256": SMR.sha256_file(args.out)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


def review_item(base, selection, candidate, acoustic, words, transcript, wav, audio_root,
                cue_gap, run, turn_index, turn, identity_cue):
        member = str(selection["member"])
        cues = selection.get("cues") or []
        start = float(turn["start_sec"])
        end = float(turn["end_sec"])
        turn_id = f"{slug(member)}-{slug(str(selection.get('source_id') or 'source'))}-{turn_index + 1:02d}"
        review_audio = cut_review_audio(
            wav, audio_root / f"{turn_id}.wav", start, end,
            float(identity_cue["start_sec"]),
        )
        review_context_start = max(
            0.0, min(start, float(identity_cue["start_sec"])) - REVIEW_CONTEXT_SECONDS
        )
        return {
            **base,
            "status": "IDENTITY_BOUNDARY_SEMANTIC_REVIEW_REQUIRED",
            "review_required": True,
            "turn_id": turn_id,
            "turn_index": turn_index,
            "url": selection.get("url"),
            "title": selection.get("title"),
            "channel": selection.get("channel"),
            "recorded_at": selection.get("recorded_at"),
            "published": selection.get("published"),
            "wav": selection.get("wav"),
            "wav_sha256": selection.get("wav_sha256"),
            "transcript": str(transcript),
            "transcript_sha256": selection.get("transcript_sha256"),
            "literal_name_cues": cues,
            "selected_identity_cue": identity_cue,
            "identity_cue_to_turn_gap_seconds": round(cue_gap, 3),
            "start_sec": start,
            "end_sec": end,
            "duration_seconds": round(end - start, 3),
            "opening_words": turn.get("opening_words"),
            "candidate_handoff_basis": turn.get("handoff_basis"),
            "preceding_context": text_between(words, review_context_start, start),
            "turn_transcript": text_between(words, start, end),
            "following_context": text_between(words, end, end + 15.0),
            "speaker_continuity_method": acoustic.get("method"),
            "anchored_member": acoustic.get("anchored_member") is True,
            "supplemental_member": acoustic.get("supplemental_member") is True,
            "consecutive_acoustic_windows": run,
            "turn_coverage": qualified_turn_coverage(candidate, acoustic, turn_index, turn),
            **review_audio,
            "review_template": {
                "member": member,
                "source_id": selection.get("source_id"),
                "turn_id": turn_id,
                "start_sec": start,
                "end_sec": end,
                "wav_sha256": selection.get("wav_sha256"),
                "transcript_sha256": selection.get("transcript_sha256"),
                "candidate_turn_binding_sha256": continuity_checks.turn_binding_sha256(candidate, turn),
                "review_audio_sha256": review_audio["review_audio_sha256"],
                "review_scope": "public_interview_identity_turn_boundaries_and_descriptive_content",
                "review_status": "ACCEPT|REJECT",
                "reviewer_type": "HUMAN",
                "reviewer_name": "",
                "reviewed_at": "",
                "review_attestation": "personally_listened_and_reviewed_identity_boundaries_and_semantics",
                "identity_review": "ACCEPT|REJECT",
                "identity_review_note": "Confirm the named handoff and selected voice.",
                "boundary_review": "ACCEPT|REJECT",
                "boundary_review_note": "Confirm both speaker handoffs and preserve the complete turn.",
                "semantic_review": "ACCEPT|REJECT",
                "semantic_findings": [{
                    "kind": "short_machine_key",
                    "finding": "Narrow observation grounded in this turn.",
                    "scope": "One reviewed public long-form turn.",
                }],
            },
        }


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Materialize source-bound turns after human or authorized automated checks.

Selected source, hash-bound transcript, literal name cue, complete-turn
candidate, per-turn acoustic continuity, and identity/boundary/semantic review
must all bind to the same member and source.  Output is descriptive evidence
only and cannot alter traits, ranks, stance, allocation, or engine output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import wave

from fec.speech import build_member_longform_review_queue as queue
from fec.speech import source_media_recovery as SMR
from fec.speech import longform_automated_authority as auto


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SELECTION = ROOT / "data/speech/member_longform_profile_source_selection_v1.jsonl"
DEFAULT_CANDIDATES = Path(r"C:\melange-data\member_longform_full_turn_candidates_local_v1.jsonl")
DEFAULT_CONTINUITY = ROOT / "data/speech/member_longform_speaker_continuity_v1.jsonl"
DEFAULT_REVIEWS = ROOT / "data/speech/member_longform_human_reviews_v1.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_longform_reviewed_turns_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_reviewed_turns_v1.receipt.json"
DEFAULT_AUDIO_ROOT = Path(r"C:\melange-data\member_longform_reviewed_turns_v1")
MINIMUM_TURN_SECONDS = 5.0


class PromotionRefusal(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def row_hash(row: dict) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_index(rows: list[dict], label: str) -> dict:
    result = {}
    for row in rows:
        key = (str(row.get("member") or ""), str(row.get("source_id") or ""))
        if label == "selection" and key[0] and not key[1] and row.get("status") != "SELECTED_HARD_NAME_CUE_CANDIDATE":
            continue
        if not all(key) or key in result:
            raise PromotionRefusal(f"missing_or_duplicate_source:{label}:{key}")
        result[key] = row
    return result


def matching_turn(candidate: dict, review: dict) -> tuple[int, dict]:
    start = float(review["start_sec"])
    end = float(review["end_sec"])
    for index, turn in enumerate(candidate.get("turns") or []):
        if abs(float(turn["start_sec"]) - start) <= 0.001 and abs(float(turn["end_sec"]) - end) <= 0.001:
            return index, turn
    raise PromotionRefusal(f"review_not_bound_to_candidate:{review.get('turn_id')}")


def words_between(words: list[dict], start: float, end: float) -> list[dict]:
    return [
        row for row in words
        if float(row["end_sec"]) > start and float(row["start_sec"]) < end
    ]


def cut_wav(source_path: Path, target_path: Path, start: float, end: float) -> str:
    with wave.open(str(source_path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16_000):
            raise PromotionRefusal(f"source_wav_contract:{source_path}")
        first = round(start * 16_000)
        last = round(end * 16_000)
        if first < 0 or last <= first or last > source.getnframes():
            raise PromotionRefusal(f"turn_out_of_audio_bounds:{source_path}:{start}:{end}")
        source.setpos(first)
        payload = source.readframes(last - first)
    if target_path.exists():
        with wave.open(str(target_path), "rb") as existing:
            if ((existing.getnchannels(), existing.getsampwidth(), existing.getframerate()) != (1, 2, 16_000)
                    or existing.readframes(existing.getnframes()) != payload):
                raise PromotionRefusal(f"existing_turn_audio_differs_preserved:{target_path}")
        return SMR.sha256_file(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(target_path.stem + ".part" + target_path.suffix)
    with wave.open(str(temporary), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(payload)
    os.replace(temporary, target_path)
    return SMR.sha256_file(target_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--continuity", type=Path, default=DEFAULT_CONTINUITY)
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEWS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    args = parser.parse_args()

    input_paths = {"selection": args.selection, "candidates": args.candidates,
                   "continuity": args.continuity, "reviews": args.reviews}
    input_hashes = {name: SMR.sha256_file(path) for name, path in input_paths.items()}
    selections = source_index(SMR.read_jsonl(args.selection, required=True), "selection")
    candidates = source_index(SMR.read_jsonl(args.candidates, required=True), "candidates")
    continuity = source_index(SMR.read_jsonl(args.continuity, required=True), "continuity")
    reviews = SMR.read_jsonl(args.reviews, required=True)
    output: list[dict] = []
    seen: set[str] = set()
    generated_at = utc_now()
    proof_hashes: dict[Path, str] = {}
    for review in reviews:
        if review.get("review_status") == "REJECT":
            continue
        turn_id = str(review.get("turn_id") or "")
        member = str(review.get("member") or "")
        source_id = str(review.get("source_id") or "")
        if not turn_id or turn_id in seen:
            raise PromotionRefusal(f"missing_or_duplicate_turn_id:{turn_id}")
        seen.add(turn_id)
        automated = review.get("reviewer_type") == "AUTOMATED"
        if automated:
            auto.check_metadata(review)
        elif review.get("reviewer_type") != "HUMAN":
            raise PromotionRefusal(f"reviewer_not_human:{turn_id}")
        if not str(review.get("reviewer_name") or "").strip():
            raise PromotionRefusal(f"reviewer_name_missing:{turn_id}")
        try:
            reviewed_at = dt.datetime.fromisoformat(str(review.get("reviewed_at") or "").replace("Z", "+00:00"))
            if reviewed_at.tzinfo is None:
                raise ValueError("review timestamp lacks timezone")
        except ValueError as exc:
            raise PromotionRefusal(f"review_timestamp_missing_or_invalid:{turn_id}") from exc
        if not automated and review.get("review_attestation") != "personally_listened_and_reviewed_identity_boundaries_and_semantics":
            raise PromotionRefusal(f"review_attestation_missing:{turn_id}")
        if any(review.get(key) != "ACCEPT" for key in (
            "review_status", "identity_review", "boundary_review", "semantic_review"
        )):
            raise PromotionRefusal(f"review_incomplete:{turn_id}")
        if any(not str(review.get(key) or "").strip() for key in (
            "identity_review_note", "boundary_review_note"
        )):
            raise PromotionRefusal(f"review_notes_missing:{turn_id}")
        findings = review.get("semantic_findings") or []
        if not findings or any(
            not str(finding.get(key) or "").strip()
            for finding in findings
            for key in ("kind", "finding", "scope")
        ):
            raise PromotionRefusal(f"semantic_findings_incomplete:{turn_id}")
        invalid_phases = {
            str(finding.get("meeting_phase")) for finding in findings
            if finding.get("meeting_phase") is not None
            and finding.get("meeting_phase") not in {"open", "bring", "run_the_room", "close"}
        }
        if invalid_phases:
            raise PromotionRefusal(
                f"semantic_findings_invalid_meeting_phase:{turn_id}:{sorted(invalid_phases)}"
            )

        selection = selections.get((member, source_id)) or {}
        candidate = candidates.get((member, source_id)) or {}
        acoustic = continuity.get((member, source_id)) or {}
        bound = (selection, candidate, acoustic)
        if any(str(row.get("source_id") or "") != source_id for row in bound):
            raise PromotionRefusal(f"source_binding_mismatch:{turn_id}")
        if selection.get("status") != "SELECTED_HARD_NAME_CUE_CANDIDATE" or not selection.get("cues"):
            raise PromotionRefusal(f"hard_name_cue_not_selected:{turn_id}")
        if candidate.get("status") != "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED":
            raise PromotionRefusal(f"complete_turn_candidate_absent:{turn_id}")
        if any(row.get("wav_sha256") != selection.get("wav_sha256") for row in bound[1:]):
            raise PromotionRefusal(f"wav_hash_binding_mismatch:{turn_id}")
        if any(row.get("transcript_sha256") != selection.get("transcript_sha256") for row in bound[1:]):
            raise PromotionRefusal(f"transcript_hash_binding_mismatch:{turn_id}")
        if any(not review.get(key) or review[key] != selection.get(key)
               for key in ("wav_sha256", "transcript_sha256")):
            raise PromotionRefusal(f"human_review_source_hash_binding_mismatch:{turn_id}")
        turn_index, turn = matching_turn(candidate, review)
        expected_turn_id = f"{queue.slug(member)}-{queue.slug(source_id)}-{turn_index + 1:02d}"
        if turn_id != expected_turn_id:
            raise PromotionRefusal(f"turn_id_not_bound_to_safe_candidate:{turn_id}")
        turn_coverage = queue.qualified_turn_coverage(candidate, acoustic, turn_index, turn)
        if turn_coverage is None:
            raise PromotionRefusal(f"complete_turn_continuity_not_verified:{turn_id}")
        if review.get("candidate_turn_binding_sha256") != turn_coverage["turn_binding_sha256"]:
            raise PromotionRefusal(f"human_review_candidate_binding_mismatch:{turn_id}")
        run = turn_coverage["maximum_consecutive_passing_windows"]
        if run < queue.REQUIRED_CONSECUTIVE_WINDOWS:
            raise PromotionRefusal(f"selected_turn_acoustic_run_below_gate:{turn_id}:{run}")
        if automated:
            if not (acoustic.get("anchored_member") is True or acoustic.get("supplemental_member") is True):
                raise PromotionRefusal(f"automatic_cold_start_is_not_positive_identity:{turn_id}")
            for path in auto.verify_boundaries(review, selection, candidate, turn):
                proof_hashes[path] = SMR.sha256_file(path)

        start = float(turn["start_sec"])
        end = float(turn["end_sec"])
        if end - start < MINIMUM_TURN_SECONDS:
            raise PromotionRefusal(f"turn_below_minimum:{turn_id}")
        wav = Path(str(selection["wav"])).resolve()
        transcript = Path(str(selection["transcript"])).resolve()
        if SMR.sha256_file(wav) != selection.get("wav_sha256"):
            raise PromotionRefusal(f"source_wav_hash_mismatch:{turn_id}")
        if SMR.sha256_file(transcript) != selection.get("transcript_sha256"):
            raise PromotionRefusal(f"source_transcript_hash_mismatch:{turn_id}")
        words = SMR.read_jsonl(transcript, required=True)
        selected_words = words_between(words, start, end)
        if len(selected_words) < 20:
            raise PromotionRefusal(f"turn_too_few_words:{turn_id}")
        transcript_text = " ".join(str(row.get("word") or "").strip() for row in selected_words)
        source_era = str(selection.get("source_era") or "UNKNOWN_REQUIRES_REVIEW")
        if selection.get("current_role") and source_era != "PRE_CURRENT_LEADERSHIP_ROLE":
            raise PromotionRefusal(f"leader_source_not_pre_current_role:{turn_id}")
        if selection.get("current_role"):
            try:
                recorded = dt.date.fromisoformat(str(selection.get("recorded_at") or selection.get("published") or "")[:10])
                cutoff = dt.date.fromisoformat(str(selection.get("current_role_assumed_on") or "")[:10])
                if recorded >= cutoff:
                    raise ValueError("recording does not predate current role")
            except ValueError as exc:
                raise PromotionRefusal(f"leader_recording_date_not_pre_current_role:{turn_id}") from exc
        turn_wav = args.audio_root / f"{turn_id}.wav"
        turn_sha = cut_wav(wav, turn_wav, start, end)
        method = str(acoustic.get("method") or "")
        identity_tier = (
            "ANCHORED_ALL_VAULT_ZERO_FALSE_ACCEPT"
            if acoustic.get("anchored_member") is True else
            "SUPPLEMENTAL_INDEPENDENT_REFERENCES"
            if acoustic.get("supplemental_member") is True else
            "LITERAL_NAMED_HANDOFF_PLUS_COLD_START_REJECTION_CONTINUITY"
        )
        row = {
            "schema": "member-interview-long-turn-v1",
            "status": auto.STATUS if automated else "HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED",
            "turn_id": turn_id,
            "member": member,
            "source_id": source_id,
            "title": selection.get("title"),
            "channel": selection.get("channel"),
            "url": selection.get("url"),
            "recorded_at": selection.get("recorded_at"),
            "published": selection.get("published"),
            "source_era": source_era,
            "current_role_assumed_on": selection.get("current_role_assumed_on"),
            "start_sec": start,
            "end_sec": end,
            "duration_seconds": round(end - start, 3),
            "duration_minutes": round((end - start) / 60.0, 3),
            "word_count": len(selected_words),
            "transcript_text": transcript_text,
            "boundary_review": review["boundary_review_note"],
            "identity_review": review["identity_review_note"],
            "semantic_findings": findings,
            "source_identity_method": f"literal-in-band-full-name-plus-{method}-plus-" + ("automated-verification" if automated else "human-review"),
            "source_identity_tier": identity_tier,
            "source_identity_anchor_windows": run,
            "identity_anchor_windows_inside_turn": run,
            "turn_audio_wav": str(turn_wav.resolve()),
            "turn_audio_sha256": turn_sha,
            "source_audio_sha256": selection["wav_sha256"],
            "source_audio_wav": str(wav),
            "source_transcript": str(transcript),
            "source_transcript_sha256": selection["transcript_sha256"],
            "turn_coverage": turn_coverage,
            "candidate_turn_binding_sha256": turn_coverage["turn_binding_sha256"],
            "review_row_sha256": row_hash(review),
            "generated_at": generated_at,
            "verification_type": "AUTOMATED" if automated else "HUMAN",
            auto.provenance_fields(automated)[0]: str(review["reviewer_name"]).strip(),
            auto.provenance_fields(automated)[1]: review.get("reviewed_at"),
            "contract": {
                "complete_reviewed_turn_not_fixed_window": True,
                "maximum_turn_seconds": None,
                "semantic_findings_require_review": True,
                "automatic_trait_or_rank_effect": False,
                "leadership_baseline_requires_pre_current_role": True,
                "complete_interior_acoustic_coverage_required": True,
                "boundary_margins_require_human_review": not automated,
                "automated_verification_is_human_review": False,
            },
        }
        row["row_sha256"] = row_hash(row)
        output.append(row)

    for name, path in input_paths.items():
        if SMR.sha256_file(path) != input_hashes[name]:
            raise PromotionRefusal(f"input_changed_during_materialization:{name}")
    for path, digest in proof_hashes.items():
        if SMR.sha256_file(path) != digest:
            raise PromotionRefusal(f"automatic_proof_changed_during_materialization:{path}")
    SMR.write_jsonl_atomic(args.out, sorted(output, key=lambda row: (row["member"], row["turn_id"])))
    durations = [float(row["duration_seconds"]) for row in output]
    receipt = {
        "schema": "member-longform-reviewed-turn-receipt-v1",
        "status": "PASS",
        "generated_at": generated_at,
        "artifact": str(args.out.resolve()),
        "artifact_sha256": SMR.sha256_file(args.out),
        "counts": {
            "turns": len(output),
            "members": len({row["member"] for row in output}),
            "sources": len({row["source_id"] for row in output}),
            "turns_over_one_minute": sum(value >= 60.0 for value in durations),
            "turns_over_two_minutes": sum(value >= 120.0 for value in durations),
            "total_turn_minutes": round(sum(durations) / 60.0, 3),
            "longest_turn_seconds": max(durations) if durations else 0.0,
        },
        "contract": {
            "turn_definition": "member takes the floor through the next speaker handoff",
            "maximum_turn_seconds": None,
            "human_identity_boundary_semantic_review_required": not any(row["verification_type"] == "AUTOMATED" for row in output),
            "user_authorized_automated_verification_allowed": True,
            "current_leader_baseline_is_strictly_pre_current_role": True,
            "automatic_trait_or_rank_effect": False,
        },
        "inputs": {
            name + "_sha256": digest for name, digest in input_hashes.items()
        },
        "input_paths": {
            "selection": str(args.selection.resolve()), "candidates": str(args.candidates.resolve()),
            "continuity": str(args.continuity.resolve()), "reviews": str(args.reviews.resolve()),
        },
        "additional_proof_inputs": [{"path": str(path.resolve()), "sha256": digest}
                                    for path, digest in sorted(proof_hashes.items())],
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Apply acoustic continuity gates to proposed complete interview turns.

For members in the speaker vault, exact windows must beat every other vaulted
speaker under that member's zero-false-accept calibration. Cold-start members
receive only a rejection/within-recording continuity result. This stage does
not approve turn boundaries or change profiles.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import numpy as np

from fec.members.b5_audio_embed import cosine, get_backend
from fec.speech import source_media_recovery as SMR
from fec.speech import verify_local_youtube_exact_windows as exact


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data/speech/member_longform_full_turn_candidates_v1.jsonl"
DEFAULT_OUTPUT = ROOT / "data/speech/member_longform_speaker_continuity_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_speaker_continuity_v1.receipt.json"
DEFAULT_ANCHOR_RECEIPT = ROOT / "data/speech/speaker_anchor_vault_all_verified_v3.receipt.json"
DEFAULT_CALIBRATION = ROOT / "data/speech/speaker_anchor_calibration_all_verified_v3.json"
DEFAULT_MODEL = Path(r"C:\melange-data\models\ecapa-voxceleb")
DEFAULT_MODEL_MANIFEST = ROOT / "data/speech/ecapa_model_manifest_v2.json"
DEFAULT_CANONICAL = Path(r"C:\melange-data\member_clips\clips_manifest.jsonl")
DEFAULT_VERIFIED_INTERVIEWS = ROOT / "data/speech/member_interview_descriptive_v1.json"
WINDOW_SECONDS = 8.0
TURN_INSET_SECONDS = 2.0
REQUIRED_CONSECUTIVE_WINDOWS = 3
# Zero means unlimited. A positive explicit budget must produce visible
# incomplete-coverage results, never a whole-turn continuity pass.
MAX_WINDOWS_PER_MEMBER = 0
CONTINUITY_CONTRACT_VERSION = "whole-turn-interior-coverage-v2"
COLD_OTHER_SPEAKER_FLOOR = 0.595722615
COLD_SEED_COHERENCE_FLOOR = 0.595722615
SUPPLEMENTAL_REFERENCE_MARGIN = 0.03
SUPPLEMENTAL_REFERENCE_SCORE_FLOOR = 0.55


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def candidate_starts(turn: dict) -> list[float]:
    turn_start, turn_end = float(turn["start_sec"]), float(turn["end_sec"])
    if (not math.isfinite(turn_start) or not math.isfinite(turn_end)
            or turn_start < 0 or turn_end <= turn_start):
        raise ValueError("invalid turn interval")
    start, stop = turn_start + TURN_INSET_SECONDS, turn_end - TURN_INSET_SECONDS
    output = []
    while start + WINDOW_SECONDS <= stop + 1e-9:
        output.append(round(start, 6))
        start += WINDOW_SECONDS
    # The final calibrated window may overlap its predecessor. It is for
    # coverage only and must not inflate the consecutive nonoverlapping run.
    tail_start = round(stop - WINDOW_SECONDS, 6)
    if output and tail_start > output[-1] + 1e-6:
        output.append(tail_start)
    return output


def turn_binding_sha256(source: dict, turn: dict) -> str:
    bound = {
        "member": source.get("member"), "source_id": source.get("source_id"),
        "wav_sha256": source.get("wav_sha256"),
        "transcript_sha256": source.get("transcript_sha256"),
        "start_sec": round(float(turn["start_sec"]), 6),
        "end_sec": round(float(turn["end_sec"]), 6),
    }
    return hashlib.sha256(json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def production_keys(row: dict) -> set[str]:
    """Known aliases of one production, not the count of reference windows."""
    keys = {
        "id:" + str(row[key]).strip().casefold()
        for key in ("production_id", "source_id", "event_id", "sid") if row.get(key)
    }
    digest = row.get("source_audio_sha256") or row.get("wav_sha256")
    if digest:
        keys.add("audio:" + str(digest).casefold())
    source_path = row.get("source_wav") or row.get("src_wav")
    if source_path:
        keys.add("path:" + os.path.normcase(str(Path(str(source_path)).resolve())))
    for key in ("url", "requested_url", "audio_url"):
        if not row.get(key):
            continue
        parsed = urlsplit(str(row[key]))
        host = (parsed.hostname or "").lower().removeprefix("www.")
        video_id = (parse_qs(parsed.query).get("v") or [None])[0]
        if host == "youtu.be":
            video_id = parsed.path.strip("/")
        if host in {"youtube.com", "m.youtube.com", "youtu.be"} and video_id:
            keys.add("id:" + video_id.casefold())
        else:
            keys.add("url:" + str(row[key]).split("#", 1)[0].rstrip("/"))
    return keys


def independent_reference_groups(references: list[dict], source: dict) -> tuple[list[list[int]], dict]:
    """Collapse connected production aliases and exclude the candidate itself."""
    groups: list[tuple[set[str], list[int]]] = []
    unidentified = 0
    for index, reference in enumerate(references):
        keys = production_keys(reference)
        if not keys:
            unidentified += 1
            continue
        indexes = [index]
        remainder = []
        for known_keys, known_indexes in groups:
            if keys & known_keys:
                keys |= known_keys
                indexes.extend(known_indexes)
            else:
                remainder.append((known_keys, known_indexes))
        # Alias bridges can connect a previously nonmatching earlier group.
        changed = True
        while changed:
            changed = False
            for known_keys, known_indexes in list(remainder):
                if keys & known_keys:
                    keys |= known_keys
                    indexes.extend(known_indexes)
                    remainder.remove((known_keys, known_indexes))
                    changed = True
        groups = remainder + [(keys, sorted(indexes))]
    source_keys = production_keys({**source, "source_wav": source.get("wav")})
    allowed = [indexes for keys, indexes in groups if not keys & source_keys]
    allowed.sort(key=lambda indexes: indexes[0])
    excluded = sum(len(indexes) for keys, indexes in groups if keys & source_keys)
    return allowed, {
        "reference_windows": len(references),
        "independent_productions_available": len(allowed),
        "same_production_reference_windows_excluded": excluded,
        "unidentified_production_reference_windows_excluded": unidentified,
        "duplicate_production_reference_windows": sum(max(0, len(indexes) - 1) for _, indexes in groups),
    }


def load_independent_references(references: list[dict], source: dict, backend: Any,
                                vector_cache: dict, source_hash_cache: dict) -> tuple[list, list[dict], dict]:
    """Use at most one verified window from each of three other productions.

    Canonical event aliases are enriched with the full recording hash when that
    recording remains available. This catches byte-identical copies stored under
    different paths/IDs without embedding every canonical clip in advance.
    """
    groups, audit = independent_reference_groups(references, source)
    source_keys = production_keys({**source, "source_wav": source.get("wav")})
    selected_keys: set[str] = set()
    vectors, selected = [], []
    late_same_source = late_duplicate = invalid_references = 0
    for indexes in groups:
        for index in indexes:
            reference = dict(references[index])
            original_path = reference.get("source_wav") or reference.get("src_wav")
            if original_path and Path(str(original_path)).is_file():
                original_path = str(Path(str(original_path)).resolve())
                if original_path not in source_hash_cache:
                    source_hash_cache[original_path] = SMR.sha256_file(Path(original_path))
                actual_hash = source_hash_cache[original_path]
                recorded_hash = reference.get("source_audio_sha256")
                if recorded_hash and recorded_hash != actual_hash:
                    invalid_references += 1
                    continue
                reference["source_audio_sha256"] = actual_hash
            keys = production_keys(reference)
            if keys & source_keys:
                late_same_source += 1
                continue
            if keys & selected_keys:
                late_duplicate += 1
                continue
            path = Path(str(reference.get("wav") or ""))
            if not path.is_file():
                invalid_references += 1
                continue
            cache_key = (str(path.resolve()), reference.get("sha256"), reference.get("pcm_sha256"))
            if cache_key not in vector_cache:
                if reference.get("sha256") and SMR.sha256_file(path) != reference["sha256"]:
                    invalid_references += 1
                    continue
                accepted = exact.read_pcm16(path)
                if reference.get("pcm_sha256"):
                    if (accepted.size != exact.WINDOW_FRAMES
                            or hashlib.sha256(accepted.astype("<i2", copy=False).tobytes()).hexdigest()
                            != reference["pcm_sha256"]):
                        invalid_references += 1
                        continue
                elif accepted.size < exact.WINDOW_FRAMES:
                    invalid_references += 1
                    continue
                offset = max(0, (accepted.size - exact.WINDOW_FRAMES) // 2)
                vector_cache[cache_key] = np.asarray(backend.embed(
                    accepted[offset:offset + exact.WINDOW_FRAMES].astype(np.float32) / 32768.0,
                    exact.SAMPLE_RATE,
                ), dtype=np.float32)
            vectors.append(vector_cache[cache_key])
            selected.append(reference)
            selected_keys |= keys
            break
        if len(selected) >= 3:
            break
    audit.update({
        "independent_productions_selected": len(selected),
        "same_source_references_excluded_after_audio_hash_check": late_same_source,
        "duplicate_references_excluded_after_audio_hash_check": late_duplicate,
        "invalid_reference_windows_skipped": invalid_references,
        "production_identity_basis": "recorded_production_aliases_and_available_full_audio_hashes",
    })
    return vectors, selected, audit


def coverage_gaps(start: float, end: float, intervals: list[tuple[float, float]]) -> list[list[float]]:
    cursor = start
    gaps = []
    for left, right in sorted(intervals):
        left, right = max(start, left), min(end, right)
        if right <= left:
            continue
        if left > cursor + 1e-6:
            gaps.append([round(cursor, 6), round(left, 6)])
        cursor = max(cursor, right)
    if cursor < end - 1e-6:
        gaps.append([round(cursor, 6), round(end, 6)])
    return gaps


def summarize_turn_coverage(source: dict, turn: dict, turn_index: int,
                            evaluated: list[dict], *, budget_exhausted: bool) -> dict:
    binding = turn_binding_sha256(source, turn)
    planned = candidate_starts(turn)
    start, end = float(turn["start_sec"]), float(turn["end_sec"])
    interior_start = min(end, start + TURN_INSET_SECONDS)
    interior_end = max(interior_start, end - TURN_INSET_SECONDS)
    rows = [row for row in evaluated if row.get("turn_binding_sha256") == binding
            and row.get("turn_index") == turn_index]
    expected = {(round(value, 6), round(value + WINDOW_SECONDS, 6)) for value in planned}
    expected_indexes = {(round(value, 6), round(value + WINDOW_SECONDS, 6)): index
                        for index, value in enumerate(planned)}
    actual = {(round(float(row["start_sec"]), 6), round(float(row["end_sec"]), 6)) for row in rows}
    valid_rows = []
    for row in rows:
        span = (round(float(row["start_sec"]), 6), round(float(row["end_sec"]), 6))
        if span not in expected or row.get("window_index") != expected_indexes[span]:
            continue
        # Recompute run eligibility from geometry instead of trusting a stored
        # flag; an overlapping coverage-tail window is not a third anchor.
        valid_rows.append({**row, "anchor_eligible":
                           abs(span[0] - (planned[0] + expected_indexes[span] * WINDOW_SECONDS)) < 1e-6})
    evaluated_intervals = [(float(row["start_sec"]), float(row["end_sec"])) for row in valid_rows]
    accepted_intervals = [(float(row["start_sec"]), float(row["end_sec"]))
                          for row in valid_rows if row.get("accepted") is True]
    interior_seconds = max(0.0, interior_end - interior_start)
    gaps = coverage_gaps(interior_start, interior_end, evaluated_intervals) if interior_seconds else []
    accepted_gaps = coverage_gaps(interior_start, interior_end, accepted_intervals) if interior_seconds else []
    complete = (bool(expected) and expected == actual and len(rows) == len(valid_rows) == len(expected)
                and not gaps)
    run = maximum_consecutive_passes(valid_rows)
    rejected = sum(row.get("accepted") is not True for row in valid_rows)
    gate = complete and rejected == 0 and run >= REQUIRED_CONSECUTIVE_WINDOWS
    status = (
        "COMPLETE_INTERIOR_CONTINUITY_PASSED_REVIEW_REQUIRED" if gate else
        "INCOMPLETE_WINDOW_BUDGET" if budget_exhausted and not complete else
        "TURN_TOO_SHORT_FOR_CALIBRATED_CONTINUITY"
        if interior_seconds < REQUIRED_CONSECUTIVE_WINDOWS * WINDOW_SECONDS else
        "CONTINUITY_REJECTED_WINDOWS" if complete and rejected else
        "CONTINUITY_INCONCLUSIVE"
    )
    return {
        "turn_index": turn_index, "turn_binding_sha256": binding,
        "start_sec": start, "end_sec": end, "status": status,
        "planned_windows": len(planned), "evaluated_windows": len(valid_rows),
        "rejected_windows": rejected, "maximum_consecutive_passing_windows": run,
        "coverage_scope": "turn_interior_excluding_two_second_boundary_margins",
        "interior_start_sec": interior_start, "interior_end_sec": interior_end,
        "interior_coverage_complete": complete, "interior_coverage_gaps": gaps,
        "accepted_interior_coverage_gaps": accepted_gaps,
        "continuity_gate_passed": gate,
        "full_turn_acoustically_verified": False,
        "boundary_margins_require_human_review": True,
        "speaker_identity_authority": False,
    }


def maximum_consecutive_passes(rows: list[dict]) -> int:
    best = current = 0
    prior_turn = None
    prior_index = None
    for row in rows:
        contiguous = row["turn_index"] == prior_turn and row["window_index"] == (prior_index or 0) + 1
        if row.get("anchor_eligible", True) and row["accepted"]:
            current = current + 1 if contiguous else 1
            best = max(best, current)
        else:
            current = 0
        prior_turn = row["turn_index"]
        prior_index = row["window_index"]
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--anchor-receipt", type=Path, default=DEFAULT_ANCHOR_RECEIPT)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--model-manifest", type=Path, default=DEFAULT_MODEL_MANIFEST)
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--verified-interviews", type=Path, default=DEFAULT_VERIFIED_INTERVIEWS)
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--max-windows-per-member", type=int, default=MAX_WINDOWS_PER_MEMBER,
                        help="Explicit window budget across each member's sources; 0 checks every turn interior")
    args = parser.parse_args()
    if args.max_windows_per_member < 0:
        parser.error("--max-windows-per-member must be nonnegative; 0 means unlimited")

    source_rows = [
        row for row in SMR.read_jsonl(args.input, required=True)
        if row.get("status") == "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED"
        and (not args.member or row.get("member") in set(args.member))
    ]
    anchor_receipt = json.loads(args.anchor_receipt.read_text(encoding="utf-8-sig"))
    vectors_path = Path(str(anchor_receipt["combined_vectors"]))
    if SMR.sha256_file(vectors_path) != anchor_receipt.get("combined_vectors_sha256"):
        raise SystemExit("anchor vector hash mismatch")
    with np.load(vectors_path, allow_pickle=False) as vault:
        vault_members = np.asarray(vault["members"]).astype(str)
        vault_vectors = np.asarray(vault["vectors"], dtype=np.float32)
    calibration = json.loads(args.calibration.read_text(encoding="utf-8-sig"))
    if calibration.get("false_accepts") != 0 or calibration.get("all_vaulted_speakers_compared") is not True:
        raise SystemExit("speaker calibration is not zero-false-accept/all-vault")
    thresholds = calibration.get("per_claim_thresholds") or {}
    backend = get_backend("ecapa", model_dir=str(args.model), model_manifest=str(args.model_manifest))

    # Members absent from the current speaker-vault cohort may still have three
    # independent, already accepted canonical event clips.  Those references
    # provide a named-speaker continuity check against every vaulted competitor.
    # This is intentionally labeled supplemental, never zero-false-accept.
    canonical_rows = SMR.read_jsonl(args.canonical, required=True)
    reference_pool: dict[str, list[dict]] = {}
    needed = {str(row["member"]) for row in source_rows}
    for row in canonical_rows:
        member = str(row.get("member") or "")
        if member not in needed or not row.get("sha256"):
            continue
        reference_pool.setdefault(member, []).append({
            "event_id": row.get("sid") or row.get("event_id"),
            "production_id": row.get("production_id"),
            "source_wav": row.get("src_wav") or row.get("source_wav"),
            "source_audio_sha256": row.get("source_audio_sha256"),
            "url": row.get("url"), "wav": row.get("out_wav"), "sha256": row["sha256"],
            "reference_basis": "accepted_canonical_event_clip",
        })

    verified_interviews = json.loads(args.verified_interviews.read_text(encoding="utf-8-sig"))
    if verified_interviews.get("status") != "DISPLAY_ONLY_NO_ENGINE_WIRING":
        raise SystemExit("verified interview artifact is not display-only")
    for source in verified_interviews.get("verified_sources") or []:
        member = str(source.get("member") or "")
        if member not in needed:
            continue
        source_id = str(source.get("source_id") or "")
        for window in source.get("windows") or []:
            if window.get("accepted") is not True or not window.get("pcm_sha256"):
                continue
            reference_pool.setdefault(member, []).append({
                "event_id": source_id,
                "production_id": source.get("production_id"),
                "source_wav": source.get("source_wav"),
                "source_audio_sha256": source.get("source_audio_sha256"),
                "url": source.get("url"), "requested_url": source.get("requested_url"),
                "window_start_sec": window.get("start_sec"),
                "wav": window.get("wav"),
                "pcm_sha256": window.get("pcm_sha256"),
                "reference_basis": "accepted_exact_interview_window",
            })

    output = []
    reference_vector_cache: dict = {}
    source_hash_cache: dict = {}
    evaluated_by_member: dict[str, int] = {}
    for source in sorted(source_rows, key=lambda row: row["member"]):
        member = str(source["member"])
        wav = Path(str(source["wav"])).resolve()
        transcript = Path(str(source["transcript"])).resolve()
        if SMR.sha256_file(wav) != source.get("wav_sha256"):
            raise SystemExit(f"WAV hash mismatch: {member}")
        if SMR.sha256_file(transcript) != source.get("transcript_sha256"):
            raise SystemExit(f"transcript hash mismatch: {member}")
        samples = exact.read_pcm16(wav)
        words = SMR.read_jsonl(transcript, required=True)
        target_mask = vault_members == member
        anchored = bool(np.any(target_mask)) and member in thresholds
        source_hash_cache[str(wav)] = source["wav_sha256"]
        if anchored:
            supplemental_target, supplemental_sources, reference_audit = [], [], {"not_used_for_vaulted_member": True}
        else:
            supplemental_target, supplemental_sources, reference_audit = load_independent_references(
                reference_pool.get(member, []), source, backend, reference_vector_cache, source_hash_cache)
        supplemented = not anchored and len(supplemental_target) >= 3
        claim_threshold = thresholds.get(member) or {}
        seed_vector = None
        evaluated = []
        turns = source.get("turns") or []
        method = (
            "all-vault-per-claim-zero-false-accept-continuity" if anchored else
            "independent-verified-event-reference-vs-all-vault-continuity" if supplemented else
            "cold-start-all-vault-rejection-and-within-recording-continuity"
        )
        budget_exhausted = False
        for turn_index, turn in enumerate(turns):
            starts = candidate_starts(turn)
            binding = turn_binding_sha256(source, turn)
            for window_index, start in enumerate(starts):
                if (args.max_windows_per_member > 0
                        and evaluated_by_member.get(member, 0) >= args.max_windows_per_member):
                    budget_exhausted = True
                    break
                end = start + WINDOW_SECONDS
                payload = exact.exact_payload(samples, start)
                signal_i16 = np.frombuffer(payload, dtype="<i2").copy()
                signal = signal_i16.astype(np.float32) / 32768.0
                window_words = exact.overlapping_words(words, start, end)
                voiced_ratio, max_silence = exact.voice_activity(signal_i16)
                vector = np.asarray(backend.embed(signal, exact.SAMPLE_RATE), dtype=np.float32)
                half_score = cosine(
                    backend.embed(signal[: exact.WINDOW_FRAMES // 2], exact.SAMPLE_RATE),
                    backend.embed(signal[exact.WINDOW_FRAMES // 2 :], exact.SAMPLE_RATE),
                )
                reasons = []
                if len(window_words) < exact.MIN_ASR_WORDS:
                    reasons.append("too_few_transcript_words")
                if voiced_ratio < exact.MIN_VOICED_RATIO:
                    reasons.append("voiced_ratio_below_floor")
                if max_silence > exact.MAX_INTERNAL_SILENCE_SECONDS:
                    reasons.append("internal_silence_above_ceiling")
                if half_score < exact.HALF_CONSISTENCY_REJECT_FLOOR:
                    reasons.append("half_voice_inconsistent")
                scores = vault_vectors @ vector
                if anchored:
                    target_best = float(np.max(scores[target_mask]))
                    competitor_scores = scores[~target_mask]
                    competitor_members = vault_members[~target_mask]
                    best_index = int(np.argmax(competitor_scores))
                    competitor_best = float(competitor_scores[best_index])
                    competitor_member = str(competitor_members[best_index])
                    margin = target_best - competitor_best
                    if target_best < float(claim_threshold["minimum_target_best_score"]):
                        reasons.append("target_score_below_per_claim_floor")
                    if margin < float(claim_threshold["minimum_best_anchor_margin"]):
                        reasons.append("margin_below_per_claim_floor")
                    seed_score = None
                    method = "all-vault-per-claim-zero-false-accept-continuity"
                elif supplemented:
                    target_best = max(cosine(vector, reference) for reference in supplemental_target)
                    best_index = int(np.argmax(scores))
                    competitor_best = float(scores[best_index])
                    competitor_member = str(vault_members[best_index])
                    margin = target_best - competitor_best
                    seed_score = None
                    if target_best < SUPPLEMENTAL_REFERENCE_SCORE_FLOOR:
                        reasons.append("supplemental_target_reference_score_below_floor")
                    if margin < SUPPLEMENTAL_REFERENCE_MARGIN:
                        reasons.append("supplemental_target_reference_not_unique_winner")
                    method = "independent-verified-event-reference-vs-all-vault-continuity"
                else:
                    target_best = None
                    best_index = int(np.argmax(scores))
                    competitor_best = float(scores[best_index])
                    competitor_member = str(vault_members[best_index])
                    margin = None
                    seed_score = None if seed_vector is None else cosine(vector, seed_vector)
                    if competitor_best >= COLD_OTHER_SPEAKER_FLOOR:
                        reasons.append("matches_existing_vaulted_speaker")
                    if seed_score is not None and seed_score < COLD_SEED_COHERENCE_FLOOR:
                        reasons.append("cold_seed_voice_inconsistent")
                    method = "cold-start-all-vault-rejection-and-within-recording-continuity"
                accepted = not reasons
                if accepted and not anchored and seed_vector is None:
                    seed_vector = vector
                evaluated.append({
                    "turn_index": turn_index,
                    "turn_binding_sha256": binding,
                    "window_index": window_index,
                    "anchor_eligible": abs(start - (starts[0] + window_index * WINDOW_SECONDS)) < 1e-6,
                    "start_sec": start,
                    "end_sec": end,
                    "accepted": accepted,
                    "refusal_reasons": reasons,
                    "pcm_sha256": hashlib.sha256(payload).hexdigest(),
                    "transcript_words": len(window_words),
                    "voiced_ratio": round(voiced_ratio, 6),
                    "maximum_internal_silence_sec": round(max_silence, 6),
                    "half_consistency_score": round(float(half_score), 9),
                    "target_best_score": None if target_best is None else round(target_best, 9),
                    "competitor_best_score": round(competitor_best, 9),
                    "competitor_member": competitor_member,
                    "target_margin": None if margin is None else round(margin, 9),
                    "seed_coherence_score": None if seed_score is None else round(float(seed_score), 9),
                })
                evaluated_by_member[member] = evaluated_by_member.get(member, 0) + 1
            if budget_exhausted:
                break
        run = maximum_consecutive_passes(evaluated)
        turn_coverage = [summarize_turn_coverage(source, turn, index, evaluated,
                         budget_exhausted=budget_exhausted) for index, turn in enumerate(turns)]
        continuity_passed = bool(turn_coverage) and all(row["continuity_gate_passed"] for row in turn_coverage)
        coverage_complete = bool(turn_coverage) and all(row["interior_coverage_complete"] for row in turn_coverage)
        status = (
            "ANCHORED_SPEAKER_CONTINUITY_PASSED_REVIEW_REQUIRED"
            if continuity_passed and anchored else
            "SUPPLEMENTAL_SPEAKER_CONTINUITY_PASSED_REVIEW_REQUIRED"
            if continuity_passed and supplemented else
            "COLD_START_CONTINUITY_PASSED_REVIEW_REQUIRED"
            if continuity_passed else
            "INCOMPLETE_WINDOW_BUDGET" if budget_exhausted and not coverage_complete else
            "SPEAKER_CONTINUITY_INCONCLUSIVE"
        )
        output.append({
            "schema": "member-longform-speaker-continuity-v1",
            "coverage_contract_version": CONTINUITY_CONTRACT_VERSION,
            "status": status,
            "member": member,
            "source_id": source.get("source_id"),
            "url": source.get("url"),
            "wav": str(wav),
            "wav_sha256": source["wav_sha256"],
            "transcript": str(transcript),
            "transcript_sha256": source["transcript_sha256"],
            "anchored_member": anchored,
            "supplemental_member": supplemented,
            "supplemental_identity_references": supplemental_sources,
            "supplemental_reference_audit": reference_audit,
            "method": method,
            "evaluated_windows": evaluated,
            "turn_coverage": turn_coverage,
            "planned_windows": sum(row["planned_windows"] for row in turn_coverage),
            "interior_coverage_complete": coverage_complete,
            "budget_exhausted": budget_exhausted,
            "max_windows_per_member": args.max_windows_per_member,
            "maximum_consecutive_passing_windows": run,
            "speaker_continuity_passed": continuity_passed,
            "reviewable_turns": sum(row["continuity_gate_passed"] for row in turn_coverage),
            "full_turn_acoustically_verified": False,
            "boundary_margins_require_human_review": True,
            "speaker_identity_authority": False,
            "turn_boundary_reviewed": False,
            "profile_effect": "NONE_UNTIL_HUMAN_TURN_REVIEW",
            "verified_at": utc_now(),
        })
        print(f"continuity {member}: {status} run={run} windows={len(evaluated)}/"
              f"{sum(row['planned_windows'] for row in turn_coverage)}", flush=True)

    SMR.write_jsonl_atomic(args.output, output)
    counts = {
        "candidate_members": len(output),
        "anchored_passed_review_required": sum(row["status"].startswith("ANCHORED_") for row in output),
        "supplemental_passed_review_required": sum(row["status"].startswith("SUPPLEMENTAL_") for row in output),
        "cold_start_passed_review_required": sum(row["status"].startswith("COLD_START_") for row in output),
        "inconclusive": sum(row["status"] == "SPEAKER_CONTINUITY_INCONCLUSIVE" for row in output),
        "incomplete_window_budget": sum(row["status"] == "INCOMPLETE_WINDOW_BUDGET" for row in output),
        "planned_windows": sum(row["planned_windows"] for row in output),
        "evaluated_windows": sum(len(row["evaluated_windows"]) for row in output),
        "candidate_turns": sum(len(row["turn_coverage"]) for row in output),
        "reviewable_turns": sum(row["reviewable_turns"] for row in output),
        "incomplete_turns": sum(not turn["interior_coverage_complete"]
                                for row in output for turn in row["turn_coverage"]),
    }
    receipt = {
        "schema": "member-longform-speaker-continuity-receipt-v1",
        "coverage_contract_version": CONTINUITY_CONTRACT_VERSION,
        "status": "PASS_REVIEW_QUEUE_ONLY",
        "generated_at": utc_now(),
        "counts": counts,
        "contract": {
            "all_vaulted_speakers_compared_for_anchored_claims": True,
            "required_consecutive_passing_windows": REQUIRED_CONSECUTIVE_WINDOWS,
            "window_seconds": WINDOW_SECONDS,
            "boundary_margin_seconds": TURN_INSET_SECONDS,
            "max_windows_per_member": args.max_windows_per_member,
            "complete_turn_interior_coverage_required": True,
            "all_interior_windows_must_pass": True,
            "overlapping_tail_window_counts_toward_anchor_run": False,
            "same_source_supplemental_references_allowed": False,
            "supplemental_reference_count_unit": "independent_production_not_window",
            "full_turn_acoustically_verified": False,
            "cold_start_is_rejection_only": True,
            "turn_boundaries_are_human_reviewed": False,
            "supplemental_three_event_continuity_is_not_zero_false_accept_calibration": True,
            "profile_effect_before_human_review": "NONE",
        },
        "inputs": {
            "turn_candidates_sha256": SMR.sha256_file(args.input),
            "anchor_receipt_sha256": SMR.sha256_file(args.anchor_receipt),
            "calibration_sha256": SMR.sha256_file(args.calibration),
            "canonical_manifest_sha256": SMR.sha256_file(args.canonical),
            "verified_interviews_sha256": SMR.sha256_file(args.verified_interviews),
        },
        "output": {"path": str(args.output.resolve()), "sha256": SMR.sha256_file(args.output)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

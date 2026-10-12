#!/usr/bin/env python3
"""Verify and measure reviewed long-form member-interview excerpts.

This is a small, display-only ingress lane for user-supplied and search-found
interviews.  Source titles and search results never establish identity.  A
reviewed transcript boundary must follow an in-band full-name cue, and exact
eight-second windows must clear voice/activity gates.  Members already in the
speaker vault must also win against every other vaulted speaker at the locked
zero-false-accept thresholds.  A member absent from the vault uses the existing
cold-start rule: the first named reply seeds the voice and later windows must
agree with it while failing to match any vaulted speaker.

The output may describe delivery and source coverage.  It must not modify Big
Five estimates, rankings, stance, allocation, or persuasion scores.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import unicodedata
import wave

import numpy as np

from fec.members.b5_audio_embed import cosine, get_backend
from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES = ROOT / "data/speech/member_interview_sources_20260903.jsonl"
DEFAULT_ASR = ROOT / "data/speech/member_interview_asr_20260903.jsonl"
DEFAULT_SPANS = ROOT / "data/speech/member_interview_reviewed_spans_20260903.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_interview_descriptive_v1.json"
DEFAULT_RECEIPT = ROOT / "data/speech/member_interview_descriptive_v1.receipt.json"
DEFAULT_ANCHOR_RECEIPT = ROOT / "data/speech/speaker_anchor_vault_all_verified_v3.receipt.json"
DEFAULT_CALIBRATION = ROOT / "data/speech/speaker_anchor_calibration_v1.json"
DEFAULT_MODEL = Path(r"C:\melange-data\models\ecapa-voxceleb")
DEFAULT_MODEL_MANIFEST = ROOT / "data/speech/ecapa_model_manifest_v2.json"
DEFAULT_CLIP_ROOT = Path(r"C:\melange-data\member_interview_exact_windows_20260903")
DEFAULT_CANONICAL = Path(r"C:\melange-data\member_clips\clips_manifest.jsonl")

SAMPLE_RATE = 16_000
WINDOW_SECONDS = 8.0
WINDOW_FRAMES = int(SAMPLE_RATE * WINDOW_SECONDS)
MAX_WINDOWS_PER_SOURCE = 5
MIN_RUN_WINDOWS = 3
MIN_VOICED_RATIO = 0.80
MAX_INTERNAL_SILENCE_SECONDS = 1.20
MIN_ASR_WORDS = 8
HALF_CONSISTENCY_REJECT_FLOOR = 0.35
MAX_NAME_TO_WINDOW_SECONDS = 120.0
SUPPLEMENTAL_REFERENCE_MARGIN = 0.03
SUPPLEMENTAL_REFERENCE_SCORE_FLOOR = 0.55
FEATURE_COLUMNS = {
    "pause": "MeanUnvoicedSegmentLength",
    "pace": "VoicedSegmentsPerSec",
    "volume_dyn": "loudness_sma3_stddevNorm",
    "pitch_var": "F0semitoneFrom27.5Hz_sma3nz_stddevNorm",
    "strain": "jitterLocal_sma3nz_amean",
    "f0_mean": "F0semitoneFrom27.5Hz_sma3nz_amean",
}
FIRST_NAME_EQUIVALENTS = (
    frozenset({"stephen", "steven", "steve"}),
    frozenset({"alexander", "alex"}),
)


class VerificationRefusal(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def evidence_names_member(evidence: str, member: str) -> bool:
    evidence_tokens = normalized(evidence).split()
    member_tokens = normalized(member).split()
    if len(member_tokens) < 2:
        return False
    approved_firsts = {member_tokens[0]}
    for group in FIRST_NAME_EQUIVALENTS:
        if member_tokens[0] in group:
            approved_firsts.update(group)
    return bool(approved_firsts.intersection(evidence_tokens)) and any(
        difflib.SequenceMatcher(None, member_tokens[-1], token, autojunk=False).ratio() >= 0.65
        for token in evidence_tokens
    )


def read_pcm16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getframerate(), source.getsampwidth()) != (1, SAMPLE_RATE, 2):
            raise VerificationRefusal(f"source_wav_contract:{path}")
        return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").copy()


def exact_payload(samples: np.ndarray, start_sec: float) -> bytes:
    start = round(start_sec * SAMPLE_RATE)
    end = start + WINDOW_FRAMES
    if start < 0 or end > samples.size:
        raise VerificationRefusal("exact_window_out_of_bounds")
    return np.asarray(samples[start:end], dtype="<i2").tobytes()


def voice_activity(samples: np.ndarray) -> tuple[float, float]:
    width = int(0.02 * SAMPLE_RATE)
    frames = samples[: samples.size - samples.size % width].reshape(-1, width)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) / 32768.0
    positive = rms[rms > 0]
    threshold = max(0.003, float(np.percentile(positive, 60) * 0.15) if positive.size else 0.0)
    voiced = rms >= threshold
    longest = current = 0
    for value in voiced:
        if value:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return float(np.mean(voiced)) if voiced.size else 0.0, longest * 0.02


def overlapping_words(words: list[dict], start: float, end: float) -> list[dict]:
    return [row for row in words if float(row["end_sec"]) > start and float(row["start_sec"]) < end]


def candidate_starts(start: float, end: float) -> list[float]:
    value = start + 2.0
    output = []
    while value + WINDOW_SECONDS <= end - 2.0 + 1e-9:
        output.append(round(value, 6))
        value += WINDOW_SECONDS
    return output


def write_wav_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".part" + path.suffix)
    with wave.open(str(temporary), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE)
        target.writeframes(payload)
    os.replace(temporary, path)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def row_hash(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--asr", type=Path, default=DEFAULT_ASR)
    parser.add_argument("--spans", type=Path, default=DEFAULT_SPANS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--anchor-receipt", type=Path, default=DEFAULT_ANCHOR_RECEIPT)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--model-manifest", type=Path, default=DEFAULT_MODEL_MANIFEST)
    parser.add_argument("--clip-root", type=Path, default=DEFAULT_CLIP_ROOT)
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    args = parser.parse_args()

    sources = {str(row["video_id"]): row for row in SMR.read_jsonl(args.sources, required=True)}
    asr = {str(row["video_id"]): row for row in SMR.read_jsonl(args.asr, required=True)}
    spans = SMR.read_jsonl(args.spans, required=True)
    if len(sources) != len(set(sources)) or len(asr) != len(set(asr)):
        raise VerificationRefusal("duplicate_source_id")

    anchor_receipt = json.loads(args.anchor_receipt.read_text(encoding="utf-8-sig"))
    vectors_path = Path(str(anchor_receipt["combined_vectors"]))
    if SMR.sha256_file(vectors_path) != anchor_receipt["combined_vectors_sha256"]:
        raise VerificationRefusal("anchor_vectors_hash_mismatch")
    with np.load(vectors_path, allow_pickle=False) as vault:
        vault_members = np.asarray(vault["members"]).astype(str)
        vault_vectors = np.asarray(vault["vectors"], dtype=np.float32)
    calibration = json.loads(args.calibration.read_text(encoding="utf-8-sig"))
    minimum_target = float(calibration["minimum_target_best_score"])
    minimum_margin = float(calibration["minimum_best_anchor_margin"])
    backend = get_backend("ecapa", model_dir=str(args.model), model_manifest=str(args.model_manifest))

    # Some accepted descriptive members predate the current all-vault cohort.
    # For them, derive one eight-second reference from each independent,
    # already accepted canonical episode.  This is explicitly not described as
    # zero-FAR calibration: the in-band name and reviewed reply boundary remain
    # the positive identity evidence; these references are a continuity check.
    canonical_rows = SMR.read_jsonl(args.canonical, required=True)
    supplemental_vectors: dict[str, list[np.ndarray]] = {}
    supplemental_sources: dict[str, list[dict]] = {}
    needed = {str(row["member"]) for row in spans}
    for member in needed:
        seen_events = set()
        for row in canonical_rows:
            if str(row.get("member")) != member:
                continue
            event = str(row.get("sid") or row.get("event_id") or "")
            if not event or event in seen_events:
                continue
            path = Path(str(row.get("out_wav") or ""))
            if not path.is_file() or SMR.sha256_file(path) != row.get("sha256"):
                continue
            accepted = read_pcm16(path)
            if accepted.size < WINDOW_FRAMES:
                continue
            offset = max(0, (accepted.size - WINDOW_FRAMES) // 2)
            vector = backend.embed(
                accepted[offset : offset + WINDOW_FRAMES].astype(np.float32) / 32768.0,
                SAMPLE_RATE,
            )
            supplemental_vectors.setdefault(member, []).append(np.asarray(vector, dtype=np.float32))
            supplemental_sources.setdefault(member, []).append({
                "event_id": event,
                "wav": str(path.resolve()),
                "sha256": row["sha256"],
            })
            seen_events.add(event)
            if len(seen_events) >= 3:
                break

    import opensmile
    smile = opensmile.Smile(feature_set=opensmile.FeatureSet.eGeMAPSv02,
                            feature_level=opensmile.FeatureLevel.Functionals)
    generated_at = utc_now()
    source_results = []
    member_windows: dict[str, list[dict]] = {}

    for review in spans:
        source_id = str(review["source_id"])
        source = sources.get(source_id)
        transcript_row = asr.get(source_id)
        if source is None or transcript_row is None:
            raise VerificationRefusal(f"missing_source_or_asr:{source_id}")
        if source["member"] != review["member"] or transcript_row["member"] != review["member"]:
            raise VerificationRefusal(f"member_binding_mismatch:{source_id}")
        wav = Path(str(source["wav"]))
        if SMR.sha256_file(wav) != source["source_audio_sha256"]:
            raise VerificationRefusal(f"source_audio_hash_mismatch:{source_id}")
        transcript = Path(str(transcript_row["transcript"]))
        if SMR.sha256_file(transcript) != transcript_row["transcript_sha256"]:
            raise VerificationRefusal(f"transcript_hash_mismatch:{source_id}")
        words = SMR.read_jsonl(transcript, required=True)
        evidence_words = overlapping_words(words, float(review["evidence_start_sec"]),
                                            float(review["evidence_end_sec"]))
        evidence_text = " ".join(str(row["word"]).strip() for row in evidence_words)
        if not evidence_names_member(evidence_text, str(review["member"])):
            raise VerificationRefusal(f"in_band_full_name_missing:{source_id}")
        if float(review["target_start_sec"]) < float(review["evidence_end_sec"]):
            raise VerificationRefusal(f"target_precedes_name_cue:{source_id}")

        samples = read_pcm16(wav)
        target_mask = vault_members == str(review["member"])
        has_target_anchor = bool(np.any(target_mask))
        supplemental_target = supplemental_vectors.get(str(review["member"])) or []
        if len(supplemental_target) < 3:
            raise VerificationRefusal(f"insufficient_supplemental_identity_references:{source_id}")
        seed_vector = None
        evaluated = []
        reviewed_starts = review.get("window_starts")
        starts = (
            [float(value) for value in reviewed_starts]
            if isinstance(reviewed_starts, list) and reviewed_starts
            else candidate_starts(float(review["target_start_sec"]), float(review["target_end_sec"]))
        )
        if any(start < float(review["target_start_sec"]) or
               start + WINDOW_SECONDS > float(review["target_end_sec"]) for start in starts):
            raise VerificationRefusal(f"reviewed_window_outside_target_span:{source_id}")
        for window_index, start in enumerate(starts):
            end = start + WINDOW_SECONDS
            excerpt_words = overlapping_words(words, start, end)
            payload = exact_payload(samples, start)
            signal_i16 = np.frombuffer(payload, dtype="<i2").copy()
            voiced_ratio, maximum_silence = voice_activity(signal_i16)
            reasons = []
            if len(excerpt_words) < MIN_ASR_WORDS:
                reasons.append("too_few_asr_words")
            if any("?" in str(row.get("word") or "") for row in excerpt_words):
                reasons.append("question_mark_inside_window")
            if voiced_ratio < MIN_VOICED_RATIO:
                reasons.append("voiced_ratio_below_floor")
            if maximum_silence > MAX_INTERNAL_SILENCE_SECONDS:
                reasons.append("internal_silence_above_ceiling")
            signal = signal_i16.astype(np.float32) / 32768.0
            vector = backend.embed(signal, SAMPLE_RATE)
            half_score = cosine(
                backend.embed(signal[: WINDOW_FRAMES // 2], SAMPLE_RATE),
                backend.embed(signal[WINDOW_FRAMES // 2 :], SAMPLE_RATE),
            )
            if half_score < HALF_CONSISTENCY_REJECT_FLOOR:
                reasons.append("half_voice_inconsistent")
            scores = vault_vectors @ np.asarray(vector, dtype=np.float32)
            if has_target_anchor:
                vault_target_best = float(np.max(scores[target_mask]))
                competitor_best = float(np.max(scores[~target_mask]))
                competitor_member = str(vault_members[~target_mask][int(np.argmax(scores[~target_mask]))])
                vault_margin = vault_target_best - competitor_best
                if vault_target_best >= minimum_target and vault_margin >= minimum_margin:
                    target_best = vault_target_best
                    margin = vault_margin
                    identity_method = "in-band-name-plus-zero-false-accept-vault-consensus"
                else:
                    target_best = max(cosine(vector, reference) for reference in supplemental_target)
                    margin = target_best - competitor_best
                    if target_best < SUPPLEMENTAL_REFERENCE_SCORE_FLOOR:
                        reasons.append("supplemental_target_reference_score_below_floor")
                    if margin < SUPPLEMENTAL_REFERENCE_MARGIN:
                        reasons.append("supplemental_target_reference_not_unique_winner")
                    identity_method = "in-band-name-plus-three-accepted-event-continuity"
                seed_score = None
            elif supplemental_target:
                target_best = max(cosine(vector, reference) for reference in supplemental_target)
                competitor_best = float(np.max(scores))
                competitor_member = str(vault_members[int(np.argmax(scores))])
                margin = target_best - competitor_best
                if target_best < SUPPLEMENTAL_REFERENCE_SCORE_FLOOR:
                    reasons.append("supplemental_target_reference_score_below_floor")
                if margin < SUPPLEMENTAL_REFERENCE_MARGIN:
                    reasons.append("supplemental_target_reference_not_unique_winner")
                identity_method = "in-band-name-plus-three-accepted-event-continuity"
                seed_score = None
            else:
                target_best = None
                competitor_best = float(np.max(scores))
                competitor_member = str(vault_members[int(np.argmax(scores))])
                margin = None
                if competitor_best >= minimum_target:
                    reasons.append("matches_existing_vaulted_speaker")
                seed_score = None if seed_vector is None else cosine(vector, seed_vector)
                if seed_vector is not None and seed_score < minimum_target:
                    reasons.append("cold_seed_voice_inconsistent")
                if seed_vector is None and start - float(review["evidence_end_sec"]) > MAX_NAME_TO_WINDOW_SECONDS:
                    reasons.append("name_cue_not_proximate_to_cold_seed")
                identity_method = "in-band-name-plus-cold-seed-rejection-only"

            accepted = not reasons
            if accepted and seed_vector is None and not has_target_anchor:
                seed_vector = np.asarray(vector, dtype=np.float32)
            evaluated.append({
                "window_index": window_index,
                "start_sec": start,
                "end_sec": end,
                "accepted": accepted,
                "refusal_reasons": reasons,
                "asr_words": len(excerpt_words),
                "asr_text": " ".join(str(row["word"]).strip() for row in excerpt_words),
                "voiced_ratio": round(voiced_ratio, 6),
                "maximum_internal_silence_sec": round(maximum_silence, 6),
                "half_consistency_score": round(float(half_score), 9),
                "target_best_score": None if target_best is None else round(target_best, 9),
                "competitor_best_score": round(competitor_best, 9),
                "competitor_member": competitor_member,
                "target_margin": None if margin is None else round(margin, 9),
                "seed_coherence_score": None if seed_score is None else round(float(seed_score), 9),
                "pcm_sha256": sha256_bytes(payload),
                "_payload": payload,
            })

        runs, current = [], []
        for window in evaluated:
            if window["accepted"]:
                current.append(window)
            else:
                if current:
                    runs.append(current)
                current = []
        if current:
            runs.append(current)
        qualifying = [run for run in runs if len(run) >= MIN_RUN_WINDOWS]
        acceptance_geometry = (
            "manually_selected_target_only_windows"
            if reviewed_starts else "consecutive_exact_windows"
        )
        if reviewed_starts:
            noncontiguous = [row for row in evaluated if row["accepted"]]
            qualifying = [noncontiguous] if len(noncontiguous) >= MIN_RUN_WINDOWS else []
        if not qualifying and supplemental_target:
            # The supplemental references are a continuity check inside a
            # manually reviewed, uninterrupted named reply; they do not claim
            # the vault's stricter zero-FAR sequence calibration.
            noncontiguous = [row for row in evaluated if row["accepted"]]
            if len(noncontiguous) >= MIN_RUN_WINDOWS:
                qualifying = [noncontiguous]
                acceptance_geometry = "noncontiguous_windows_inside_one_reviewed_turn"
        if not qualifying:
            failures = Counter(reason for row in evaluated for reason in row["refusal_reasons"])
            raise VerificationRefusal(f"no_qualifying_identity_run:{source_id}:{dict(failures)}")
        accepted_run = max(qualifying, key=lambda run: (len(run), -run[0]["start_sec"]))[:MAX_WINDOWS_PER_SOURCE]
        persisted = []
        for window in accepted_run:
            filename = (f"{normalized(review['member']).replace(' ', '_')}__{source_id}__"
                        f"{round(window['start_sec'] * 1000):09d}.wav")
            out_wav = args.clip_root / filename
            if out_wav.exists():
                with wave.open(str(out_wav), "rb") as check:
                    existing_payload = check.readframes(check.getnframes())
                if sha256_bytes(existing_payload) != window["pcm_sha256"]:
                    raise VerificationRefusal(f"existing_pcm_mismatch:{out_wav}")
            else:
                write_wav_atomic(out_wav, window["_payload"])
            feature_row = smile.process_file(str(out_wav)).iloc[0]
            features = {name: round(float(feature_row[column]), 8)
                        for name, column in FEATURE_COLUMNS.items()}
            if not all(math.isfinite(value) for value in features.values()):
                raise VerificationRefusal(f"nonfinite_features:{out_wav}")
            clean = {key: value for key, value in window.items() if key != "_payload"}
            clean.update({"wav": str(out_wav.resolve()), "features": features})
            clean["window_row_sha256"] = row_hash(clean)
            persisted.append(clean)
            member_windows.setdefault(str(review["member"]), []).append(clean)

        source_results.append({
            "source_id": source_id,
            "member": review["member"],
            "title": source["title"],
            "channel": source["channel"],
            "published": source["published"],
            "url": source["url"],
            "requested_url": source.get("requested_url"),
            "discovery": source["discovery"],
            "discovery_query": source.get("discovery_query"),
            "source_wav": str(wav.resolve()),
            "source_audio_sha256": source["source_audio_sha256"],
            "transcript": str(transcript.resolve()),
            "transcript_sha256": transcript_row["transcript_sha256"],
            "identity_evidence": evidence_text,
            "identity_evidence_start_sec": review["evidence_start_sec"],
            "identity_evidence_end_sec": review["evidence_end_sec"],
            "reviewed_target_span": [review["target_start_sec"], review["target_end_sec"]],
            "reviewed_window_starts": reviewed_starts,
            "review_basis": review["review_basis"],
            "identity_method": identity_method,
            "acceptance_geometry": acceptance_geometry,
            "supplemental_identity_references": supplemental_sources.get(str(review["member"]), []),
            "accepted_windows": len(persisted),
            "evaluated_windows": len(evaluated),
            "windows": persisted,
        })

    members = {}
    for member, windows in sorted(member_windows.items()):
        members[member] = {
            "sources": sum(row["member"] == member for row in source_results),
            "clips": len(windows),
            "minutes": round(len(windows) * WINDOW_SECONDS / 60.0, 3),
            "features": {
                channel: round(float(np.mean([row["features"][channel] for row in windows])), 8)
                for channel in FEATURE_COLUMNS
            },
            "scope": "verified long-form interview excerpts; descriptive delivery only",
        }

    reviewed_ids = {str(row["source_id"]) for row in spans}
    catalog = []
    for source_id, source in sorted(sources.items()):
        catalog.append({
            "source_id": source_id,
            "member": source["member"],
            "title": source["title"],
            "channel": source["channel"],
            "published": source["published"],
            "url": source["url"],
            "requested_url": source.get("requested_url"),
            "discovery": source["discovery"],
            "discovery_query": source.get("discovery_query"),
            "status": "VERIFIED_EXCERPTS" if source_id in reviewed_ids else "CATALOGUED_NOT_PROFILE_EVIDENCE",
        })
    artifact = {
        "schema": "member-interview-descriptive-v1",
        "status": "DISPLAY_ONLY_NO_ENGINE_WIRING",
        "generated_at": generated_at,
        "contract": {
            "titles_or_search_results_establish_identity": False,
            "in_band_name_required": True,
            "exact_window_seconds": WINDOW_SECONDS,
            "minimum_consecutive_windows": MIN_RUN_WINDOWS,
            "big_five_or_rank_changes_allowed": False,
            "stance_allocation_or_persuasion_changes_allowed": False,
            "delivery_description_only": True,
            "pre_vault_member_continuity_is_zero_far_claim": False,
            "supplemental_reference_score_floor": SUPPLEMENTAL_REFERENCE_SCORE_FLOOR,
            "supplemental_reference_margin_floor": SUPPLEMENTAL_REFERENCE_MARGIN,
        },
        "inputs": {
            "sources": {"path": str(args.sources.resolve()), "sha256": SMR.sha256_file(args.sources)},
            "asr": {"path": str(args.asr.resolve()), "sha256": SMR.sha256_file(args.asr)},
            "reviewed_spans": {"path": str(args.spans.resolve()), "sha256": SMR.sha256_file(args.spans)},
            "anchor_receipt": {"path": str(args.anchor_receipt.resolve()), "sha256": SMR.sha256_file(args.anchor_receipt)},
            "calibration": {"path": str(args.calibration.resolve()), "sha256": SMR.sha256_file(args.calibration)},
            "model_manifest": {"path": str(args.model_manifest.resolve()), "sha256": SMR.sha256_file(args.model_manifest)},
            "canonical_manifest": {"path": str(args.canonical.resolve()), "sha256": SMR.sha256_file(args.canonical)},
        },
        "catalog": catalog,
        "verified_sources": source_results,
        "members": members,
    }
    SMR.write_json_atomic(args.out, artifact)
    receipt = {
        "schema": "member-interview-descriptive-receipt-v1",
        "status": "PASS",
        "generated_at": generated_at,
        "artifact": str(args.out.resolve()),
        "artifact_sha256": SMR.sha256_file(args.out),
        "counts": {
            "catalogued_sources": len(catalog),
            "verified_sources": len(source_results),
            "members": len(members),
            "accepted_exact_windows": sum(len(row["windows"]) for row in source_results),
            "user_supplied_sources": sum("user_supplied" in row["discovery"] for row in catalog),
            "google_companion_sources": sum("google_search" in row["discovery"] for row in catalog),
        },
        "contract": artifact["contract"],
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

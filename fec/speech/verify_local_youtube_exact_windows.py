"""Verify and optionally promote exact local YouTube target-speech windows.

Positive identity comes only from the earlier literal in-band naming receipt
plus the first continuous target response.  ECAPA is used only to reject a
candidate that conflicts with the seed voice or any existing vaulted speaker;
it never establishes a cold identity.  Every promoted row is an exact 8-second
mono/16 kHz/PCM16 WAV with its own hashes and atomic canonical append.
"""

from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import wave

import numpy as np

from fec.members.b5_audio_embed import cosine, get_backend
from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data/speech/local_youtube_turn_candidates_v1.jsonl"
DEFAULT_OUTPUT = ROOT / "data/speech/local_youtube_exact_window_verification_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/local_youtube_exact_window_verification_v1.receipt.json"
DEFAULT_CENSUS = ROOT / "data/speech/congress_audio_profile_census_full_v1.jsonl"
DEFAULT_ANCHOR_RECEIPT = ROOT / "data/speech/speaker_anchor_vault_all_verified_v3.receipt.json"
DEFAULT_MODEL = Path(r"C:\melange-data\models\ecapa-voxceleb")
DEFAULT_MODEL_MANIFEST = ROOT / "data/speech/ecapa_model_manifest_v2.json"
DEFAULT_CLIP_ROOT = Path(r"C:\melange-data\member_clips\cspan_youtube_verified_v1")
DEFAULT_CANONICAL = Path(r"C:\melange-data\member_clips\clips_manifest.jsonl")

WINDOW_SECONDS = 8.0
SAMPLE_RATE = 16_000
WINDOW_FRAMES = int(WINDOW_SECONDS * SAMPLE_RATE)
TURN_INSET_SECONDS = 2.0
LOCKED_OTHER_SPEAKER_FLOOR = 0.595722615
SEED_COHERENCE_FLOOR = 0.595722615
HALF_CONSISTENCY_REJECT_FLOOR = 0.35
MIN_VOICED_RATIO = 0.80
MAX_INTERNAL_SILENCE_SECONDS = 1.20
MIN_ASR_WORDS = 8


class VerificationRefusal(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_pcm16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getframerate() != SAMPLE_RATE
            or source.getsampwidth() != 2
        ):
            raise VerificationRefusal("source_wav_contract")
        payload = source.readframes(source.getnframes())
    return np.frombuffer(payload, dtype="<i2").copy()


def exact_payload(samples: np.ndarray, start_sec: float) -> bytes:
    start_frame = round(start_sec * SAMPLE_RATE)
    end_frame = start_frame + WINDOW_FRAMES
    if start_frame < 0 or end_frame > samples.size:
        raise VerificationRefusal("exact_window_out_of_bounds")
    payload = np.asarray(samples[start_frame:end_frame], dtype="<i2").tobytes()
    if len(payload) != WINDOW_FRAMES * 2:
        raise VerificationRefusal("exact_window_frame_contract")
    return payload


def voice_activity(samples: np.ndarray) -> tuple[float, float]:
    frame_width = int(0.02 * SAMPLE_RATE)
    frames = samples[: samples.size - samples.size % frame_width].reshape(-1, frame_width)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) / 32768.0
    positive = rms[rms > 0]
    adaptive = float(np.percentile(positive, 60) * 0.15) if positive.size else 0.0
    threshold = max(0.003, adaptive)
    voiced = rms >= threshold
    ratio = float(np.mean(voiced)) if voiced.size else 0.0
    longest = 0
    current = 0
    for value in voiced:
        if value:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return ratio, longest * 0.02


def overlapping_words(words: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    return [
        row for row in words
        if float(row["end_sec"]) > start and float(row["start_sec"]) < end
    ]


def candidate_starts(span: dict[str, Any]) -> list[float]:
    start = float(span["start_sec"]) + TURN_INSET_SECONDS
    stop = float(span["end_sec"]) - TURN_INSET_SECONDS
    values = []
    while start + WINDOW_SECONDS <= stop + 1e-9:
        values.append(round(start, 6))
        start += WINDOW_SECONDS
    return values


def hard_signal_is_proximate_to_first_exact_cut(
    evidence_end: float, first_span: dict[str, Any]
) -> bool:
    """Bind identity to the first safe cut, not a coarse ASR turn boundary.

    Word timestamps can overlap the raw turn boundary slightly.  Exact cuts
    already apply a two-second inset, so the naming evidence must finish before
    that inset cut begins.
    """
    starts = candidate_starts(first_span)
    if not starts:
        return False
    first_exact_start = starts[0]
    return evidence_end <= first_exact_start and first_exact_start - evidence_end <= 120


def first_seed_span_index(evidence_end: float, spans: list[dict[str, Any]]) -> int | None:
    """Choose the earliest transcript-supported reply wholly after the name cue."""
    for index, span in enumerate(spans):
        if float(span["start_sec"]) < evidence_end:
            continue
        if hard_signal_is_proximate_to_first_exact_cut(evidence_end, span):
            return index
    return None


def seed_span_indices(evidence_end: float, spans: list[dict[str, Any]]) -> list[int]:
    """Return every reply close enough to the literal in-band name cue.

    A brief greeting can contain no acceptable eight-second window.  A later
    answer may still be safely bound to the same introduction when its first
    exact cut remains within the locked two-minute proximity window.
    """
    return [
        index for index, span in enumerate(spans)
        if float(span["start_sec"]) >= evidence_end
        and hard_signal_is_proximate_to_first_exact_cut(evidence_end, span)
    ]


def residual_passes_required(census_row: dict[str, Any], maximum: int) -> int:
    basis = census_row.get("verified_audio_basis") or {}
    existing = max(
        int(basis.get("anchor_exact_windows") or 0),
        int(basis.get("nonfloor_exact_windows_total") or 0),
    )
    floor = int(census_row.get("profile_floor_exact_windows") or maximum)
    return max(0, floor - existing)


def parse_required_pass_overrides(values: list[str], maximum: int) -> dict[str, int]:
    output: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise VerificationRefusal("required_pass_override_format")
        member, raw_count = value.rsplit("=", 1)
        member = member.strip()
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise VerificationRefusal("required_pass_override_count") from exc
        if not member or not 1 <= count <= maximum or member in output:
            raise VerificationRefusal("required_pass_override_value")
        output[member] = count
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


CANONICAL_REBIND_FIELDS = (
    "member", "outlet", "sid", "start", "end", "frames", "samplerate",
    "sha256", "out_wav", "src_wav", "source_audio_sha256",
    "transcript_path", "transcript_sha256", "alignment_method", "event_id",
)


def append_canonical_atomic(
    path: Path, new_rows: list[dict[str, Any]]
) -> tuple[int, int]:
    """Append new rows and rebind byte-identical existing canonical cuts.

    A consolidated verification run is allowed to encounter rows written by an
    earlier batch only when every identity, timing, PCM, and lineage field still
    matches.  This permits a current receipt to supersede batch receipts without
    duplicating or silently changing canonical data.
    """
    # cut_spans.py, houselive_cut.py and import_wj_clips.py all guard this manifest with
    # "<manifest>.lock"; a private lock name excluded this writer from that mutual exclusion.
    lock = Path(str(path) + ".lock")
    try:
        descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise VerificationRefusal("canonical_manifest_writer_lock") from exc
    os.close(descriptor)
    try:
        existing = SMR.read_jsonl(path, required=True)
        by_key = {}
        for row in existing:
            key = (
                str(row.get("outlet") or ""), str(row.get("sid") or ""),
                round(float(row.get("start") or 0.0), 6), str(row.get("member") or ""),
            )
            if key in by_key:
                raise VerificationRefusal("canonical_manifest_duplicate_key")
            by_key[key] = row
        added = []
        rebound = 0
        for row in new_rows:
            key = (row["outlet"], row["sid"], round(float(row["start"]), 6), row["member"])
            prior = by_key.get(key)
            if prior is None:
                added.append(row)
                by_key[key] = row
                continue
            if any(prior.get(field) != row.get(field) for field in CANONICAL_REBIND_FIELDS):
                raise VerificationRefusal("existing_canonical_binding_mismatch")
            rebound += 1
        if added:
            SMR.write_jsonl_atomic(path, existing + added)
        return len(added), rebound
    finally:
        lock.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--anchor-receipt", type=Path, default=DEFAULT_ANCHOR_RECEIPT)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--model-manifest", type=Path, default=DEFAULT_MODEL_MANIFEST)
    parser.add_argument("--clip-root", type=Path, default=DEFAULT_CLIP_ROOT)
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--exclude-video", action="append", default=[])
    parser.add_argument("--max-passes-per-member", type=int, default=5)
    parser.add_argument(
        "--required-pass", action="append", default=[], metavar="MEMBER=COUNT",
        help="Revalidate/rebind an explicit number of passes instead of using the live census residual",
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    source_rows = SMR.read_jsonl(args.input, required=True)
    wanted = set(args.member)
    excluded = set(args.exclude_video)
    source_rows = [
        row for row in source_rows
        if row.get("status") == "HARD_SIGNAL_TURN_CANDIDATE"
        and (not wanted or row["member"] in wanted)
        and str(row["video_id"]) not in excluded
    ]
    census_by_member = {
        str(row["member"]): row for row in SMR.read_jsonl(args.census, required=True)
    }
    required_overrides = parse_required_pass_overrides(
        args.required_pass, args.max_passes_per_member
    )
    source_members = {str(row["member"]) for row in source_rows}
    if set(required_overrides) - source_members:
        raise VerificationRefusal("required_pass_override_member_missing")
    required_by_member = {
        str(row["member"]): required_overrides.get(
            str(row["member"]),
            residual_passes_required(
                census_by_member[str(row["member"])], args.max_passes_per_member
            ),
        )
        for row in source_rows
    }
    source_rows = [
        row for row in source_rows if required_by_member[str(row["member"])] > 0
    ]

    anchor_receipt = json.loads(args.anchor_receipt.read_text(encoding="utf-8-sig"))
    vectors_path = Path(str(anchor_receipt["combined_vectors"]))
    if SMR.sha256_file(vectors_path) != anchor_receipt.get("combined_vectors_sha256"):
        raise VerificationRefusal("anchor_competitor_vectors_hash_mismatch")
    with np.load(vectors_path, allow_pickle=False) as vault:
        vault_members = np.asarray(vault["members"]).astype(str)
        vault_vectors = np.asarray(vault["vectors"], dtype=np.float32)
    if vault_vectors.ndim != 2 or vault_vectors.shape[0] != vault_members.size:
        raise VerificationRefusal("anchor_competitor_vectors_contract")

    backend = get_backend(
        "ecapa", model_dir=str(args.model), model_manifest=str(args.model_manifest)
    )
    verification_rows: list[dict[str, Any]] = []
    canonical_rows: list[dict[str, Any]] = []
    passes_by_member: Counter[str] = Counter()

    for source in sorted(source_rows, key=lambda row: (row["member"], row["video_id"])):
        member = str(source["member"])
        wav = Path(str(source["source_wav"])).resolve()
        if SMR.sha256_file(wav) != source.get("source_audio_sha256"):
            raise VerificationRefusal("source_audio_hash_mismatch")
        words = SMR.read_jsonl(Path(str(source["transcript"])), required=True)
        if SMR.sha256_file(Path(str(source["transcript"]))) != source.get("transcript_sha256"):
            raise VerificationRefusal("transcript_hash_mismatch")
        samples = read_pcm16(wav)
        spans = sorted(source.get("target_spans") or [], key=lambda value: float(value["start_sec"]))
        if not spans:
            continue
        # A missing/None evidence timestamp used to collapse silently to 0.0, which makes
        # every span "proximate to the name cue" and voids the in-band-naming gate that is
        # this lane's ONLY positive identity evidence. Refuse instead.
        evidence_end_value = source.get("evidence_end_sec")
        if (
            isinstance(evidence_end_value, bool)
            or not isinstance(evidence_end_value, (int, float))
            or not math.isfinite(float(evidence_end_value))
        ):
            raise VerificationRefusal("hard_signal_evidence_end_missing")
        evidence_end = float(evidence_end_value)
        seed_spans = set(seed_span_indices(evidence_end, spans))
        if not seed_spans:
            raise VerificationRefusal("hard_signal_not_proximate_to_first_target_span")
        passes_required = required_by_member[member]

        seed_vector = None
        for span_index, span in enumerate(spans):
            for window_index, start in enumerate(candidate_starts(span)):
                if passes_by_member[member] >= passes_required:
                    break
                # The cold identity seed must come from the first purported
                # target span after the named introduction, never a later
                # acoustically selected cluster.
                if seed_vector is None and span_index not in seed_spans:
                    continue
                end = start + WINDOW_SECONDS
                window_words = overlapping_words(words, start, end)
                reasons = []
                if len(window_words) < MIN_ASR_WORDS:
                    reasons.append("too_few_asr_words")
                if any("?" in str(row.get("word") or "") for row in window_words):
                    reasons.append("question_mark_inside_window")
                payload = exact_payload(samples, start)
                window_i16 = np.frombuffer(payload, dtype="<i2").copy()
                voiced_ratio, max_silence = voice_activity(window_i16)
                if voiced_ratio < MIN_VOICED_RATIO:
                    reasons.append("voiced_ratio_below_floor")
                if max_silence > MAX_INTERNAL_SILENCE_SECONDS:
                    reasons.append("internal_silence_above_ceiling")

                vector = backend.embed(window_i16.astype(np.float32) / 32768.0, SAMPLE_RATE)
                first_half = backend.embed(
                    window_i16[: WINDOW_FRAMES // 2].astype(np.float32) / 32768.0,
                    SAMPLE_RATE,
                )
                second_half = backend.embed(
                    window_i16[WINDOW_FRAMES // 2 :].astype(np.float32) / 32768.0,
                    SAMPLE_RATE,
                )
                half_score = cosine(first_half, second_half)
                if half_score < HALF_CONSISTENCY_REJECT_FLOOR:
                    reasons.append("half_voice_inconsistent")

                scores = vault_vectors @ np.asarray(vector, dtype=np.float32)
                best_index = int(np.argmax(scores))
                best_other_score = float(scores[best_index])
                best_other_member = str(vault_members[best_index])
                if best_other_score >= LOCKED_OTHER_SPEAKER_FLOOR:
                    reasons.append("matches_existing_vaulted_speaker")

                seed_score = None if seed_vector is None else cosine(vector, seed_vector)
                if seed_vector is not None and seed_score < SEED_COHERENCE_FLOOR:
                    reasons.append("target_seed_voice_inconsistent")
                status = "PASS_EXACT_WINDOW" if not reasons else "REFUSED_EXACT_WINDOW"
                row = {
                    "schema": "local-youtube-exact-window-verification-v1",
                    "status": status,
                    "member": member,
                    "bioguide": source.get("bioguide"),
                    "chamber": source.get("chamber"),
                    "video_id": source["video_id"],
                    "url": source["url"],
                    "source_wav": str(wav),
                    "source_audio_sha256": source["source_audio_sha256"],
                    "transcript": source["transcript"],
                    "transcript_sha256": source["transcript_sha256"],
                    "identity_evidence": source.get("evidence"),
                    "identity_evidence_basis": (
                        "literal-in-band-approved-first-name-and-surname-before-first-target-response"
                    ),
                    "ecapa_role": "rejection-only-for-cold-identity",
                    "span_index": span_index,
                    "window_index": window_index,
                    "start_sec": round(start, 6),
                    "end_sec": round(end, 6),
                    "frames": WINDOW_FRAMES,
                    "pcm_sha256": sha256_bytes(payload),
                    "asr_words": len(window_words),
                    "asr_text": " ".join(str(item["word"]).strip() for item in window_words),
                    "voiced_ratio": round(voiced_ratio, 6),
                    "maximum_internal_silence_sec": round(max_silence, 6),
                    "half_consistency_score": round(half_score, 9),
                    "seed_coherence_score": None if seed_score is None else round(seed_score, 9),
                    "best_other_vaulted_member": best_other_member,
                    "best_other_vaulted_score": round(best_other_score, 9),
                    "refusal_reasons": reasons,
                    "final_identity_acceptance": status == "PASS_EXACT_WINDOW" and args.write,
                    "verified_at": utc_now(),
                }
                if status == "PASS_EXACT_WINDOW":
                    if seed_vector is None:
                        seed_vector = np.asarray(vector, dtype=np.float32)
                        row["cold_seed_basis"] = "first-span-named-introduction-turn"
                    passes_by_member[member] += 1
                    if args.write:
                        name = (
                            f"{slug(member)}__{source['video_id']}__{span_index:03d}_"
                            f"{window_index:03d}__{round(start * 1000):09d}.wav"
                        )
                        out = args.clip_root / name
                        if out.exists():
                            with wave.open(str(out), "rb") as check:
                                existing_payload = check.readframes(check.getnframes())
                            if sha256_bytes(existing_payload) != row["pcm_sha256"]:
                                raise VerificationRefusal("existing_exact_window_pcm_mismatch")
                        else:
                            write_wav_atomic(out, payload)
                        canonical_rows.append({
                            "bytes": out.stat().st_size,
                            "dur": WINDOW_SECONDS,
                            "end": round(end, 6),
                            "frames": WINDOW_FRAMES,
                            "match": 1.0,
                            "member": member,
                            "member_slug": slug(member),
                            "out_wav": str(out.resolve()),
                            "outlet": "CSPAN",
                            "rms": float(np.sqrt(np.mean(window_i16.astype(np.float64) ** 2)) / 32768.0),
                            "samplerate": SAMPLE_RATE,
                            "seg": span_index * 1000 + window_index,
                            "sha256": SMR.sha256_file(out),
                            "show": "C-SPAN interview",
                            "sid": str(source["video_id"]),
                            "src_wav": str(wav),
                            "start": round(start, 6),
                            "words": len(window_words),
                            "package": source["url"],
                            "source_audio_sha256": source["source_audio_sha256"],
                            "transcript_path": source["transcript"],
                            "transcript_sha256": source["transcript_sha256"],
                            "alignment_method": (
                                "in-band-approved-first+surname+asr-turn-interior+ecapa-rejection-only"
                            ),
                            "identity_evidence": source.get("evidence"),
                            "event_id": f"youtube:{source['video_id']}",
                        })
                        row["out_wav"] = str(out.resolve())
                        row["wav_sha256"] = SMR.sha256_file(out)
                verification_rows.append(row)
            if passes_by_member[member] >= passes_required:
                break

    # Promote only members whose new windows close the residual census gap.
    # Existing verified anchor/canonical windows remain part of the floor; this
    # lane does not force five redundant cuts from one newly found recording.
    eligible_members = {
        member for member, count in passes_by_member.items()
        if count >= required_by_member[member] > 0
    }
    canonical_rows = [row for row in canonical_rows if row["member"] in eligible_members]
    for row in verification_rows:
        if row["member"] not in eligible_members:
            row["final_identity_acceptance"] = False
    appended, rebound = (
        append_canonical_atomic(args.canonical, canonical_rows) if args.write else (0, 0)
    )
    SMR.write_jsonl_atomic(args.output, verification_rows)
    receipt = {
        "schema": "local-youtube-exact-window-verification-receipt-v1",
        "status": (
            "PASS" if not args.write or appended + rebound == len(canonical_rows)
            else "FAIL_APPEND"
        ),
        "generated_at": utc_now(),
        "mode": "write" if args.write else "dry-run",
        "contract": {
            "window_seconds": WINDOW_SECONDS,
            "window_frames": WINDOW_FRAMES,
            "turn_inset_seconds": TURN_INSET_SECONDS,
            "minimum_voiced_ratio": MIN_VOICED_RATIO,
            "maximum_internal_silence_seconds": MAX_INTERNAL_SILENCE_SECONDS,
            "half_consistency_reject_floor": HALF_CONSISTENCY_REJECT_FLOOR,
            "seed_coherence_floor": SEED_COHERENCE_FLOOR,
            "locked_other_speaker_floor": LOCKED_OTHER_SPEAKER_FLOOR,
            "ecapa_is_positive_cold_identity_authority": False,
            "in_band_identity_signal_required": True,
            "approved_first_name_alias_may_satisfy_first_name": True,
            "surname_still_required_with_approved_first_name_alias": True,
            "complete_profile_required_for_canonical_append": True,
            "existing_verified_windows_count_toward_profile_floor": True,
            "explicit_pass_override_is_revalidation_only": True,
        },
        "inputs": {
            "candidates": {"path": str(args.input.resolve()), "sha256": SMR.sha256_file(args.input)},
            "census": {"path": str(args.census.resolve()), "sha256": SMR.sha256_file(args.census)},
            "anchor_receipt": {"path": str(args.anchor_receipt.resolve()), "sha256": SMR.sha256_file(args.anchor_receipt)},
            "anchor_vectors": {"path": str(vectors_path.resolve()), "sha256": SMR.sha256_file(vectors_path)},
            "model_manifest": {"path": str(args.model_manifest.resolve()), "sha256": SMR.sha256_file(args.model_manifest)},
        },
        "coverage": {
            "source_candidates": len(source_rows),
            "verification_rows": len(verification_rows),
            "passed_exact_windows": sum(row["status"] == "PASS_EXACT_WINDOW" for row in verification_rows),
            "refused_exact_windows": sum(row["status"] == "REFUSED_EXACT_WINDOW" for row in verification_rows),
            "members_with_complete_profiles": len(eligible_members),
            "complete_profile_members": sorted(eligible_members),
            "residual_passes_required_by_member": dict(sorted(required_by_member.items())),
            "explicit_pass_overrides": dict(sorted(required_overrides.items())),
            "canonical_rows_planned": len(canonical_rows),
            "canonical_rows_appended": appended,
            "canonical_rows_rebound_existing": rebound,
            "excluded_video_ids": sorted(excluded),
        },
        "refusal_reasons": dict(sorted(Counter(
            reason for row in verification_rows for reason in row["refusal_reasons"]
        ).items())),
        "output": {"path": str(args.output.resolve()), "sha256": SMR.sha256_file(args.output)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps({"coverage": receipt["coverage"], "refusals": receipt["refusal_reasons"]}, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

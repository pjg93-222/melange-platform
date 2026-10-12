#!/usr/bin/env python3
"""Fill caption gaps with hash-bound local Whisper word timestamps.

This is a localization stage only. It does not establish that the named member
is the speaker. The output replaces ``LOCAL_ASR_REQUIRED`` rows in the shared
long-form transcript manifest and preserves caption or previously verified
transcripts already present there.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import types
import wave

from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACQUISITION = ROOT / "data/speech/member_longform_acquisition_v1.jsonl"
DEFAULT_TRANSCRIPTS = ROOT / "data/speech/member_longform_transcripts_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_transcripts_v1.receipt.json"
DEFAULT_MODEL = Path(r"C:\melange-data\models\faster-whisper-small")
DEFAULT_TRANSCRIPT_ROOT = Path(r"C:\melange-data\member_longform_transcripts_v1")
ASR_LANGUAGE_CONTRACT = "original-language-localization-v2"


def asr_request_settings(model_path: Path, language: str,
                         condition_on_previous_text: bool = True) -> dict:
    """Keep automatic language detection distinct from forced-language requests."""
    language = language.strip().lower()
    if language != "auto" and (not language.isalpha() or not 2 <= len(language) <= 3):
        raise ValueError("language must be 'auto' or a two/three-letter language code")
    return {"contract": ASR_LANGUAGE_CONTRACT, "model_path": str(model_path.resolve()),
            "requested_language": None if language == "auto" else language,
            "condition_on_previous_text": condition_on_previous_text,
            "task": "transcribe_original_language"}


def request_cache_key(settings: dict) -> str:
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def source_binding_mismatches(rows: list[dict], acquisitions: dict[str, dict]) -> list[str]:
    by_member = {str(row.get("member") or ""): row for row in rows}
    return sorted(member for member, source in acquisitions.items()
                  if not transcript_matches_acquisition(by_member.get(member, {}), source))


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def pcm16_audio(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getframerate(), source.getsampwidth()) != (1, 16_000, 2):
            raise RuntimeError(f"source WAV violates mono/16k/PCM16 contract: {path}")
        payload = source.readframes(source.getnframes())
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0


def wav_duration_seconds(path: Path) -> float:
    """Read duration from the normalized WAV header without decoding audio."""
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def transcript_matches_acquisition(row: dict, acquisition: dict | None) -> bool:
    if not acquisition:
        return False
    return (
        str(row.get("source_id") or "") == str(acquisition.get("source_id") or "")
        and str(row.get("wav_sha256") or "") == str(acquisition.get("wav_sha256") or "")
    )


def pending_rows(
    transcripts: list[dict], members: set[str], retry_errors: bool,
    acquisitions: dict[str, dict] | None = None, refresh: bool = False,
) -> list[dict]:
    if refresh and not members:
        raise ValueError("refresh requires explicit members")
    retryable = {"LOCAL_ASR_REQUIRED"}
    if retry_errors:
        retryable.add("TRANSCRIPT_ERROR")
    return [
        row for row in transcripts
        if (not members or row.get("member") in members) and (
            refresh or row.get("status") in retryable
            or (
                acquisitions is not None
                and not transcript_matches_acquisition(
                    row, acquisitions.get(str(row.get("member") or ""))
                )
            )
        )
    ]


def install_av_policy_shim() -> None:
    if "av" in sys.modules:
        return
    av_module = types.ModuleType("av")
    av_module.audio = types.ModuleType("av.audio")
    av_module.error = types.ModuleType("av.error")

    class InvalidDataError(Exception):
        pass

    av_module.error.InvalidDataError = InvalidDataError
    av_module.open = None
    sys.modules["av"] = av_module
    sys.modules["av.audio"] = av_module.audio
    sys.modules["av.error"] = av_module.error


def counts(rows: list[dict]) -> dict:
    return {
        "processed": len(rows),
        "caption_ready": sum(row.get("status") == "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED" for row in rows),
        "verified_asr_reused": sum(row.get("status") == "REUSED_VERIFIED_LOCAL_ASR" for row in rows),
        "local_asr_ready": sum(row.get("status") == "LOCAL_ASR_READY_IDENTITY_UNVERIFIED" for row in rows),
        "local_asr_required": sum(row.get("status") == "LOCAL_ASR_REQUIRED" for row in rows),
        "errors": sum(row.get("status") == "TRANSCRIPT_ERROR" for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition", type=Path, default=DEFAULT_ACQUISITION)
    parser.add_argument("--transcripts", type=Path, default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--transcript-root", type=Path, default=DEFAULT_TRANSCRIPT_ROOT)
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--cpu-threads", type=int, default=6)
    parser.add_argument("--bounded-feature-memory", action="store_true",
                        help="Batch FFT calculations, preserving full-recording ASR and global normalization")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--refresh", action="store_true",
                        help="Reprocess explicitly named members, preserving earlier transcript files")
    parser.add_argument("--no-condition-on-previous-text", dest="condition_on_previous_text",
                        action="store_false", default=True,
                        help="Disable previous-text conditioning for targeted repetition-loop repairs")
    parser.add_argument("--language", default="auto",
                        help="Original-language transcription: auto-detect by default; e.g. es for Spanish")
    parser.add_argument(
        "--shortest-first",
        action="store_true",
        help="Process the shortest pending recordings first to maximize incremental coverage.",
    )
    args = parser.parse_args()
    if args.refresh and not args.member:
        parser.error("--refresh requires at least one --member")
    settings = asr_request_settings(args.model, args.language, args.condition_on_previous_text)
    if args.bounded_feature_memory:
        from fec.speech.bounded_whisper_features import CONTRACT, install
        settings["feature_runtime"] = CONTRACT
    refresh_run = (dt.datetime.now(dt.timezone.utc).strftime("refresh-%Y%m%dT%H%M%S%fZ")
                   if args.refresh else None)

    acquisitions = {
        row["member"]: row for row in SMR.read_jsonl(args.acquisition, required=True)
        if row.get("status") in {"ACQUIRED_IDENTITY_VERIFIED", "ACQUIRED_IDENTITY_UNVERIFIED"}
    }
    rows = SMR.read_jsonl(args.transcripts, required=True)
    for row in rows:
        acquisition = acquisitions.get(str(row.get("member") or ""))
        if not transcript_matches_acquisition(row, acquisition):
            continue
        for key in (
            "recorded_at", "published", "source_era", "current_role_assumed_on"
        ):
            if acquisition.get(key) is not None:
                row[key] = acquisition.get(key)
    by_member = {row["member"]: row for row in rows}
    missing_requested = set(args.member) - set(by_member)
    if missing_requested:
        parser.error("requested members lack transcript-manifest rows: " + ", ".join(sorted(missing_requested)))
    pending = pending_rows(rows, set(args.member), args.retry_errors, acquisitions, refresh=args.refresh)
    if args.shortest_first:
        def duration_key(row: dict) -> tuple[float, str]:
            acquisition = acquisitions.get(str(row.get("member") or "")) or {}
            try:
                duration = wav_duration_seconds(Path(str(acquisition.get("wav") or "")))
            except (OSError, EOFError, wave.Error, ZeroDivisionError):
                duration = float("inf")
            return duration, str(row.get("member") or "")

        pending.sort(key=duration_key)
    if args.limit:
        pending = pending[:args.limit]

    install_av_policy_shim()
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(
        str(args.model), device="cpu", compute_type="int8", local_files_only=True,
        cpu_threads=max(1, args.cpu_threads), num_workers=1,
    )
    if args.bounded_feature_memory:
        install(model)
    for number, source in enumerate(pending, 1):
        member = source["member"]
        acquisition = acquisitions.get(member)
        try:
            if not acquisition:
                raise RuntimeError("matching acquired audio row is absent")
            wav = Path(str(acquisition.get("wav") or "")).resolve()
            if not wav.is_file() or SMR.sha256_file(wav) != acquisition.get("wav_sha256"):
                raise RuntimeError("acquired WAV is absent or hash-mismatched")
            source_id = str(acquisition["source_id"])
            # Language/configuration changes cannot reuse old forced-English ASR.
            # Earlier transcripts remain recoverable in their original directories.
            directory = args.transcript_root / source_id / request_cache_key(settings)
            if refresh_run:
                directory = directory / refresh_run
            request = {
                "wav": str(wav),
                "wav_sha256": acquisition["wav_sha256"],
                "media_url": acquisition.get("url"),
                "reference_url": acquisition.get("url"),
                "asr_transcript": str((directory / "transcript.words.jsonl").resolve()),
                "asr_transcript_receipt": str((directory / "transcript.receipt.json").resolve()),
                "model_path": str(args.model.resolve()),
                "language": settings["requested_language"],
                "condition_on_previous_text": settings["condition_on_previous_text"],
                "asr_request_settings": settings,
            }
            words, receipt = SMR.load_or_create_asr_transcript(request, model, pcm16_audio(wav))
            result = {
                "schema": "member-longform-transcript-v1",
                "status": "LOCAL_ASR_READY_IDENTITY_UNVERIFIED",
                "member": member,
                "source_id": source_id,
                "source_type": acquisition.get("source_type"),
                "title": acquisition.get("title"),
                "channel": acquisition.get("channel"),
                "url": acquisition.get("url"),
                "recorded_at": acquisition.get("recorded_at"),
                "published": acquisition.get("published"),
                "source_era": acquisition.get("source_era"),
                "current_role_assumed_on": acquisition.get("current_role_assumed_on"),
                "wav": str(wav),
                "wav_sha256": acquisition["wav_sha256"],
                "transcript": str(Path(str(receipt["transcript"])).resolve()),
                "transcript_sha256": receipt["transcript_sha256"],
                "word_count": len(words),
                "asr_request_settings": settings,
                "detected_language": receipt.get("language"),
                "speaker_identity_authority": False,
                "transcript_identity_authority": False,
                "generated_at": utc_now(),
            }
        except Exception as exc:
            result = {
                **source,
                "attempted_source_id": (acquisition or {}).get("source_id"),
                "attempted_wav_sha256": (acquisition or {}).get("wav_sha256"),
                "status": "TRANSCRIPT_ERROR",
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                "speaker_identity_authority": False,
                "transcript_identity_authority": False,
                "generated_at": utc_now(),
            }
        by_member[member] = result
        SMR.write_jsonl_atomic(
            args.transcripts, sorted(by_member.values(), key=lambda row: row["member"])
        )
        print(f"local-asr {number}/{len(pending)} {member}: {result['status']}", flush=True)

    final_rows = list(by_member.values())
    summary = counts(final_rows)
    acquired_count = len(acquisitions)
    binding_mismatches = source_binding_mismatches(final_rows, acquisitions)
    complete = (
        len(final_rows) == acquired_count and summary["local_asr_required"] == 0 and summary["errors"] == 0
        and not binding_mismatches
    )
    receipt = {
        "schema": "member-longform-transcript-receipt-v1",
        "status": "COMPLETE_LOCALIZATION_ONLY" if complete else "PARTIAL_VISIBLE",
        "generated_at": utc_now(),
        "counts": {"acquired_inputs": acquired_count, **summary,
                   "source_binding_mismatches": len(binding_mismatches)},
        "source_binding_mismatch_members": binding_mismatches,
        "contract": {
            "transcript_establishes_speaker_identity": False,
            "transcript_is_hash_bound_to_acquired_audio_row": True,
            "promotion_requires_in_band_name_full_turns_and_speaker_gate": True,
        },
        "inputs": {"acquisition_sha256": SMR.sha256_file(args.acquisition)},
        "output": {"path": str(args.transcripts.resolve()), "sha256": SMR.sha256_file(args.transcripts)},
        "model": str(args.model.resolve()),
        "asr_request_settings": settings,
        "refresh_run": refresh_run,
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

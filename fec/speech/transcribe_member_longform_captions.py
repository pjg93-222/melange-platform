#!/usr/bin/env python3
"""Materialize hash-bound YouTube caption transcripts for acquired interviews.

Captions are localization evidence, not speaker identity. Sources without
usable English captions remain explicitly queued for local Whisper ASR.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request

import yt_dlp

from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACQUISITION = ROOT / "data/speech/member_longform_acquisition_v1.jsonl"
DEFAULT_EXISTING_ASR = ROOT / "data/speech/member_interview_asr_20260903.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_longform_transcripts_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_transcripts_v1.receipt.json"
DEFAULT_TRANSCRIPT_ROOT = Path(r"C:\melange-data\member_longform_transcripts_v1")
TRANSCRIPT_ENDPOINT_BLOCKED = threading.Event()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    replace_with_retries(temporary, path)


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True,
                                    separators=(",", ":")) + "\n")
    replace_with_retries(temporary, path)


def replace_with_retries(temporary: Path, path: Path) -> None:
    for attempt in range(40):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.25)


def tokenized(value: str) -> list[str]:
    return re.findall(r"\S+", value.replace("\n", " ").strip())


def json3_words(payload: dict) -> list[dict]:
    """Expand captions for localization, preserving that word timing is estimated.

    Segment offsets are provider cues; dividing a segment among tokens does
    not align those tokens to the waveform or measure pauses between words.
    """
    words = []
    for event in payload.get("events") or []:
        event_start = float(event.get("tStartMs") or 0.0) / 1000.0
        event_end = event_start + float(event.get("dDurationMs") or 0.0) / 1000.0
        segments = [segment for segment in (event.get("segs") or []) if segment.get("utf8")]
        for index, segment in enumerate(segments):
            text = str(segment.get("utf8") or "").strip()
            tokens = tokenized(text)
            if not tokens:
                continue
            start = event_start + float(segment.get("tOffsetMs") or 0.0) / 1000.0
            if index + 1 < len(segments):
                end = event_start + float(segments[index + 1].get("tOffsetMs") or 0.0) / 1000.0
            else:
                end = event_end
            end = max(start + 0.02 * len(tokens), end)
            width = (end - start) / len(tokens)
            for token_index, token in enumerate(tokens):
                words.append({
                    "schema": "speech-source-asr-word-v1",
                    "index": len(words),
                    "word": token,
                    "start_sec": round(start + token_index * width, 3),
                    "end_sec": round(start + (token_index + 1) * width, 3),
                    "probability": None,
                    "timing_method": "caption_segment_linear_interpolation",
                    "timing_is_estimated": True,
                    "word_alignment_verified": False,
                })
    return words


def english_json3(info: dict) -> tuple[str, str, str] | None:
    for kind, mapping in (("manual", info.get("subtitles") or {}),
                          ("automatic", info.get("automatic_captions") or {})):
        # Prefer the original track.  The translated generic ``en`` endpoint
        # is more aggressively throttled and may 429 while ``en-orig`` works.
        for language in ("en-orig", "en-US", "en-GB", "en"):
            formats = mapping.get(language) or []
            selected = next((row for row in formats if row.get("ext") == "json3"), None)
            if selected and selected.get("url"):
                return kind, language, str(selected["url"])
    return None


def lineage(row: dict) -> dict:
    """Preserve source and leadership-era fields through localization."""
    return {
        "member": row.get("member"),
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
    }


def download_caption_words(url: str) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    payload = None
    # Provider blocks are recorded as retryable transcript errors. Do not turn
    # a roster-scale 429 into a per-member minute-long sleep cascade.
    retry_delays = (0,)
    for attempt, delay in enumerate(retry_delays):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == len(retry_delays) - 1:
                raise
    if payload is None:
        raise RuntimeError("caption download retry loop ended without a payload")
    words = json3_words(payload)
    if len(words) < 50:
        raise RuntimeError("caption transcript contains fewer than 50 words")
    return words


def transcript_api_words(video_id: str) -> tuple[str, list[dict]]:
    """Fetch YouTube captions without a full player metadata extraction."""
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore

    fetched = YouTubeTranscriptApi().fetch(
        video_id, languages=["en", "en-US", "en-GB", "en-orig"]
    )
    words = []
    for snippet in fetched:
        tokens = tokenized(str(snippet.text or ""))
        if not tokens:
            continue
        start = float(snippet.start)
        end = max(start + 0.02 * len(tokens), start + float(snippet.duration))
        width = (end - start) / len(tokens)
        for index, token in enumerate(tokens):
            words.append({
                "schema": "speech-source-asr-word-v1",
                "index": len(words),
                "word": token,
                "start_sec": round(start + index * width, 3),
                "end_sec": round(start + (index + 1) * width, 3),
                "probability": None,
                "timing_method": "caption_snippet_linear_interpolation",
                "timing_is_estimated": True,
                "word_alignment_verified": False,
            })
    if len(words) < 50:
        raise RuntimeError("caption transcript contains fewer than 50 words")
    kind = "automatic" if bool(getattr(fetched, "is_generated", False)) else "manual"
    return kind, words


def caption_definitively_absent(exc: Exception) -> bool:
    return type(exc).__name__ in {
        "AgeRestricted", "InvalidVideoId", "NoTranscriptFound",
        "TranscriptsDisabled", "VideoUnavailable", "VideoUnplayable",
    }


def ytdlp_caption_words(url: str) -> tuple[str, list[dict]] | None:
    options = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noprogress": True,
        # The transcript endpoint and Android player route are frequently IP
        # blocked while the visionOS player still exposes the signed json3
        # caption URL.  Pin that route to match the successful media extractor
        # fallback used during acquisition.
        "extractor_args": {"youtube": {"player_client": ["visionos"]}},
        "socket_timeout": 30, "retries": 2,
    }
    with yt_dlp.YoutubeDL(options) as client:
        info = client.extract_info(url, download=False)
    selected = english_json3(info)
    if not selected:
        return None
    caption_kind, language, _caption_url = selected
    # Direct requests to the signed caption URL can receive a 429 even when
    # yt-dlp's downloader succeeds with the extractor-provided headers.  Let
    # yt-dlp perform the caption transfer as well as metadata extraction.
    with tempfile.TemporaryDirectory(prefix="member-longform-caption-") as temporary:
        root = Path(temporary)
        download_options = {
            **options,
            "writeautomaticsub": caption_kind == "automatic",
            "writesubtitles": caption_kind == "manual",
            "subtitleslangs": [language],
            "subtitlesformat": "json3",
            "outtmpl": str(root / "%(id)s.%(ext)s"),
        }
        with yt_dlp.YoutubeDL(download_options) as client:
            client.download([url])
        payload_paths = sorted(root.glob("*.json3"))
        if not payload_paths:
            raise RuntimeError("yt-dlp reported captions but did not materialize json3")
        payload = json.loads(payload_paths[0].read_text(encoding="utf-8-sig"))
    words = json3_words(payload)
    if len(words) < 50:
        raise RuntimeError("caption transcript contains fewer than 50 words")
    return caption_kind, words


def process(row: dict, transcript_root: Path, existing_asr: dict[str, dict]) -> dict:
    source_id = str(row.get("source_id") or "")
    existing = existing_asr.get(source_id)
    if row["status"] == "ACQUIRED_IDENTITY_VERIFIED" and existing:
        transcript = Path(existing["transcript"])
        if SMR.sha256_file(transcript) != existing["transcript_sha256"]:
            raise RuntimeError("existing verified transcript hash mismatch")
        return {
            "schema": "member-longform-transcript-v1", "status": "REUSED_VERIFIED_LOCAL_ASR",
            **lineage(row),
            "transcript": str(transcript.resolve()), "transcript_sha256": existing["transcript_sha256"],
            "word_count": existing.get("word_count"), "speaker_identity_authority": True,
            "transcript_identity_authority": False, "generated_at": utc_now(),
        }
    if row.get("source_type") != "youtube":
        return {
            "schema": "member-longform-transcript-v1", "status": "LOCAL_ASR_REQUIRED",
            **lineage(row),
            "speaker_identity_authority": False, "transcript_identity_authority": False,
            "generated_at": utc_now(),
        }
    try:
        if TRANSCRIPT_ENDPOINT_BLOCKED.is_set():
            raise RuntimeError("transcript endpoint disabled after provider block")
        caption_kind, words = transcript_api_words(source_id)
    except Exception as exc:
        if type(exc).__name__ in {"IpBlocked", "RequestBlocked"}:
            TRANSCRIPT_ENDPOINT_BLOCKED.set()
        fallback = ytdlp_caption_words(str(row["url"]))
        if fallback is not None:
            caption_kind, words = fallback
        else:
            # Full player extraction succeeded and found no usable English
            # json3 caption, which is stronger absence evidence than a
            # transient transcript-endpoint error.
            return {
                "schema": "member-longform-transcript-v1", "status": "LOCAL_ASR_REQUIRED",
                **lineage(row),
                "speaker_identity_authority": False, "transcript_identity_authority": False,
                "generated_at": utc_now(),
            }
    if not words:
        return {
            "schema": "member-longform-transcript-v1", "status": "LOCAL_ASR_REQUIRED",
            **lineage(row),
            "speaker_identity_authority": False, "transcript_identity_authority": False,
            "generated_at": utc_now(),
        }
    directory = transcript_root / source_id
    transcript = directory / "transcript.words.jsonl"
    write_jsonl_atomic(transcript, words)
    return {
        "schema": "member-longform-transcript-v1", "status": "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED",
        **lineage(row),
        "transcript": str(transcript.resolve()), "transcript_sha256": SMR.sha256_file(transcript),
        "word_count": len(words), "caption_kind": caption_kind,
        "timing_methods": sorted({str(word.get("timing_method") or "unknown") for word in words}),
        "timing_is_estimated": True,
        "word_alignment_verified": False,
        "timing_scope": "localization only; not measured pauses or verified speaker boundaries",
        "speaker_identity_authority": False, "transcript_identity_authority": False,
        "generated_at": utc_now(),
    }


def transcript_needs_processing(acquisition: dict, existing: dict | None,
                                retry_errors: bool, retry_local_asr: bool) -> bool:
    if existing is None:
        return True
    if (existing.get("source_id") != acquisition.get("source_id") or
            existing.get("wav_sha256") != acquisition.get("wav_sha256")):
        return True
    if retry_errors and existing.get("status") == "TRANSCRIPT_ERROR":
        return True
    return bool(
        retry_local_asr
        and existing.get("status") == "LOCAL_ASR_REQUIRED"
        and existing.get("source_type") == "youtube"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition", type=Path, default=DEFAULT_ACQUISITION)
    parser.add_argument("--existing-asr", type=Path, default=DEFAULT_EXISTING_ASR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--transcript-root", type=Path, default=DEFAULT_TRANSCRIPT_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--request-delay", type=float, default=0.0,
        help="Minimum seconds between starting provider requests across workers.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--retry-local-asr", action="store_true")
    parser.add_argument("--member", action="append", default=[])
    args = parser.parse_args()

    acquired = [row for row in read_jsonl(args.acquisition)
                if row.get("status") in {"ACQUIRED_IDENTITY_VERIFIED", "ACQUIRED_IDENTITY_UNVERIFIED"}]
    existing_asr = {str(row["video_id"]): row for row in read_jsonl(args.existing_asr)}
    existing = {row["member"]: row for row in read_jsonl(args.out)} if (args.resume or args.member) else {}
    selected = acquired
    if args.member:
        selected = [row for row in selected if row["member"] in set(args.member)]
    if args.resume:
        selected = [
            row for row in selected
            if transcript_needs_processing(
                row, existing.get(row["member"]), args.retry_errors, args.retry_local_asr
            )
        ]
    lock = threading.Lock()
    rate_lock = threading.Lock()
    next_request_at = [0.0]

    def persist() -> None:
        write_jsonl_atomic(args.out, sorted(existing.values(), key=lambda row: row["member"]))

    def guarded_process(row: dict) -> dict:
        if args.request_delay > 0:
            with rate_lock:
                delay = next_request_at[0] - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                next_request_at[0] = time.monotonic() + args.request_delay
        return process(row, args.transcript_root, existing_asr)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(guarded_process, row): row for row in selected}
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "schema": "member-longform-transcript-v1", "status": "TRANSCRIPT_ERROR",
                    **lineage(source),
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    "speaker_identity_authority": False, "transcript_identity_authority": False,
                    "generated_at": utc_now(),
                }
            with lock:
                existing[source["member"]] = result
                if number % 10 == 0 or number == len(selected):
                    persist()
            print(f"transcript {number}/{len(selected)} {source['member']}: {result['status']}", flush=True)
    persist()
    rows = list(existing.values())
    counts = {
        "acquired_inputs": len(acquired), "processed": len(rows),
        "caption_ready": sum(row["status"] == "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED" for row in rows),
        "verified_asr_reused": sum(row["status"] == "REUSED_VERIFIED_LOCAL_ASR" for row in rows),
        "local_asr_ready": sum(row["status"] == "LOCAL_ASR_READY_IDENTITY_UNVERIFIED" for row in rows),
        "local_asr_required": sum(row["status"] == "LOCAL_ASR_REQUIRED" for row in rows),
        "errors": sum(row["status"] == "TRANSCRIPT_ERROR" for row in rows),
    }
    receipt = {
        "schema": "member-longform-transcript-receipt-v1", "status": "PARTIAL_VISIBLE",
        "generated_at": utc_now(), "counts": counts,
        "contract": {
            "caption_establishes_speaker_identity": False,
            "caption_word_times_establish_exact_boundaries": False,
            "caption_interpolation_measures_pauses": False,
            "transcript_is_hash_bound_to_acquired_audio_row": True,
            "promotion_requires_in_band_name_full_turns_and_speaker_gate": True,
        },
        "inputs": {"acquisition_sha256": SMR.sha256_file(args.acquisition)},
        "output": {"path": str(args.out.resolve()), "sha256": SMR.sha256_file(args.out)},
    }
    write_json_atomic(args.receipt, receipt)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

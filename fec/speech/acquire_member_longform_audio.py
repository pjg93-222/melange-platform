#!/usr/bin/env python3
"""Acquire and hash primary long-form interview candidates, resumably.

Acquisition proves only that a public recording was physically obtained. It
does not prove the named member is the speaker. ASR, in-band naming, complete
turn review, and speaker verification remain separate promotion stages.
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
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import wave

import yt_dlp

from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUEUE = ROOT / "data/speech/member_longform_acquisition_queue_v1.jsonl"
DEFAULT_VERIFIED_SOURCES = ROOT / "data/speech/member_interview_sources_20260903.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_longform_acquisition_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_acquisition_v1.receipt.json"
DEFAULT_MEDIA_ROOT = Path(r"C:\melange-data\member_longform_audio_v1")
SUCCESSFUL_ACQUISITION_STATUSES = {
    "ACQUIRED_IDENTITY_VERIFIED",
    "ACQUIRED_IDENTITY_UNVERIFIED",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json_atomic(path: Path, value) -> None:
    SMR.write_json_atomic(path, value)


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    SMR.write_jsonl_atomic(path, rows)


def safe_name(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value[:80] or "member"


def normalized_url(value: object) -> str | None:
    """Return a stable URL form for matching queue rows to acquisitions."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw
        scheme = parsed.scheme.casefold()
        hostname = (parsed.hostname or "").casefold()
        port = parsed.port
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            hostname = f"{hostname}:{port}"
        if parsed.username:
            credentials = urllib.parse.quote(parsed.username, safe="")
            if parsed.password:
                credentials += ":" + urllib.parse.quote(parsed.password, safe="")
            hostname = f"{credentials}@{hostname}"
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")
        query = urllib.parse.urlencode(
            sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)),
            doseq=True,
        )
        return urllib.parse.urlunsplit((scheme, hostname, path, query, ""))
    except (TypeError, ValueError):
        return raw


def candidate_source_key(candidate: dict) -> str:
    explicit = candidate.get("source_id") or candidate.get("video_id")
    if explicit:
        return str(explicit)
    return hashlib.sha256(
        str(candidate.get("audio_url") or candidate.get("url")).encode()
    ).hexdigest()[:12]


def acquisition_matches_candidate(existing_row: dict, candidate: dict) -> bool:
    """Match provenance identifiers or normalized public/media URLs."""
    existing_ids = {
        str(value)
        for value in (existing_row.get("source_id"), existing_row.get("extractor_id"))
        if value
    }
    candidate_ids = {
        str(value)
        for value in (
            candidate.get("source_id"),
            candidate.get("video_id"),
            candidate_source_key(candidate),
        )
        if value
    }
    if existing_ids & candidate_ids:
        return True
    existing_urls = {
        url
        for value in (existing_row.get("url"), existing_row.get("audio_url"))
        if (url := normalized_url(value))
    }
    candidate_urls = {
        url
        for value in (candidate.get("url"), candidate.get("audio_url"))
        if (url := normalized_url(value))
    }
    return bool(existing_urls & candidate_urls)


def acquisition_matches_queue(existing_row: dict, queue_row: dict) -> bool:
    """Allow fall-through sources unless a reviewed primary is authoritative."""
    primary = queue_row["primary"]
    candidates = [primary]
    if not primary.get("reviewed_replacement"):
        candidates.extend(queue_row.get("alternatives") or [])
    return any(acquisition_matches_candidate(existing_row, candidate) for candidate in candidates)


def wav_details(path: Path) -> tuple[float, int]:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getframerate(), source.getsampwidth()) != (1, 16_000, 2):
            raise RuntimeError("output WAV violates mono/16k/PCM16 contract")
        frames = source.getnframes()
    return round(frames / 16_000.0, 6), frames


def transcode(source: Path, target: Path) -> None:
    temporary = target.with_name(target.stem + ".part" + target.suffix)
    command = [
        SMR.ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-threads", "1", str(temporary),
    ]
    try:
        process = subprocess.run(command, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=3600)
    except subprocess.TimeoutExpired:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg conversion timed out after 3600 s")
    if process.returncode or not temporary.exists():
        temporary.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg conversion failed: " + process.stderr[-400:])
    os.replace(temporary, target)


def acquire_youtube(primary: dict, target: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="member-longform-") as temp_name:
        temp = Path(temp_name)
        options = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "noprogress": True,
            "format": "bestaudio/best", "outtmpl": str(temp / "source.%(ext)s"),
            "retries": 3, "fragment_retries": 3, "socket_timeout": 60,
        }
        with yt_dlp.YoutubeDL(options) as client:
            info = client.extract_info(primary["url"], download=True)
        downloads = [path for path in temp.glob("source.*") if path.is_file()]
        if len(downloads) != 1:
            raise RuntimeError(f"yt-dlp emitted {len(downloads)} files")
        target.parent.mkdir(parents=True, exist_ok=True)
        transcode(downloads[0], target)
    duration, frames = wav_details(target)
    return {
        "extractor_id": info.get("id"), "extractor_title": info.get("title"),
        "extractor_channel": info.get("channel") or info.get("uploader"),
        "extractor_duration_seconds": info.get("duration"),
        "measured_duration_seconds": duration, "frame_count": frames,
    }


def acquire_direct_audio(primary: dict, target: Path) -> dict:
    import urllib.request

    audio_url = primary.get("audio_url")
    if not audio_url:
        raise RuntimeError("direct audio route lacks audio_url")
    with tempfile.TemporaryDirectory(prefix="member-longform-") as temp_name:
        source = Path(temp_name) / "source"
        request = urllib.request.Request(audio_url, headers={"User-Agent": "MelangeSystems/1.0"})
        with urllib.request.urlopen(request, timeout=180) as response, source.open("wb") as handle:
            shutil.copyfileobj(response, handle, 1024 * 1024)
        if source.stat().st_size < 4096:
            raise RuntimeError("downloaded media is implausibly small")
        target.parent.mkdir(parents=True, exist_ok=True)
        transcode(source, target)
    duration, frames = wav_details(target)
    return {"measured_duration_seconds": duration, "frame_count": frames}


def acquire_one(row: dict, media_root: Path, verified_sources: dict[str, dict],
                prior_acquisition: dict | None = None) -> dict:
    if prior_acquisition and prior_acquisition.get("previous_acquisition"):
        prior_acquisition = prior_acquisition["previous_acquisition"]
    member = row["member"]
    primary = row["primary"]
    if row["acquisition_route"] == "already_verified":
        source = verified_sources.get(str(primary.get("source_id") or ""))
        if not source:
            raise RuntimeError("verified source is absent from source ledger")
        wav = Path(source["wav"])
        digest = SMR.sha256_file(wav)
        if digest != source["source_audio_sha256"]:
            raise RuntimeError("verified source WAV hash mismatch")
        duration, frames = wav_details(wav)
        return {
            "schema": "member-longform-acquisition-v1", "status": "ACQUIRED_IDENTITY_VERIFIED",
            "member": member, "source_type": "verified_longform", "source_id": primary.get("source_id"),
            "url": primary.get("url"), "title": primary.get("title"), "channel": primary.get("channel"),
            "recorded_at": primary.get("recorded_at"), "published": primary.get("published"),
            "source_era": primary.get("source_era"),
            "current_role_assumed_on": primary.get("current_role_assumed_on"),
            "wav": str(wav.resolve()), "wav_sha256": digest,
            "measured_duration_seconds": duration, "frame_count": frames,
            "speaker_identity_authority": True, "acquired_at": utc_now(),
        }
    if row["acquisition_route"] == "provider_page_media_resolution":
        return {
            "schema": "member-longform-acquisition-v1", "status": "PROVIDER_MEDIA_RESOLUTION_REQUIRED",
            "member": member, "source_type": primary["source_type"], "url": primary.get("url"),
            "title": primary.get("title"), "channel": primary.get("channel"),
            "recorded_at": primary.get("recorded_at"), "published": primary.get("published"),
            "source_era": primary.get("source_era"),
            "current_role_assumed_on": primary.get("current_role_assumed_on"),
            "speaker_identity_authority": False, "acquired_at": utc_now(),
        }
    candidates = []
    seen = set()
    for candidate in [primary, *(row.get("alternatives") or [])]:
        if primary.get("reviewed_replacement") and not candidate.get("reviewed_replacement"):
            continue
        key = (candidate.get("source_type"), candidate.get("video_id"),
               candidate.get("audio_url"), candidate.get("url"))
        if key in seen:
            continue
        seen.add(key)
        if candidate.get("source_type") in {"youtube", "apple_directory_audio"}:
            candidates.append(candidate)
    errors = []
    for candidate in candidates:
        source_key = candidate_source_key(candidate)
        target = media_root / f"{safe_name(member)}__{safe_name(str(source_key))}.wav"
        try:
            if target.is_file():
                if (prior_acquisition and prior_acquisition.get("wav")
                        and Path(prior_acquisition["wav"]).resolve() == target.resolve()
                        and prior_acquisition.get("wav_sha256")
                        and SMR.sha256_file(target) != prior_acquisition["wav_sha256"]):
                    raise RuntimeError("cached WAV differs from its recorded hash; original retained")
                duration, frames = wav_details(target)
                details = {
                    "measured_duration_seconds": duration,
                    "frame_count": frames,
                    "reused_existing_normalized_wav": True,
                }
            elif candidate["source_type"] == "youtube":
                details = acquire_youtube(candidate, target)
            else:
                details = acquire_direct_audio(candidate, target)
        except Exception as exc:
            errors.append(f"{candidate['source_type']}:{source_key}:{type(exc).__name__}:{str(exc)[:160]}")
            # A failed header/hash check must never delete a prior recording.
            # Keep the file available for inspection and report the failed source.
            continue
        return {
            "schema": "member-longform-acquisition-v1", "status": "ACQUIRED_IDENTITY_UNVERIFIED",
            "member": member, "source_type": candidate["source_type"], "source_id": source_key,
            "candidate_rank": candidate.get("candidate_rank"),
            "url": candidate.get("url"), "audio_url": candidate.get("audio_url"),
            "title": candidate.get("title"), "channel": candidate.get("channel"),
            "recorded_at": candidate.get("recorded_at"), "published": candidate.get("published"),
            "source_era": candidate.get("source_era"),
            "current_role_assumed_on": candidate.get("current_role_assumed_on"),
            "wav": str(target.resolve()), "wav_sha256": SMR.sha256_file(target),
            "speaker_identity_authority": False, "acquired_at": utc_now(), **details,
        }
    raise RuntimeError("all downloadable alternatives failed: " + " | ".join(errors))


def retry_existing(existing_row: dict, *, retry_errors: bool,
                   retry_provider_resolution: bool) -> bool:
    status = existing_row.get("status")
    return (
        (retry_errors and status == "ACQUISITION_ERROR") or
        (retry_provider_resolution and status == "PROVIDER_MEDIA_RESOLUTION_REQUIRED")
    )


def acquisition_media_problem(row: dict, cache: dict | None = None) -> str | None:
    """Verify physical reuse; memoize unchanged files within this one process."""
    if not row.get("wav") or not row.get("wav_sha256"):
        return "acquisition lacks WAV path or hash"
    try:
        path = Path(row["wav"]).resolve()
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns, row["wav_sha256"])
        if cache is not None and key in cache:
            return cache[key]
        wav_details(path)
        problem = None if SMR.sha256_file(path) == row["wav_sha256"] else "WAV hash mismatch"
        if cache is not None:
            cache[key] = problem
        return problem
    except (OSError, EOFError, wave.Error, RuntimeError, ValueError) as exc:
        return f"WAV unavailable or invalid: {type(exc).__name__}: {exc}"


def should_acquire(queue_row: dict, existing_row: dict | None, *,
                   retry_errors: bool, retry_provider_resolution: bool,
                   verify_media: bool = False, media_cache: dict | None = None) -> bool:
    if existing_row is None:
        return True
    if retry_existing(
        existing_row,
        retry_errors=retry_errors,
        retry_provider_resolution=retry_provider_resolution,
    ):
        return True
    if existing_row.get("status") in SUCCESSFUL_ACQUISITION_STATUSES:
        return (not acquisition_matches_queue(existing_row, queue_row)
                or (verify_media and acquisition_media_problem(existing_row, media_cache) is not None))
    return False


def reconcile_acquisitions(queue: list[dict], rows: list[dict], media_cache: dict | None = None) -> dict:
    """A roster-sized file alone cannot certify the current requested sources."""
    requested = {row["member"]: row for row in queue}
    acquired = {row["member"]: row for row in rows}
    mismatched = []
    invalid_media = {}
    for member, row in acquired.items():
        if row.get("status") not in SUCCESSFUL_ACQUISITION_STATUSES:
            continue
        if member not in requested or not acquisition_matches_queue(row, requested[member]):
            mismatched.append(member)
        problem = acquisition_media_problem(row, media_cache)
        if problem:
            invalid_media[member] = problem
    return {
        "missing_members": sorted(set(requested) - set(acquired)),
        "unexpected_members": sorted(set(acquired) - set(requested)),
        "source_mismatch_members": sorted(mismatched),
        "invalid_media": invalid_media,
        "duplicate_queue_members": len(queue) - len(requested),
        "duplicate_acquisition_members": len(rows) - len(acquired),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--verified-sources", type=Path, default=DEFAULT_VERIFIED_SOURCES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--member", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Reacquire explicitly selected members even when a prior acquisition succeeded.",
    )
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--retry-provider-resolution", action="store_true")
    args = parser.parse_args()
    if args.refresh and not args.member:
        parser.error("--refresh requires at least one --member")

    queue = read_jsonl(args.queue)
    queue_sha256 = SMR.sha256_file(args.queue)
    verified_sources = {str(row["video_id"]): row for row in read_jsonl(args.verified_sources)}
    existing = {row["member"]: row for row in read_jsonl(args.out)} if (args.resume or args.member or args.limit) else {}
    media_cache = {}
    selected = queue
    if args.member:
        selected = [row for row in selected if row["member"] in set(args.member)]
    if args.resume and not args.refresh:
        selected = [
            row for row in selected
            if should_acquire(
                row,
                existing.get(row["member"]),
                retry_errors=args.retry_errors,
                retry_provider_resolution=args.retry_provider_resolution,
                verify_media=True, media_cache=media_cache,
            )
        ]
    if args.limit:
        selected = selected[:args.limit]
    lock = threading.Lock()

    def persist() -> None:
        write_jsonl_atomic(args.out, sorted(existing.values(), key=lambda row: row["member"]))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(acquire_one, row, args.media_root, verified_sources,
                               existing.get(row["member"])): row for row in selected}
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                prior = existing.get(source["member"]) or {}
                prior = prior.get("previous_acquisition") or prior
                result = {
                    "schema": "member-longform-acquisition-v1", "status": "ACQUISITION_ERROR",
                    "member": source["member"], "source_type": source["primary"]["source_type"],
                    "url": source["primary"].get("url"), "title": source["primary"].get("title"),
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    "speaker_identity_authority": False, "acquired_at": utc_now(),
                }
                if prior.get("wav") and prior.get("wav_sha256"):
                    # Preserve the original binding across failed retries; otherwise
                    # a valid-header but altered WAV could be trusted on the next run.
                    result["previous_acquisition"] = prior
            with lock:
                existing[source["member"]] = result
                persist()
            print(f"acquired {number}/{len(selected)} {source['member']}: {result['status']}", flush=True)

    persist()
    rows = list(existing.values())
    reconciliation = reconcile_acquisitions(queue, rows, media_cache)
    current_queue_sha256 = SMR.sha256_file(args.queue)
    counts = {
        "queue_members": len(queue), "processed_members": len(rows),
        "acquired_identity_verified": sum(row["status"] == "ACQUIRED_IDENTITY_VERIFIED" for row in rows),
        "acquired_identity_unverified": sum(row["status"] == "ACQUIRED_IDENTITY_UNVERIFIED" for row in rows),
        "provider_resolution_required": sum(row["status"] == "PROVIDER_MEDIA_RESOLUTION_REQUIRED" for row in rows),
        "acquisition_errors": sum(row["status"] == "ACQUISITION_ERROR" for row in rows),
        "source_mismatches": len(reconciliation["source_mismatch_members"]),
        "invalid_media": len(reconciliation["invalid_media"]),
    }
    complete = (len(rows) == len(queue) and not counts["acquisition_errors"]
                and not counts["provider_resolution_required"]
                and not any(reconciliation.values()) and current_queue_sha256 == queue_sha256)
    receipt = {
        "schema": "member-longform-acquisition-receipt-v1",
        "status": "COMPLETE" if complete else "PARTIAL_VISIBLE",
        "generated_at": utc_now(), "counts": counts,
        "contract": {
            "acquisition_establishes_speaker_identity": False,
            "physical_audio_hash_required": True,
            "promotion_requires_asr_in_band_name_full_turns_and_speaker_gate": True,
        },
        "inputs": {"queue_sha256": queue_sha256},
        "reconciliation": {**reconciliation, "queue_unchanged_during_run": current_queue_sha256 == queue_sha256,
                           "current_queue_sha256": current_queue_sha256},
        "output": {"path": str(args.out.resolve()), "sha256": SMR.sha256_file(args.out)},
        "media_root": str(args.media_root.resolve()),
    }
    write_json_atomic(args.receipt, receipt)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

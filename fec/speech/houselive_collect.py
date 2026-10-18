# -*- coding: utf-8 -*-
r"""Resume HouseLive public-domain floor-audio collection from saved transcript probes.

The transcript sweep and candidate ranking already live under ``C:\melange-data\houselive``.
This collector only materializes the paired broadcast event, WebVTT, and mono 16 kHz WAV for
explicit dates.  It appends one hash-bearing manifest row only after all artifacts verify.

Example:
  python fec/speech/houselive_collect.py --dates 2025-12-09 2025-12-01
"""

from __future__ import annotations

import argparse
import codecs
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import wave


DEFAULT_ROOT = Path(r"C:\melange-data\houselive")
DEFAULT_DATES = (
    "2025-12-09",
    "2025-12-01",
    "2025-12-15",
    "2025-12-16",
    "2025-12-12",
    "2025-12-03",
    "2025-12-02",
    "2025-12-04",
    "2025-12-10",
    "2025-12-11",
    "2025-12-17",
    "2025-12-18",
)
UA = {"User-Agent": "MelangeSystemsResearchCollector/1.0 (public-records research)"}
EVENT_BASE = "https://liveproxy-azapp-prod-eastus2-003.azurewebsites.net/broadcastevents/"

_SYSTEM_GETADDRINFO = socket.getaddrinfo


def _ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
    rows = _SYSTEM_GETADDRINFO(host, port, family, type, proto, flags)
    return sorted(rows, key=lambda row: row[0] != socket.AF_INET)


socket.getaddrinfo = _ipv4_first


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", nargs="+", default=list(DEFAULT_DATES))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--ffmpeg-timeout-s", type=int, default=14400)
    parser.add_argument("--budget-s", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    for value in args.dates:
        dt.date.fromisoformat(value)
    if len(set(args.dates)) != len(args.dates):
        parser.error("--dates contains duplicates")
    return args


def fetch(url: str, *, binary: bool = False, tries: int = 3):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as response:
                data = response.read()
                status = response.status
            return (data if binary else data.decode("utf-8", "replace")), status
        except Exception as exc:  # transient network errors remain retryable
            last = exc
            if attempt + 1 < tries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError("fetch failed after %d tries: %s :: %s" % (tries, url, last))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(payload)
    os.replace(temp, path)


def read_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    return rows


def read_manifest(path: Path) -> dict[str, dict]:
    # main() rewrites manifest.jsonl from this dict, so a skipped line is permanently
    # DELETED (OneDrive partial sync). Refuse instead, like houselive_cut.read_jsonl.
    rows = {}
    if not path.exists():
        return rows
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("invalid JSON at %s:%d" % (path, number)) from exc
        if not isinstance(row, dict) or not row.get("date"):
            raise RuntimeError("manifest row without date at %s:%d" % (path, number))
        if row["date"] in rows:
            raise RuntimeError("duplicate manifest date at %s:%d" % (path, number))
        rows[row["date"]] = row
    return rows


def write_manifest_atomic(path: Path, rows: dict[str, dict]) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for date in sorted(rows):
            handle.write(json.dumps(rows[date], ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def acquire_run_lock(root: Path):
    """Hold a Windows byte-range lock for the process lifetime; the OS releases it on crash."""
    import msvcrt

    path = root / ".houselive_collect.lock"
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise RuntimeError("another HouseLive collector holds %s" % path)
    return handle


def ffmpeg_path(explicit: Path | None) -> Path:
    if explicit is not None:
        path = explicit
    else:
        try:
            import imageio_ffmpeg

            path = Path(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception as exc:
            raise RuntimeError("--ffmpeg is required (imageio_ffmpeg unavailable): %s" % exc)
    if not path.is_file():
        raise RuntimeError("ffmpeg not found: %s" % path)
    return path


def select_asset(event: dict, kind: str) -> dict:
    assets = (((event.get("asset") or {}).get("files")) or [])
    hits = [row for row in assets if row.get("type") == kind and "/east/" in (row.get("url") or "")]
    if not hits:
        hits = [row for row in assets if row.get("type") == kind]
    if not hits:
        raise RuntimeError("broadcast event has no %s asset" % kind)
    return hits[0]


def audio_playlist(master_url: str) -> tuple[str, str]:
    clean = master_url.split("#", 1)[0]
    text, _ = fetch(clean)
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line:
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                return urllib.parse.urljoin(clean, match.group(1)), "EXT-X-MEDIA:TYPE=AUDIO"
    return clean, "master-playlist-fallback"


def wav_facts(path: Path) -> tuple[int, int, int, float]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        frames = handle.getnframes()
    if channels != 1 or rate != 16000 or width != 2 or frames <= 0:
        raise RuntimeError(
            "unexpected WAV contract channels=%d rate=%d width=%d frames=%d"
            % (channels, rate, width, frames)
        )
    return channels, rate, width, frames / float(rate)


def summary_rows(root: Path) -> dict[str, dict]:
    path = root / "logs" / "probe_transcripts.jsonl"
    return {row.get("date"): row for row in read_jsonl(path) if row.get("date")}


def verified_existing(row: dict, root: Path, summary: dict | None) -> tuple[bool, str]:
    date = row.get("date")
    if not date or not row.get("ok"):
        return False, "missing date/ok"
    paths = {
        "broadcastevents_sha256": root / "broadcastevents" / (date + ".json"),
        "transcript_sha256": root / "transcripts" / (date + ".json"),
        "vtt_sha256": root / "vtt" / (date + ".vtt"),
        "wav_sha256": Path(row.get("wav_path") or ""),
    }
    try:
        for field, path in paths.items():
            if not path.is_file() or not row.get(field) or sha256(path) != row[field]:
                return False, "missing/hash mismatch: %s" % field
        wav_facts(paths["wav_sha256"])
        vtt_payload = paths["vtt_sha256"].read_bytes()
        if vtt_payload.startswith(codecs.BOM_UTF8):
            vtt_payload = vtt_payload[len(codecs.BOM_UTF8):]
        if not vtt_payload.lstrip().startswith(b"WEBVTT"):
            return False, "VTT header mismatch"
        if summary is not None and summary.get("sha256") != row.get("transcript_sha256"):
            return False, "probe/transcript hash mismatch"
    except Exception as exc:
        return False, str(exc)
    return True, "verified"


def collect_date(date: str, root: Path, ffmpeg: Path, summary: dict, timeout_s: int) -> dict:
    event_url = EVENT_BASE + date.replace("-", "")
    raw_event, event_status = fetch(event_url, binary=True)
    events = json.loads(raw_event.decode("utf-8", "replace"))
    if len(events) != 1:
        raise RuntimeError("expected one broadcast event, got %d" % len(events))
    event = events[0]
    hls = select_asset(event, "HLS")
    vtt = select_asset(event, "WebVTT")
    vtt_bytes, vtt_status = fetch(vtt["url"], binary=True)
    playlist_url, selection = audio_playlist(hls["url"])

    event_path = root / "broadcastevents" / (date + ".json")
    vtt_path = root / "vtt" / (date + ".vtt")
    wav_path = root / "audio" / (date + ".wav")
    write_atomic(event_path, raw_event)
    write_atomic(vtt_path, vtt_bytes)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    temp_wav = wav_path.with_name(wav_path.stem + ".part" + wav_path.suffix)

    started = time.monotonic()
    command = [
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", playlist_url, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(temp_wav),
    ]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        if temp_wav.exists():
            temp_wav.unlink()
        raise RuntimeError("ffmpeg exceeded timeout of %ds" % timeout_s)
    except KeyboardInterrupt:
        if temp_wav.exists():
            temp_wav.unlink()
        raise
    if run.returncode != 0:
        if temp_wav.exists():
            temp_wav.unlink()
        raise RuntimeError("ffmpeg exit %d: %s" % (run.returncode, run.stderr[-500:]))
    os.replace(temp_wav, wav_path)
    channels, rate, width, duration = wav_facts(wav_path)

    probe = summary or {}
    row = {
        "asset_id": (event.get("asset") or {}).get("id"),
        "audio_playlist_url": playlist_url,
        "audio_rendition_selection": selection,
        "broadcastevents_http": event_status,
        "broadcastevents_sha256": sha256(event_path),
        "broadcastevents_url": event_url,
        "channels": channels,
        "congress_num": ((event.get("superEvent") or {}).get("congressNum")),
        "date": date,
        "distinct_members": probe.get("distinct_members", []),
        "duration_hours": round(duration / 3600.0, 4),
        "duration_sec": round(duration, 3),
        "error": None,
        "event_end": event.get("endDate"),
        "event_id": event.get("_id"),
        "event_start": event.get("startDate"),
        "event_title": event.get("name"),
        "event_uri": event.get("id"),
        "ffmpeg_seconds": round(time.monotonic() - started, 1),
        "hls_asset_id": hls.get("id"),
        "hls_url": hls.get("url"),
        "n_distinct_members": len(probe.get("distinct_members", [])),
        "n_named_units": probe.get("named_units", 0),
        "n_resolved_units": probe.get("resolved_units", 0),
        "n_units": probe.get("units", 0),
        "ok": True,
        "rights": event.get("rights"),
        "sample_rate": rate,
        "sample_width_bytes": width,
        "session_num": ((event.get("superEvent") or {}).get("sessionNum")),
        "transcript_sha256": probe.get("sha256"),
        "vtt_asset_id": vtt.get("id"),
        "vtt_bytes": len(vtt_bytes),
        "vtt_http": vtt_status,
        "vtt_sha256": sha256(vtt_path),
        "vtt_url": vtt.get("url"),
        "wav_bytes": wav_path.stat().st_size,
        "wav_path": str(wav_path),
        "wav_sha256": sha256(wav_path),
    }
    return row


def main(argv=None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    run_lock = None if args.dry_run else acquire_run_lock(root)
    manifest_path = root / "manifest.jsonl"
    manifest_rows = read_manifest(manifest_path)
    summaries = summary_rows(root)
    metadata_changed = False
    for date, row in manifest_rows.items():
        probe = summaries.get(date)
        if probe is None or probe.get("sha256") != row.get("transcript_sha256"):
            continue
        members = probe.get("distinct_members", [])
        if row.get("distinct_members") != members or row.get("n_distinct_members") != len(members):
            row["distinct_members"] = members
            row["n_distinct_members"] = len(members)
            metadata_changed = True
    if metadata_changed and not args.dry_run:
        write_manifest_atomic(manifest_path, manifest_rows)

    existing = {}
    for date, row in manifest_rows.items():
        ok, reason = verified_existing(row, root, summaries.get(date))
        if ok:
            existing[date] = row
        else:
            print("RETRY %s existing receipt invalid: %s" % (date, reason), flush=True)
    pending = [date for date in args.dates if date not in existing]
    print("HOUSELIVE dates=%d complete=%d pending=%d" % (len(args.dates), len(args.dates) - len(pending), len(pending)))
    for date in args.dates:
        if date not in summaries:
            raise RuntimeError("no saved transcript probe summary for %s" % date)
    if args.dry_run:
        for date in pending:
            probe = summaries[date]
            print("DRY-RUN %s units=%s resolved=%s members=%s" % (
                date, probe.get("units"), probe.get("resolved_units"),
                len(probe.get("distinct_members", []))))
        return 0

    ffmpeg = ffmpeg_path(args.ffmpeg)
    started = time.monotonic()
    completed = 0
    for date in pending:
        if args.budget_s and time.monotonic() - started >= args.budget_s:
            print("BUDGET exhausted; %d date(s) remain" % (len(pending) - completed), flush=True)
            return 2
        try:
            timeout_s = args.ffmpeg_timeout_s
            if args.budget_s:
                timeout_s = max(1, min(timeout_s, int(args.budget_s - (time.monotonic() - started))))
            row = collect_date(date, root, ffmpeg, summaries[date], timeout_s)
            verified, reason = verified_existing(row, root, summaries[date])
            if not verified:
                raise RuntimeError("new receipt failed verification: %s" % reason)
        except Exception as exc:
            print("FAIL %s %s" % (date, str(exc)[:500]), file=sys.stderr, flush=True)
            return 2
        manifest_rows[date] = row
        write_manifest_atomic(manifest_path, manifest_rows)
        completed += 1
        print("OK %s hours=%.2f bytes=%d members=%d elapsed=%.1fs" % (
            date, row["duration_hours"], row["wav_bytes"], row["n_distinct_members"],
            row["ffmpeg_seconds"]), flush=True)
    print("HOUSELIVE complete: %d new day(s)" % completed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

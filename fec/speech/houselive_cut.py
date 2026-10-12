# -*- coding: utf-8 -*-
"""Cut conservatively speaker-bound HouseLive turns from verified daily WAV files.

Only official transcript labels beginning with a member honorific and resolving to exactly one
current House surname are accepted. Ambiguous or malformed labels are dropped, never guessed.
"""

from __future__ import annotations

import argparse
import atexit
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import unicodedata
import wave


DEFAULT_ROOT = Path(r"C:\melange-data\houselive")
DEFAULT_OUT = Path(r"C:\melange-data\member_clips\houselive")
HONORIFIC = re.compile(r"^(?:MR|MRS|MS|MISS|REP|REPRESENTATIVE|CONGRESSMAN|CONGRESSWOMAN)[.]?\s+(.+)$", re.I)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--roster", type=Path, default=Path("data/engine/congestion.json"))
    parser.add_argument("--dates", nargs="*")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def norm(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z]", "", value.lower())


def member_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")


def surname_index(roster_path: Path) -> dict[str, list[str]]:
    rows = json.loads(roster_path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for row in rows:
        if row.get("chamber") != "House":
            continue
        member = row.get("member") or ""
        parts = member.split()
        if not parts:
            continue
        out.setdefault(norm(parts[-1]), []).append(member)
    return out


def resolve_label(label: str, surnames: dict[str, list[str]]) -> str | None:
    match = HONORIFIC.match((label or "").strip())
    if not match:
        return None
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'’-]+", match.group(1))
    if not words:
        return None
    candidates = surnames.get(norm(words[-1]), [])
    return candidates[0] if len(candidates) == 1 else None


def read_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except (ValueError, TypeError) as exc:
            # main() rewrites clips_manifest.jsonl from this list, so a silently skipped
            # row is permanently DELETED from the canonical manifest. Refuse instead.
            raise RuntimeError("invalid JSON at %s:%d" % (path, number)) from exc
    return rows


def acquire_manifest_lock(manifest_path: Path) -> Path:
    lock_path = Path(str(manifest_path) + ".lock")
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("another cutter or stale manifest lock exists: %s" % lock_path) from exc
    os.write(descriptor, ("pid=%d\n" % os.getpid()).encode("ascii"))
    os.close(descriptor)
    atexit.register(lambda: lock_path.unlink(missing_ok=True))
    return lock_path


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    temporary = Path(str(path) + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rms16(payload: bytes) -> float:
    samples = array("h")
    samples.frombytes(payload)
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0


def source_contract(path: Path):
    handle = wave.open(str(path), "rb")
    if handle.getnchannels() != 1 or handle.getframerate() != 16000 or handle.getsampwidth() != 2:
        handle.close()
        raise RuntimeError("source WAV violates mono/16k/16-bit contract: %s" % path)
    return handle


def eligible_spans(rows: list[dict], surnames: dict[str, list[str]], duration: float):
    spans = []
    for ordinal, row in enumerate(rows):
        member = resolve_label(row.get("speaker") or "", surnames)
        text = re.sub(r"\s+", " ", row.get("text") or "").strip()
        if member is None or len(text.split()) < 8:
            continue
        try:
            start = float(row["offsettime"])
        except (KeyError, TypeError, ValueError):
            continue
        next_start = duration
        for later in rows[ordinal + 1:]:
            try:
                next_start = float(later["offsettime"])
                break
            except (KeyError, TypeError, ValueError):
                continue
        start = max(0.0, start - 0.10)
        end = min(duration, next_start + 0.10)
        if not (0.5 <= end - start <= 600.0):
            continue
        spans.append((ordinal, member, row.get("speaker") or "", text, start, end))
    return spans


def main(argv=None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    out = args.out.resolve()
    manifest_path = out.parent / "clips_manifest.jsonl"
    rejection_path = out.parent / "clips_rejections.jsonl"
    lock_path = acquire_manifest_lock(manifest_path)
    house_manifest = {row.get("date"): row for row in read_jsonl(root / "manifest.jsonl") if row.get("ok")}
    dates = args.dates or sorted(house_manifest)
    existing = {row.get("out_wav") for row in read_jsonl(manifest_path)}
    current_rejections = read_jsonl(rejection_path)
    known_rejections = {
        (row.get("outlet"), row.get("sid"), row.get("seg"), row.get("member"), row.get("reason"))
        for row in current_rejections
    }
    surnames = surname_index(args.roster.resolve())
    planned = written = skipped = rejected_existing = zero = 0
    planned_members = set()
    output_rows = []
    rejection_rows = []

    for date in dates:
        source_row = house_manifest.get(date)
        if source_row is None:
            raise RuntimeError("date has no verified HouseLive manifest row: %s" % date)
        src = Path(source_row["wav_path"])
        transcript = root / "transcripts" / (date + ".json")
        rows = json.loads(transcript.read_text(encoding="utf-8"))
        with source_contract(src) as source:
            rate = source.getframerate()
            duration = source.getnframes() / float(rate)
            spans = eligible_spans(rows, surnames, duration)
            print("DATE %s eligible=%d" % (date, len(spans)), flush=True)
            for ordinal, member, label, text, start, end in spans:
                planned += 1
                planned_members.add(member)
                rejection_signature = ("HouseLive", date, ordinal, member, "zero_rms")
                if rejection_signature in known_rejections:
                    rejected_existing += 1
                    continue
                slug = member_slug(member)
                name = "%s__%s__%04d__%d_%d.wav" % (
                    slug, date, ordinal, round(start * 1000), round(end * 1000))
                dest = out / slug / name
                if str(dest) in existing:
                    skipped += 1
                    continue
                if args.dry_run:
                    continue
                first = round(start * rate)
                frames = max(1, round((end - start) * rate))
                source.setpos(first)
                payload = source.readframes(frames)
                level = rms16(payload)
                if level == 0.0:
                    zero += 1
                    rejection_rows.append({
                        "outlet": "HouseLive",
                        "date": date,
                        "sid": date,
                        "seg": ordinal,
                        "member": member,
                        "speaker_label": label,
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "reason": "zero_rms",
                        "src_wav": str(src),
                    })
                    known_rejections.add(rejection_signature)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                temp = dest.with_name(dest.stem + ".part" + dest.suffix)
                with wave.open(str(temp), "wb") as clip:
                    clip.setnchannels(1)
                    clip.setsampwidth(2)
                    clip.setframerate(rate)
                    clip.writeframes(payload)
                os.replace(temp, dest)
                text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                package = source_row.get("event_uri")
                artifact_id = hashlib.sha256(
                    ("houselive\0%s\0%s" % (date, package)).encode("utf-8")).hexdigest()
                turn_id = hashlib.sha256(
                    ("%s\0%d\0%s\0%s" % (artifact_id, ordinal, member, text_digest)).encode("utf-8")
                ).hexdigest()
                output_rows.append({
                    "member": member,
                    "member_slug": slug,
                    "outlet": "HouseLive",
                    "show": "U.S. House Floor",
                    "sid": date,
                    "seg": ordinal,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "dur": round(end - start, 3),
                    "match": 1.0,
                    "alignment_method": "official-speaker-label+official-offsettime",
                    "speaker_label": label,
                    "artifact_id": artifact_id,
                    "turn_id": turn_id,
                    "original_ordinal": ordinal,
                    "words": len(text.split()),
                    "date": date,
                    "package": package,
                    "transcript_path": str(transcript),
                    "text_sha256": text_digest,
                    "src_wav": str(src),
                    "source_audio_sha256": source_row.get("wav_sha256"),
                    "out_wav": str(dest),
                    "bytes": dest.stat().st_size,
                    "samplerate": rate,
                    "frames": len(payload) // 2,
                    "rms": level,
                    "sha256": sha256(dest),
                })
                existing.add(str(dest))
                written += 1

    if not args.dry_run and output_rows:
        current = read_jsonl(manifest_path)
        current_paths = {row.get("out_wav") for row in current}
        merged = current + [row for row in output_rows if row.get("out_wav") not in current_paths]
        write_jsonl_atomic(manifest_path, merged)
    if not args.dry_run and rejection_rows:
        signatures = {
            (row.get("outlet"), row.get("sid"), row.get("seg"), row.get("member"), row.get("reason"))
            for row in current_rejections
        }
        merged_rejections = current_rejections + [
            row for row in rejection_rows
            if (row.get("outlet"), row.get("sid"), row.get("seg"), row.get("member"), row.get("reason"))
            not in signatures
        ]
        write_jsonl_atomic(rejection_path, merged_rejections)
    print("HOUSELIVE CLIPS mode=%s planned=%d written=%d existing=%d rejected_existing=%d "
          "zero_rms=%d members=%d" % (
              "DRY-RUN" if args.dry_run else "WRITE", planned, written, skipped,
              rejected_existing, zero, len(planned_members)), flush=True)
    lock_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Plan minimal canonical-anchor additions for speech-profile recovery targets."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fec.paths import ROOT as ROOT_VALUE


ROOT = Path(ROOT_VALUE)
DEFAULT_GAP = ROOT / "data" / "speech" / "member_speech_profile_gap_v1.jsonl"
DEFAULT_ANCHORS = ROOT / "data" / "speech" / "speaker_anchor_vault_v1.jsonl"
DEFAULT_CANONICAL = Path(r"C:\melange-data\member_clips\clips_manifest.jsonl")
DEFAULT_OUTPUT = ROOT / "data" / "speech" / "speaker_anchor_expansion_plan_v1.jsonl"
CANONICAL_ROOT = Path(r"C:\melange-data\member_clips")
MIN_CALIBRATION_SECONDS = 24.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


def wav_facts(path: Path) -> tuple[float, int]:
    try:
        with wave.open(str(path), "rb") as handle:
            if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (1, 2, 16000):
                raise ValueError("not mono 16-kHz PCM16")
            frames = handle.getnframes()
    except (OSError, wave.Error, ValueError) as exc:
        raise RuntimeError(f"invalid canonical anchor {path}: {exc}") from exc
    return frames / 16000.0, frames


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def build_plan(
    gaps: list[dict[str, Any]], anchors: list[dict[str, Any]],
    canonical: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    targets = {
        row["member"] for row in gaps if row.get("anchor_recovery_required") is True
    }
    existing: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in anchors:
        if row.get("member") in targets:
            existing[row["member"]].append(row)
    candidates: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in canonical:
        member = str(row.get("member") or "")
        path = Path(str(row.get("out_wav") or ""))
        event_id = str(row.get("sid") or "")
        duration = float(row.get("dur") or 0.0)
        if (
            member in targets and event_id
            and duration >= MIN_CALIBRATION_SECONDS and path.is_file()
        ):
            candidates[member].append({
                "event_id": event_id,
                "path": path,
                "duration_sec": duration,
                "canonical_audio_sha256": str(row.get("sha256") or ""),
                "canonical_match": float(row.get("match") or 0.0),
                "outlet": str(row.get("outlet") or ""),
                "show": str(row.get("show") or ""),
            })

    planned: list[dict[str, Any]] = []
    for member in sorted(targets):
        current = existing.get(member, [])
        current_events = {str(row.get("event_id") or "") for row in current}
        current_seconds = sum(float(row.get("duration_sec") or 0.0) for row in current)
        longest_by_event: dict[str, dict[str, Any]] = {}
        for candidate in candidates.get(member, []):
            prior = longest_by_event.get(candidate["event_id"])
            if prior is None or candidate["duration_sec"] > prior["duration_sec"]:
                longest_by_event[candidate["event_id"]] = candidate
        pool = sorted(
            longest_by_event.values(),
            key=lambda row: (-row["duration_sec"], row["event_id"], str(row["path"])),
        )
        union_events = current_events | {row["event_id"] for row in pool}
        upper_eligible = (
            len(current) + len(pool) >= 3
            and len(union_events) >= 3
            and current_seconds + sum(row["duration_sec"] for row in pool) >= 180.0
        )
        selected: list[dict[str, Any]] = []
        events = set(current_events)
        seconds = current_seconds
        count = len(current)
        remaining = list(pool)
        if upper_eligible:
            while count < 3 or len(events) < 3 or seconds < 180.0:
                need_event = len(events) < 3
                choices = [row for row in remaining if row["event_id"] not in events] if need_event else remaining
                if not choices:
                    raise RuntimeError(f"anchor planner stalled for {member}")
                choice = max(choices, key=lambda row: (row["duration_sec"], row["event_id"]))
                remaining.remove(choice)
                path = choice["path"]
                if not _within(path, CANONICAL_ROOT):
                    raise RuntimeError(f"canonical anchor escapes root: {path}")
                duration, frames = wav_facts(path)
                digest = sha256_file(path)
                stored_digest = choice["canonical_audio_sha256"]
                if stored_digest and stored_digest != digest:
                    raise RuntimeError(f"canonical anchor hash changed: {path}")
                selected.append({
                    "event_id": "canonical:" + hashlib.sha256(
                        (member + "|" + choice["event_id"]).encode("utf-8")
                    ).hexdigest()[:24],
                    "source_event_id": choice["event_id"],
                    "path": str(path.resolve()),
                    "duration_sec": round(duration, 6),
                    "frames": frames,
                    "audio_sha256": digest,
                    "canonical_match": choice["canonical_match"],
                    "outlet": choice["outlet"],
                    "show": choice["show"],
                })
                events.add(choice["event_id"])
                seconds += duration
                count += 1
        row = {
            "schema": "speech-speaker-anchor-expansion-plan-v1",
            "member": member,
            "status": (
                "CAN_REACH_ELIGIBLE_FROM_CANONICAL"
                if upper_eligible else "ADDITIONAL_VERIFIED_REFERENCE_REQUIRED"
            ),
            "current": {
                "anchors": len(current), "events": len(current_events),
                "duration_sec": round(current_seconds, 6),
            },
            "projected": {
                "anchors": len(current) + len(selected),
                "events": len(events),
                "duration_sec": round(seconds, 6),
                "target_eligible": upper_eligible,
            },
            "selected_canonical_anchors": selected,
        }
        row["row_sha256"] = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        planned.append(row)
    return planned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap", type=Path, default=DEFAULT_GAP)
    parser.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows = build_plan(read_jsonl(args.gap), read_jsonl(args.anchors), read_jsonl(args.canonical))
    write_atomic(args.output, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    receipt = {
        "schema": "speech-speaker-anchor-expansion-plan-receipt-v1",
        "status": "PASS",
        "gap_sha256": sha256_file(args.gap),
        "anchor_manifest_sha256": sha256_file(args.anchors),
        "canonical_manifest_sha256": sha256_file(args.canonical),
        "manifest": str(args.output.resolve()),
        "manifest_sha256": sha256_file(args.output),
        "members": len(rows),
        "canonical_ready": sum(row["status"] == "CAN_REACH_ELIGIBLE_FROM_CANONICAL" for row in rows),
        "additional_reference_required": sum(row["status"] == "ADDITIONAL_VERIFIED_REFERENCE_REQUIRED" for row in rows),
        "selected_canonical_anchors": sum(len(row["selected_canonical_anchors"]) for row in rows),
        "identity_floor": {"anchors": 3, "events": 3, "duration_sec": 180.0},
        "minimum_anchor_duration_sec": MIN_CALIBRATION_SECONDS,
    }
    receipt_path = args.output.with_suffix(".receipt.json")
    write_atomic(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

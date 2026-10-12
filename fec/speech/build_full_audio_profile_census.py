#!/usr/bin/env python3
"""Build a roster-complete census of verified and candidate congressional audio holdings.

This census intentionally keeps two claims separate:

* ``verified_audio_profile`` accepts exact windows from a verified speaker-anchor recording or
  from an accepted canonical member clip in any setting.
* ``strict_identity_prerequisite_ready`` is recomputed from the physical recordings.  It requires
  three distinct verified events and 180 seconds; persisted ``target_eligible`` booleans are not
  trusted because the v3 receipt contains rows that contradict its own stated threshold.
* ``verified_nonfloor_descriptive_profile`` accepts only active non-House-floor canonical windows
  plus recovered windows that survived the published locked-v2 identity gate.

Raw YouTube title matches are inventoried but never promoted by this producer.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WINDOW_FRAMES = 128_000
PROFILE_FLOOR = 5
MIN_IDENTITY_EVENTS = 3
MIN_IDENTITY_SECONDS = 180.0


class CensusError(RuntimeError):
    pass


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CensusError(f"invalid/missing JSON: {path}") from exc


def read_jsonl(path: Path):
    rows = []
    try:
        handle = path.open("r", encoding="utf-8-sig")
    except OSError as exc:
        raise CensusError(f"missing JSONL: {path}") from exc
    with handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise CensusError(f"invalid JSONL {path}:{number}") from exc
            if not isinstance(row, dict):
                raise CensusError(f"non-object JSONL {path}:{number}")
            rows.append(row)
    return rows


def canonical_bytes(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def value_sha256(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def official_universe(local_roster: list[dict], reconciliation: dict):
    changes = reconciliation.get("local_to_official_changes") or {}
    remove = {row["member"] for row in changes.get("remove") or []}
    add = {row["member"]: row for row in changes.get("add") or []}
    universe = {
        row["member"]: {
            "member": row["member"],
            "bioguide": row.get("bioguide"),
            "chamber": row.get("chamber"),
            "roster_origin": "local",
        }
        for row in local_roster
        if row["member"] not in remove
    }
    for member, row in add.items():
        office = str(row.get("office") or "")
        universe[member] = {
            "member": member,
            "bioguide": row.get("bioguide"),
            "chamber": "Senate" if "Senator" in office else "House",
            "roster_origin": "official_reconciliation_addition",
        }
    return universe, sorted(remove), sorted(add)


def build(args):
    local_roster = read_json(args.roster)
    reconciliation = read_json(args.reconciliation)
    universe, removed, added = official_universe(local_roster, reconciliation)

    anchors = read_jsonl(args.anchor_manifest)
    canonical = read_jsonl(args.canonical_manifest)
    descriptive = read_json(args.descriptive_artifact)
    locked_gap = {
        row["member"]: row for row in read_jsonl(args.locked_gap)
    }
    youtube = read_jsonl(args.youtube_manifest)
    cache = read_json(args.b5_cache)

    superseded = {
        row["canonical_manifest_row_sha256"]
        for row in (
            descriptive.get("source", {})
            .get("canonical_supersession", {})
            .get("records", [])
        )
    }

    # Canonical hashes are indexed first so an anchor copied from a canonical
    # clip retains the canonical event identity instead of double-counting the
    # same event under two namespaces.
    canonical_event_by_hash = {}
    for row in canonical:
        if value_sha256(row) in superseded:
            continue
        digest = str(row.get("sha256") or "")
        if digest:
            canonical_event_by_hash[digest] = "canonical:%s:%s" % (
                row.get("outlet") or "unknown",
                row.get("sid") or row.get("package") or row.get("artifact_id") or digest,
            )

    anchor_windows = Counter()
    anchor_events = defaultdict(set)
    anchor_seconds = Counter()
    strict_events = defaultdict(set)
    strict_audio_seconds = defaultdict(dict)
    manifest_target_eligible_members = set()
    for row in anchors:
        member = row.get("member")
        if member not in universe:
            continue
        if row.get("target_eligible") is True:
            manifest_target_eligible_members.add(member)
        frames = int(row.get("frames") or 0)
        anchor_windows[member] += frames // WINDOW_FRAMES
        anchor_seconds[member] += frames / 16_000.0
        digest = str(row.get("audio_sha256") or "")
        event = canonical_event_by_hash.get(digest) or (
            "anchor:" + str(row.get("event_id") or row.get("anchor_id"))
        )
        anchor_events[member].add(event)
        strict_events[member].add(event)
        if digest:
            strict_audio_seconds[member][digest] = max(
                strict_audio_seconds[member].get(digest, 0.0),
                frames / 16_000.0,
            )

    canonical_any = Counter()
    canonical_nonfloor = Counter()
    canonical_parents = Counter()
    canonical_seconds = Counter()
    for row in canonical:
        member = row.get("member")
        if member not in universe or value_sha256(row) in superseded:
            continue
        frames = int(row.get("frames") or 0)
        windows = frames // WINDOW_FRAMES
        canonical_any[member] += windows
        canonical_parents[member] += 1
        canonical_seconds[member] += frames / float(row.get("samplerate") or 16_000)
        digest = str(row.get("sha256") or "")
        event = canonical_event_by_hash.get(digest)
        if event:
            strict_events[member].add(event)
        if digest:
            strict_audio_seconds[member][digest] = max(
                strict_audio_seconds[member].get(digest, 0.0),
                frames / float(row.get("samplerate") or 16_000),
            )
        if row.get("outlet") != "HouseLive":
            canonical_nonfloor[member] += windows

    raw_youtube_clips = Counter()
    raw_youtube_seconds = Counter()
    for row in youtube:
        member = row.get("member")
        if member in universe:
            raw_youtube_clips[member] += 1
            raw_youtube_seconds[member] += float(row.get("measured_duration") or 0.0)

    b5_seconds = {
        member: float(seconds)
        for member, seconds in (cache.get("seconds") or {}).items()
    }

    rows = []
    for member in sorted(universe):
        locked_recovered = int(
            locked_gap.get(member, {}).get("recovered_locked_v2_final_windows") or 0
        )
        any_verified = max(anchor_windows[member], canonical_any[member])
        nonfloor_verified = canonical_nonfloor[member] + locked_recovered
        strict_seconds = sum(strict_audio_seconds[member].values())
        strict_ready = (
            len(strict_events[member]) >= MIN_IDENTITY_EVENTS
            and strict_seconds >= MIN_IDENTITY_SECONDS
        )
        row = {
            "schema": "congress-audio-profile-census-v1",
            **universe[member],
            "profile_floor_exact_windows": PROFILE_FLOOR,
            "verified_audio_profile": any_verified >= PROFILE_FLOOR,
            "verified_nonfloor_descriptive_profile": (
                nonfloor_verified >= PROFILE_FLOOR
            ),
            "strict_identity_prerequisite_ready": strict_ready,
            "strict_identity_prerequisite": {
                "minimum_events": MIN_IDENTITY_EVENTS,
                "minimum_seconds": MIN_IDENTITY_SECONDS,
                "verified_events": len(strict_events[member]),
                "verified_unique_audio_seconds": round(strict_seconds, 3),
                "persisted_target_eligible_flag_present": (
                    member in manifest_target_eligible_members
                ),
            },
            "verified_audio_basis": {
                "anchor_exact_windows": anchor_windows[member],
                "anchor_events": len(anchor_events[member]),
                "anchor_seconds": round(anchor_seconds[member], 3),
                "canonical_any_setting_exact_windows": canonical_any[member],
                "canonical_nonfloor_exact_windows": canonical_nonfloor[member],
                "locked_v2_recovered_nonfloor_windows": locked_recovered,
                "nonfloor_exact_windows_total": nonfloor_verified,
                "canonical_parent_clips": canonical_parents[member],
                "canonical_seconds": round(canonical_seconds[member], 3),
            },
            "existing_measured_profile": {
                "b5_cache_present": member in b5_seconds,
                "b5_cache_seconds": round(b5_seconds.get(member, 0.0), 3),
            },
            "unverified_holdings": {
                "youtube_title_match_clips": raw_youtube_clips[member],
                "youtube_title_match_seconds": round(raw_youtube_seconds[member], 3),
            },
            "status": (
                "VERIFIED_AUDIO_PROFILE"
                if any_verified >= PROFILE_FLOOR
                else "NEEDS_VERIFIED_AUDIO_PROFILE"
            ),
        }
        row["row_sha256"] = value_sha256(row)
        rows.append(row)

    verified = sum(row["verified_audio_profile"] for row in rows)
    descriptive_verified = sum(
        row["verified_nonfloor_descriptive_profile"] for row in rows
    )
    strict_ready = sum(row["strict_identity_prerequisite_ready"] for row in rows)
    contradictory_target_eligible = sum(
        row["strict_identity_prerequisite"]["persisted_target_eligible_flag_present"]
        and not row["strict_identity_prerequisite_ready"]
        for row in rows
    )
    receipt = {
        "schema": "congress-audio-profile-census-receipt-v1",
        "status": "PASS",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "contract": {
            "profile_floor_exact_windows": PROFILE_FLOOR,
            "window_frames": WINDOW_FRAMES,
            "raw_youtube_title_matches_never_promoted": True,
            "verified_audio_profile_uses_max_not_sum_across_sources": True,
            "nonfloor_profile_excludes_houselive": True,
            "superseded_canonical_rows_excluded": True,
            "strict_identity_minimum_events": MIN_IDENTITY_EVENTS,
            "strict_identity_minimum_seconds": MIN_IDENTITY_SECONDS,
            "persisted_target_eligible_flags_are_not_dispositive": True,
        },
        "universe": {
            "official_named_members": len(rows),
            "local_members": len(local_roster),
            "official_additions": added,
            "official_removals": removed,
            "requested_536_requires_one_named_exclusion": len(rows) == 537,
        },
        "coverage": {
            "verified_audio_profiles": verified,
            "needs_verified_audio_profiles": len(rows) - verified,
            "verified_nonfloor_descriptive_profiles": descriptive_verified,
            "needs_nonfloor_descriptive_profiles": len(rows) - descriptive_verified,
            "members_with_unverified_youtube_holdings": sum(
                row["unverified_holdings"]["youtube_title_match_clips"] > 0
                for row in rows
            ),
            "strict_identity_prerequisite_ready": strict_ready,
            "needs_strict_identity_prerequisite": len(rows) - strict_ready,
            "persisted_target_eligible_contradictions": contradictory_target_eligible,
        },
        "inputs": {
            label: {"path": str(path.resolve()), "sha256": sha256_file(path.resolve())}
            for label, path in {
                "roster": args.roster,
                "reconciliation": args.reconciliation,
                "anchor_manifest": args.anchor_manifest,
                "canonical_manifest": args.canonical_manifest,
                "descriptive_artifact": args.descriptive_artifact,
                "locked_gap": args.locked_gap,
                "youtube_manifest": args.youtube_manifest,
                "b5_cache": args.b5_cache,
            }.items()
        },
        "output": {
            "manifest": str(args.out.resolve()),
            "rows_sha256": value_sha256(rows),
        },
    }
    atomic_jsonl(args.out, rows)
    receipt["output"]["manifest_sha256"] = sha256_file(args.out.resolve())
    atomic_json(args.receipt, receipt)
    return receipt


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=ROOT / "data/engine/congestion.json")
    parser.add_argument(
        "--reconciliation",
        type=Path,
        default=ROOT / "data/speech/congress_audio_roster_reconciliation_2026-08-31.json",
    )
    parser.add_argument(
        "--anchor-manifest",
        type=Path,
        default=ROOT / "data/speech/speaker_anchor_vault_all_verified_v3.jsonl",
    )
    parser.add_argument(
        "--canonical-manifest",
        type=Path,
        default=Path(r"C:\melange-data\member_clips\clips_manifest.jsonl"),
    )
    parser.add_argument(
        "--descriptive-artifact",
        type=Path,
        default=ROOT / "data/speech/speech_descriptive_v1.json",
    )
    parser.add_argument(
        "--locked-gap",
        type=Path,
        default=ROOT / "data/speech/member_speech_profile_gap_locked_exact_v2.jsonl",
    )
    parser.add_argument(
        "--youtube-manifest",
        type=Path,
        default=Path(r"C:\melange-data\youtube_audio\youtube_manifest.jsonl"),
    )
    parser.add_argument(
        "--b5-cache", type=Path, default=ROOT / "data/members/b5_audio_cache.json"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data/speech/congress_audio_profile_census_full_v1.jsonl",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=ROOT / "data/speech/congress_audio_profile_census_full_v1.receipt.json",
    )
    return parser.parse_args(argv)


def main(argv=None):
    receipt = build(parse_args(argv))
    print(json.dumps(receipt["coverage"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

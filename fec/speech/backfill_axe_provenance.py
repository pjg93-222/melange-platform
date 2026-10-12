#!/usr/bin/env python3
"""Backfill and verify deterministic Axe Files clip-to-turn provenance.

The original pilot feature rows retained the exact aligned word count but not
the database row that supplied each transcript turn.  This utility reconstructs
that join fail-closed: for every episode there must be exactly one monotonic
mapping from feature rows to exact-package database turns with the same
normalized content-word count.  It then writes immutable join keys and hashes
onto the already accepted clip rows.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT as _MB_ROOT  # noqa: E402

# See axe_segment.py: the project root is paths.ROOT, not a hardcoded checkout literal.
BASE = Path(_MB_ROOT)
DEFAULT_DB = BASE / "social_media.db"
DEFAULT_FEATURES = BASE / "outputs" / "axe_pilot_20260827" / "axe_features.jsonl"
DEFAULT_AXE_MANIFEST = Path(r"C:\melange-data\axe_audio\axe_manifest.jsonl")
DEFAULT_CLIP_MANIFEST = Path(r"C:\melange-data\member_clips\clips_manifest.jsonl")
PACKAGE_PREFIX = "https://www.cnn.com/audio/podcasts/axe-files/episodes/"
FILLERS = {"uh", "um", "erm"}

PILOT_EPISODES = (
    ("ac039188-cdf2-4fc7-b3e5-b00c017d18d0", "Mark Kelly", "2023-05-25"),
    ("c39ab944-4576-4d99-8ada-b045000c1277", "Tammy Baldwin", "2023-07-20"),
    ("ce60c69a-9f76-11ee-84b4-472857128c4c", "Dan Crenshaw", "2024-07-25"),
    ("d019b3d4-9f76-11ee-84b4-7bab49e6f4f4", "Nancy Pelosi", "2024-08-29"),
    ("d51bd830-9f76-11ee-84b4-df852c827ef7", "Marie Gluesenkamp Perez", "2024-12-05"),
)


class ProvenanceError(RuntimeError):
    """A provenance assertion was not uniquely satisfied."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--features", type=Path, default=DEFAULT_FEATURES)
    parser.add_argument("--axe-manifest", type=Path, default=DEFAULT_AXE_MANIFEST)
    parser.add_argument("--clip-manifest", type=Path, default=DEFAULT_CLIP_MANIFEST)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Atomically replace the clip manifest after every binding verifies.",
    )
    return parser.parse_args(argv)


def norm(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def content_word_count(text: str | None) -> int:
    return sum(token not in FILLERS for token in norm(text))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(row: dict[str, Any]) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)


def sha256_feature(row: dict[str, Any]) -> str:
    core = {key: value for key, value in row.items() if key != "feature_sha256"}
    return sha256_json(core)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProvenanceError(f"invalid JSON at {path}:{number}: {exc.msg}") from exc
            if not isinstance(row, dict):
                raise ProvenanceError(f"non-object JSON at {path}:{number}")
            rows.append(row)
    return rows


def exact_turns(db: Path, guid: str, member: str, day: str) -> list[dict[str, Any]]:
    package = PACKAGE_PREFIX + guid
    uri = "file:" + str(db.resolve()).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            "SELECT rowid, member, date, package, text FROM speeches "
            "WHERE source='INTERVIEW' AND chamber='axefiles' AND package=? ORDER BY rowid",
            (package,),
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise ProvenanceError(f"no exact database package for {guid}")
    turns = []
    for rowid, actual_member, actual_day, actual_package, text in rows:
        if (actual_member, actual_day, actual_package) != (member, day, package):
            raise ProvenanceError(f"member/date/package assertion failed for {guid} rowid={rowid}")
        if not isinstance(text, str) or not text.strip():
            raise ProvenanceError(f"empty transcript for {guid} rowid={rowid}")
        turns.append(
            {
                "rowid": int(rowid),
                "member": actual_member,
                "date": actual_day,
                "package": actual_package,
                "text": text,
                "words": content_word_count(text),
            }
        )
    return turns


def unique_monotonic_mapping(
    features: Sequence[dict[str, Any]], turns: Sequence[dict[str, Any]], sid: str
) -> list[dict[str, Any]]:
    counts = []
    previous_seg = -1
    for row in features:
        seg = row.get("seg")
        words = row.get("words")
        if isinstance(seg, bool) or not isinstance(seg, int) or seg <= previous_seg:
            raise ProvenanceError(f"feature segments are not strictly increasing for {sid}")
        if isinstance(words, bool) or not isinstance(words, int) or words < 1:
            raise ProvenanceError(f"invalid feature word count for {sid} seg={seg}")
        previous_seg = seg
        counts.append(words)

    @lru_cache(maxsize=None)
    def ways(feature_index: int, turn_floor: int) -> int:
        if feature_index == len(counts):
            return 1
        total = 0
        wanted = counts[feature_index]
        remaining = len(counts) - feature_index - 1
        stop = len(turns) - remaining
        for turn_index in range(turn_floor, stop):
            if turns[turn_index]["words"] != wanted:
                continue
            total += ways(feature_index + 1, turn_index + 1)
            if total > 1:
                return 2
        return total

    total = ways(0, 0)
    if total != 1:
        raise ProvenanceError(
            f"expected one monotonic feature-to-turn mapping for {sid}, found {total}"
        )
    result = []
    floor = 0
    for feature_index, wanted in enumerate(counts):
        remaining = len(counts) - feature_index - 1
        choices = [
            turn_index
            for turn_index in range(floor, len(turns) - remaining)
            if turns[turn_index]["words"] == wanted
            and ways(feature_index + 1, turn_index + 1) > 0
        ]
        if len(choices) != 1:
            raise ProvenanceError(f"mapping reconstruction diverged for {sid}")
        floor = choices[0] + 1
        result.append(turns[choices[0]])
    return result


def build_bindings(
    db: Path,
    features_path: Path,
    axe_manifest_path: Path,
) -> dict[tuple[str, int], dict[str, Any]]:
    feature_rows = read_jsonl(features_path)
    axe_rows = read_jsonl(axe_manifest_path)
    axe_by_guid: dict[str, dict[str, Any]] = {}
    for row in axe_rows:
        guid = row.get("guid")
        if isinstance(guid, str) and guid:
            if guid in axe_by_guid:
                raise ProvenanceError(f"duplicate Axe manifest GUID: {guid}")
            axe_by_guid[guid] = row

    bindings: dict[tuple[str, int], dict[str, Any]] = {}
    for guid, member, day in PILOT_EPISODES:
        package = PACKAGE_PREFIX + guid
        manifest = axe_by_guid.get(guid)
        if manifest is None:
            raise ProvenanceError(f"Axe manifest lacks pilot GUID {guid}")
        if manifest.get("member") != member:
            raise ProvenanceError(f"Axe manifest member mismatch for {guid}")
        wav = Path(str(manifest.get("wav") or ""))
        if not wav.is_file():
            raise ProvenanceError(f"Axe source WAV is missing for {guid}: {wav}")
        sid = wav.stem
        episode_features = sorted(
            (row for row in feature_rows if row.get("sid") == sid),
            key=lambda row: row.get("seg", -1),
        )
        if not episode_features:
            raise ProvenanceError(f"no feature rows for {guid} sid={sid}")
        turns = exact_turns(db, guid, member, day)
        mapped_turns = unique_monotonic_mapping(episode_features, turns, sid)
        source_hash = sha256_file(wav)
        manifest_hash = sha256_json(manifest)
        for feature, turn in zip(episode_features, mapped_turns):
            seg = int(feature["seg"])
            key = (sid, seg)
            if key in bindings:
                raise ProvenanceError(f"duplicate feature binding: {key}")
            rowid = int(turn["rowid"])
            bindings[key] = {
                "provenance_schema": "axe-clip-turn-v1",
                "artifact_id": f"axe:{guid}:speeches:{rowid}:seg:{seg}",
                "episode_guid": guid,
                "turn_id": f"sqlite:speeches:{rowid}",
                "package": package,
                "turn_date": day,
                "transcript_path": str(db.resolve()) + f"#speeches.rowid={rowid}",
                "text_sha256": sha256_text(str(turn["text"])),
                "source_audio_sha256": source_hash,
                "source_manifest_sha256": manifest_hash,
                "feature_sha256": sha256_feature(feature),
                "binding_method": "exact_guid_package_member_date+unique_monotonic_word_count",
            }
    return bindings


def apply_bindings(
    clip_rows: list[dict[str, Any]], bindings: dict[tuple[str, int], dict[str, Any]]
) -> int:
    updated = 0
    seen: set[tuple[str, int]] = set()
    for row in clip_rows:
        if row.get("show") != "CNN The Axe Files":
            continue
        sid = row.get("sid")
        seg = row.get("seg")
        if not isinstance(sid, str) or isinstance(seg, bool) or not isinstance(seg, int):
            raise ProvenanceError("accepted Axe row lacks a valid sid/seg")
        key = (sid, seg)
        binding = bindings.get(key)
        if binding is None:
            raise ProvenanceError(f"accepted Axe clip has no reconstructed turn binding: {key}")
        if key in seen:
            raise ProvenanceError(f"duplicate accepted Axe sid/seg: {key}")
        seen.add(key)
        row.update(binding)
        updated += 1
    if updated == 0:
        raise ProvenanceError("no accepted Axe clips found")
    return updated


def write_jsonl_atomic(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    temporary = Path(str(path) + ".part")
    backup = path.with_name("clips_manifest.pre-axe-provenance-20260828.jsonl")
    if temporary.exists():
        raise ProvenanceError(f"stale temporary output requires review: {temporary}")
    if backup.exists():
        raise ProvenanceError(f"provenance backup already exists and requires review: {backup}")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    shutil.copy2(path, backup)
    temporary.replace(path)


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        bindings = build_bindings(args.db, args.features, args.axe_manifest)
        clip_rows = read_jsonl(args.clip_manifest)
        updated = apply_bindings(clip_rows, bindings)
        if args.write:
            write_jsonl_atomic(args.clip_manifest, clip_rows)
        bound_sources = {
            row["source_audio_sha256"]
            for row in clip_rows
            if row.get("provenance_schema") == "axe-clip-turn-v1"
        }
        print(
            f"AXE PROVENANCE mode={'write' if args.write else 'dry-run'} "
            f"accepted={updated} bound_source_episodes={len(bound_sources)}"
        )
        return 0
    except (OSError, sqlite3.Error, ProvenanceError, ValueError) as exc:
        print(f"AXE PROVENANCE FAILED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(run())

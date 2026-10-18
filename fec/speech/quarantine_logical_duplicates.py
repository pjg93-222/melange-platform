#!/usr/bin/env python3
"""Quarantine near-identical accepted clips that represent the same logical turn."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import shutil


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(r"C:\melange-data\member_clips\clips_manifest.jsonl"),
    )
    parser.add_argument(
        "--quarantine",
        type=Path,
        default=Path(r"C:\melange-data\quarantine\logical_duplicate_clips_20260828"),
    )
    parser.add_argument("--max-boundary-delta", type=float, default=3.0)
    parser.add_argument("--min-overlap", type=float, default=0.85)
    return parser.parse_args(argv)


def read_rows(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise RuntimeError("non-object manifest row at line %d" % number)
            rows.append(row)
    return rows


def write_jsonl_atomic(rows: list[dict], path: Path) -> None:
    temporary = Path(str(path) + ".logical-dedupe.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def logical_key(row: dict):
    return (row.get("outlet"), row.get("sid"), row.get("seg"), row.get("member"))


def interval_overlap(rows: list[dict]) -> float:
    start = max(float(row["start"]) for row in rows)
    end = min(float(row["end"]) for row in rows)
    union_start = min(float(row["start"]) for row in rows)
    union_end = max(float(row["end"]) for row in rows)
    return max(0.0, end - start) / (union_end - union_start)


def selection_key(row: dict):
    # Confidence first. On ties, keep the conservative (shorter) boundary set.
    return (
        -float(row.get("match") or 0.0),
        float(row["end"]) - float(row["start"]),
        str(row.get("out_wav") or "").casefold(),
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    manifest = args.manifest.resolve(strict=True)
    member_root = manifest.parent.resolve(strict=True)
    quarantine = args.quarantine.resolve(strict=False)
    if quarantine == member_root or member_root in quarantine.parents:
        raise RuntimeError("quarantine must be outside the accepted member-clips tree")

    rows = read_rows(manifest)
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        groups[logical_key(row)].append((index, row))
    duplicates = [group for group in groups.values() if len(group) > 1]

    losers = []
    receipts = []
    for group in duplicates:
        group_rows = [row for _, row in group]
        starts = [float(row["start"]) for row in group_rows]
        ends = [float(row["end"]) for row in group_rows]
        overlap = interval_overlap(group_rows)
        if max(starts) - min(starts) > args.max_boundary_delta:
            raise RuntimeError("duplicate group exceeds start-boundary safety limit: %r" % (logical_key(group_rows[0]),))
        if max(ends) - min(ends) > args.max_boundary_delta:
            raise RuntimeError("duplicate group exceeds end-boundary safety limit: %r" % (logical_key(group_rows[0]),))
        if overlap < args.min_overlap:
            raise RuntimeError("duplicate group is not near-identical: %r overlap=%.4f" % (logical_key(group_rows[0]), overlap))
        ranked = sorted(group, key=lambda item: selection_key(item[1]))
        keep_index, kept = ranked[0]
        removed = [item for item in ranked[1:]]
        losers.extend(removed)
        receipts.append({
            "key": list(logical_key(kept)),
            "overlap": overlap,
            "kept": kept["out_wav"],
            "removed": [row["out_wav"] for _, row in removed],
        })

    if not losers:
        print("LOGICAL_DEDUPE groups=0 removed=0")
        return 0

    loser_indices = {index for index, _ in losers}
    retained_sources = {
        os.path.normcase(str(Path(row["out_wav"]).resolve()))
        for index, row in enumerate(rows)
        if index not in loser_indices and row.get("out_wav")
    }
    selected_sources = set()
    moves = []
    for _, row in losers:
        source = Path(row.get("out_wav") or "").resolve(strict=True)
        if member_root not in source.parents:
            raise RuntimeError("refusing to move clip outside accepted tree: %s" % source)
        source_identity = os.path.normcase(str(source))
        if source_identity in retained_sources:
            raise RuntimeError("refusing to quarantine a source used by a retained row: %s" % source)
        if source_identity in selected_sources:
            raise RuntimeError("duplicate quarantine source: %s" % source)
        selected_sources.add(source_identity)
        destination = quarantine / source.relative_to(member_root)
        if destination.exists():
            raise RuntimeError("quarantine destination already exists: %s" % destination)
        moves.append((source, destination))

    backup = manifest.with_name(manifest.stem + ".pre-logical-dedupe-20260828.jsonl")
    if backup.exists():
        raise RuntimeError("backup already exists; refusing ambiguous rerun: %s" % backup)
    quarantine.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, backup)

    completed = []
    try:
        for source, destination in moves:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            completed.append((source, destination))
        kept_rows = [row for index, row in enumerate(rows) if index not in loser_indices]
        write_jsonl_atomic(kept_rows, manifest)
        receipt_path = quarantine / "receipt.json"
        temporary = Path(str(receipt_path) + ".tmp")
        temporary.write_text(
            json.dumps({
                "manifest": str(manifest),
                "backup": str(backup),
                "groups": len(duplicates),
                "removed": len(losers),
                "rows_before": len(rows),
                "rows_after": len(kept_rows),
                "decisions": receipts,
            }, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, receipt_path)
    except Exception:
        for source, destination in reversed(completed):
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        shutil.copy2(backup, manifest)
        raise

    print(
        "LOGICAL_DEDUPE groups=%d removed=%d rows_before=%d rows_after=%d backup=%s quarantine=%s"
        % (len(duplicates), len(losers), len(rows), len(kept_rows), backup, quarantine)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

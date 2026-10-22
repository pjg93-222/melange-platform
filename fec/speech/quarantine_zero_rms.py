#!/usr/bin/env python3
"""Atomically remove zero-RMS clip receipts and quarantine their WAV files."""

from __future__ import annotations

import argparse
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
        default=Path(r"C:\melange-data\quarantine\zero_rms_houselive_20260828"),
    )
    parser.add_argument("--outlet", default="HouseLive")
    parser.add_argument("--expected", type=int, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    rows = []
    for number, line in enumerate(args.manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            raise RuntimeError("invalid JSON at manifest line %d" % number) from exc

    rejected = [
        row
        for row in rows
        if row.get("outlet") == args.outlet and float(row.get("rms") or 0.0) == 0.0
    ]
    if len(rejected) != args.expected:
        raise RuntimeError(
            "expected %d zero-RMS %s row(s), found %d"
            % (args.expected, args.outlet, len(rejected))
        )

    args.quarantine.mkdir(parents=True, exist_ok=True)
    rejected_ids = {id(row) for row in rejected}
    kept = [row for row in rows if id(row) not in rejected_ids]
    retained_sources = {
        os.path.normcase(str(Path(row["out_wav"]).resolve()))
        for row in kept if row.get("out_wav")
    }
    moves = []
    source_identities = set()
    target_identities = set()
    for row in rejected:
        source = Path(row["out_wav"])
        if not source.is_file():
            raise RuntimeError("zero-RMS source is missing: %s" % source)
        target = args.quarantine / source.name
        if target.exists():
            raise RuntimeError("quarantine target already exists: %s" % target)
        source_identity = os.path.normcase(str(source.resolve()))
        target_identity = os.path.normcase(str(target.resolve()))
        if source_identity in retained_sources:
            raise RuntimeError("refusing to quarantine a source used by a retained row: %s" % source)
        if source_identity in source_identities:
            raise RuntimeError("duplicate quarantine source: %s" % source)
        if target_identity in target_identities:
            raise RuntimeError("duplicate quarantine target: %s" % target)
        source_identities.add(source_identity)
        target_identities.add(target_identity)
        moves.append((source, target))

    backup = args.manifest.with_name(args.manifest.stem + ".pre-zero-rms-20260828.jsonl")
    if backup.exists():
        raise RuntimeError("refusing to replace existing backup: %s" % backup)
    shutil.copy2(args.manifest, backup)

    temp = args.manifest.with_name(args.manifest.name + ".part")
    completed = []
    try:
        for source, target in moves:
            os.replace(source, target)
            completed.append((source, target))
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            for row in kept:
                handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, args.manifest)
    except Exception:
        for source, target in reversed(completed):
            if target.exists() and not source.exists():
                os.replace(target, source)
        shutil.copy2(backup, args.manifest)
        if temp.exists():
            temp.unlink()
        backup.unlink()  # identical to the restored manifest; lets a re-run proceed
        raise
    print(
        "QUARANTINED zero_rms=%d kept=%d backup=%s quarantine=%s"
        % (len(rejected), len(kept), backup, args.quarantine)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

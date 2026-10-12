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
    moves = []
    for row in rejected:
        source = Path(row["out_wav"])
        if not source.is_file():
            raise RuntimeError("zero-RMS source is missing: %s" % source)
        target = args.quarantine / source.name
        if target.exists():
            raise RuntimeError("quarantine target already exists: %s" % target)
        moves.append((source, target))

    backup = args.manifest.with_name(args.manifest.stem + ".pre-zero-rms-20260828.jsonl")
    if backup.exists():
        raise RuntimeError("refusing to replace existing backup: %s" % backup)
    shutil.copy2(args.manifest, backup)

    for source, target in moves:
        os.replace(source, target)

    rejected_ids = {id(row) for row in rejected}
    kept = [row for row in rows if id(row) not in rejected_ids]
    temp = args.manifest.with_name(args.manifest.name + ".part")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in kept:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    os.replace(temp, args.manifest)
    print(
        "QUARANTINED zero_rms=%d kept=%d backup=%s quarantine=%s"
        % (len(rejected), len(kept), backup, args.quarantine)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

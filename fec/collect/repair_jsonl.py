"""Remove syntactically invalid JSONL records with an explicit expected-invalid guard."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _signature(path: Path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--expected-invalid", type=int, required=True)
    args = parser.parse_args()

    path = args.path.resolve()
    before = _signature(path)
    kept = []
    rejected = []
    invalid = []
    for number, raw in enumerate(path.read_bytes().splitlines(keepends=True), 1):
        if not raw.strip():
            kept.append(raw)    # blank lines are not records, so they are not invalid
            continue
        try:
            if not isinstance(json.loads(raw), dict):
                raise ValueError("record is not a JSON object")
            kept.append(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            invalid.append((number, str(exc)))
            rejected.append(raw)
    if len(invalid) != args.expected_invalid:
        raise RuntimeError(
            "expected %d invalid records, found %d" % (args.expected_invalid, len(invalid))
        )
    if not invalid:
        print("valid=%d invalid_removed=0 first_invalid_line=none (file not rewritten)" % len(kept))
        return 0

    temp = path.with_name(path.name + ".repair.tmp")
    try:
        with temp.open("wb") as handle:
            for raw in kept:
                handle.write(raw)
            if kept and not kept[-1].endswith((b"\n", b"\r")):
                handle.write(b"\n")    # the output has always ended on a newline
            handle.flush()
            os.fsync(handle.fileno())
        # The dropped lines are saved before the file they came from is replaced.
        with path.with_name(path.name + ".rejected").open("ab") as dropped:
            for raw in rejected:
                dropped.write(raw if raw.endswith((b"\n", b"\r")) else raw + b"\n")
            dropped.flush()
            os.fsync(dropped.fileno())
        # ponytail: size+mtime check, not a lock; a write landing between this and os.replace is still lost.
        if _signature(path) != before:
            raise RuntimeError("%s changed while it was being repaired; nothing replaced" % path)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    print("valid=%d invalid_removed=%d first_invalid_line=%s" % (
        len(kept), len(invalid), invalid[0][0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

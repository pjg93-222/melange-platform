"""Remove syntactically invalid JSONL records with an explicit expected-invalid guard."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--expected-invalid", type=int, required=True)
    args = parser.parse_args()

    path = args.path.resolve()
    kept = []
    invalid = []
    for number, raw in enumerate(path.read_bytes().splitlines(), 1):
        try:
            json.loads(raw)
            kept.append(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            invalid.append((number, str(exc)))
    if len(invalid) != args.expected_invalid:
        raise RuntimeError(
            "expected %d invalid records, found %d" % (args.expected_invalid, len(invalid))
        )

    temp = path.with_name(path.name + ".repair.tmp")
    with temp.open("wb") as handle:
        for raw in kept:
            handle.write(raw + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    print("valid=%d invalid_removed=%d first_invalid_line=%s" % (
        len(kept), len(invalid), invalid[0][0] if invalid else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

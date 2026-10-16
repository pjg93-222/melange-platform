#!/usr/bin/env python3
"""Emit a records file containing only candidates that still need collecting.

The collector's resume path re-verifies every already-collected page by
decompressing it and recomputing its sha256. That is sound, but the cost grows
with progress: after a cf_clearance rotation forces a relaunch at N collected
records, the next run pays N verifications before issuing a single request. With
frequent rotations the lane spends more time re-proving old work than fetching
new work, and the progress counter restarts from zero each time so it looks like
the run is starting over.

Pointing --records at this filtered file removes that entirely: the collector
sees only outstanding work, starts fetching immediately, and its progress
counter means "remaining work done" rather than "records scanned".

Safety: read-only with respect to collected evidence. A candidate is considered
done only when its page body exists and its receipt parses as an accepted
receipt for the expected /text URL. This is a cheap subset of the collector's
resume check (no decompression, so no body sha256/byte_length or html_file
comparison): it never drops a record the collector would re-fetch for status or
URL reasons, but a body corrupted after acceptance is not caught here.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import json
import pathlib
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True, help="full discovery records ndjson")
    ap.add_argument("--out-dir", required=True, help="collector output generation")
    ap.add_argument("--emit", required=True, help="path to write the remaining-records ndjson")
    args = ap.parse_args()

    records = pathlib.Path(args.records)
    out_dir = pathlib.Path(args.out_dir)
    emit = pathlib.Path(args.emit)
    receipts = out_dir / "receipts"
    html = out_dir / "accepted_html"

    total = 0
    remaining: list[str] = []
    done = 0
    for line in records.read_text("utf8").splitlines():
        if not line.strip():
            continue
        total += 1
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            remaining.append(line)
            continue
        if not isinstance(rec, dict):
            remaining.append(line)
            continue
        parts = (str(rec.get("congress")), str(rec.get("type")), str(rec.get("number")))
        r = receipts.joinpath(*parts) / "receipt.json"
        h = html.joinpath(*parts) / "page.html.gz"
        # Body must exist and the receipt must be accepted for the expected
        # URL; anything unparseable stays in remaining.
        if r.is_file() and h.is_file():
            try:
                prior = json.loads(r.read_text("utf8"))
            except (OSError, ValueError):
                prior = None
            expected = str(rec.get("canonical_url") or "").rstrip("/") + "/text"
            if (
                isinstance(prior, dict)
                and prior.get("status") == "accepted"
                and rec.get("canonical_url")
                and prior.get("expected_url") == expected
            ):
                done += 1
                continue
        remaining.append(line)

    tmp = emit.with_suffix(emit.suffix + ".tmp")
    tmp.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf8")
    tmp.replace(emit)

    print("total candidates   : %d" % total)
    print("already collected  : %d" % done)
    print("remaining to fetch : %d" % len(remaining))
    print("written            : %s" % emit)
    return 0


if __name__ == "__main__":
    sys.exit(main())

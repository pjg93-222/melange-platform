"""Pre-flight for the one receipt a landing of the compute lane invalidates (read-only; prints counts, never content).

    python ops/compute/check_longform_inputs.py [--root TREE] [--receipt PATH]

data/speech/member_longform_turn_features_v1.receipt.json pins the bytes of fec/speech/build_member_longform_turn_features.py.
Landing that producer's edit makes its `--if-current` step see a changed producer and REBUILD the artifact once, and a rebuild reads
every other input the receipt names (turn files, clip manifests, wavs, transcripts). This checks, before the rebuild is allowed to
run, that each recorded input other than the producer exists and still hashes to its recorded sha256.

Output is one line of counts. No path, name or byte of any input is printed, and nothing is written. Exit 0: every input verified.
Exit 1: any input missing or changed, or none recorded. Exit 2: the receipt is absent or unreadable.
The landing step runs this against the live tree; the unit tests run it on a synthetic receipt only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import PureWindowsPath

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECEIPT = os.path.join("data", "speech", "member_longform_turn_features_v1.receipt.json")
PRODUCER = ("fec", "speech", "build_member_longform_turn_features.py")      # the one input a landing is expected to change


def load_to_host():
    """paths.to_host maps a Windows absolute path stored in the receipt onto this host (the identity on Windows)."""
    sys.path.insert(0, os.path.join(REPO, "fec"))
    from paths import to_host
    return to_host


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_producer(stored: str) -> bool:
    return tuple(part.lower() for part in PureWindowsPath(stored).parts[-3:]) == PRODUCER


def check(receipt_path: str) -> tuple[int, dict]:
    """(exit code, counts). Counts only: the caller never sees an input's name or contents."""
    try:
        with open(receipt_path, encoding="utf-8") as handle:
            inputs = json.load(handle)["inputs"]
        entries = [(str(item["path"]), str(item["sha256"])) for item in inputs]
    except (OSError, ValueError, KeyError, TypeError):
        return 2, {}
    to_host = load_to_host()
    counts = {"checked": 0, "match": 0, "missing": 0, "changed": 0, "producer_skipped": 0}
    for stored, expected in entries:
        if is_producer(stored):
            counts["producer_skipped"] += 1
            continue
        counts["checked"] += 1
        try:
            counts["match" if sha256_file(to_host(stored)) == expected else "changed"] += 1
        except OSError:
            counts["missing"] += 1
    return (0 if counts["checked"] and counts["match"] == counts["checked"] else 1), counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", default=REPO, help="tree whose data/speech receipt is read (default: this checkout)")
    parser.add_argument("--receipt", help="receipt path (default: <root>/%s)" % RECEIPT.replace(os.sep, "/"))
    args = parser.parse_args(argv)
    code, counts = check(args.receipt or os.path.join(args.root, RECEIPT))
    if code == 2:
        print("longform inputs: FAIL receipt absent or unreadable")
    else:
        print("longform inputs: %s %d checked, %d match, %d missing, %d changed (producer entries skipped: %d)"
              % ("PASS" if code == 0 else "FAIL", counts["checked"], counts["match"], counts["missing"], counts["changed"],
                 counts["producer_skipped"]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

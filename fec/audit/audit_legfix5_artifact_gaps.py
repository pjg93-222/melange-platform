#!/usr/bin/env python3
"""Cross-check every artifact path declared by a legfix5 receipt or manifest
against what is actually on disk.

Every stage in this pipeline writes a receipt that names its inputs and outputs
with absolute paths, byte counts and sha256 digests. That makes the receipts a
machine-checkable index of what is supposed to exist. This walks all of them and
reports three classes of gap:

  MISSING   declared path is absent
  SIZE      present but its byte count differs from the declaration
  HASH      present and right-sized but its sha256 differs (opt-in, --hash)

Read-only. Nothing is written, moved, or repaired.

Two rules keep this honest, and both were learned the hard way:

* A relative declaration is resolved against the DECLARING receipt's own
  directory before the workspace root.  Manifests name their own outputs
  relative to themselves (``union.records_file =
  "congress_advanced_records.ndjson"``); rooting those at the workspace
  manufactured thousands of phantom MISSING rows for files that were sitting
  right next to their manifest.

* De-duplication keys on the RESOLVED path, never on the raw declaration
  string.  Many stages emit identically named artifacts
  (``failure_attempts.ndjson`` is declared from six different stage
  directories); collapsing those by name hid the blast radius of a real loss
  behind a single row, and would silently resolve one directory's file against
  another directory's declaration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

WORKSPACE = pathlib.Path(__file__).resolve().parents[2]
ROOT = WORKSPACE / "audits" / "legfix5"

# Keys whose values are artifact paths, and the sibling keys that describe them.
PATH_KEYS = {"path", "quarantine_file", "html_file", "records_file",
             "output_directory", "ledger_file", "included_file",
             "exclusions_file", "unresolved_file", "sorted_review_queue_file",
             "decision_file", "gap_ledger_file", "exclusion_ledger_file"}

MAX_SOURCES_KEPT = 8


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(raw: str,
            source: pathlib.Path | None = None,
            workspace: pathlib.Path = WORKSPACE) -> pathlib.Path | None:
    """Resolve a declared artifact path the way its declarer meant it.

    Absolute declarations are taken as written.  A relative declaration is
    tried against ``source.parent`` first -- manifests name their own outputs
    relative to the directory the manifest lives in -- and only falls back to
    ``workspace`` when no such sibling exists, which is what the genuinely
    workspace-relative rows (``fec/tests/legislation/test_*.py``,
    ``source_data/legislation/...``) need.

    When neither candidate is on disk the declaration is genuinely missing and
    we must still name a location.  A BARE filename
    (``congress_advanced_records.ndjson``) can only ever have meant the
    declaring receipt's own directory, so that is what gets reported; a
    declaration carrying a directory component is reported workspace-relative.
    Reporting the wrong one here is how a real per-stage loss came to be
    printed as a single synthetic workspace-root path.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    p = pathlib.Path(raw.replace("\\", "/"))
    if p.is_absolute():
        return p
    sibling = source.parent / p if source is not None else None
    if sibling is not None and sibling.exists():
        return sibling
    candidate = workspace / p
    if candidate.exists():
        return candidate
    if sibling is not None and len(p.parts) == 1:
        return sibling
    return candidate


def walk(node, source: pathlib.Path, out: list) -> None:
    """Collect (declared_path, bytes, sha256, source_receipt) triples."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in PATH_KEYS and isinstance(value, str):
                out.append({
                    "path": value,
                    "bytes": node.get("bytes"),
                    "sha256": node.get("sha256"),
                    "source": source,
                    "key": key,
                })
            else:
                walk(value, source, out)
    elif isinstance(node, list):
        for item in node:
            walk(item, source, out)


def gather_receipts(root: pathlib.Path) -> list[pathlib.Path]:
    receipts = sorted(
        list(root.rglob("receipt.json"))
        + list(root.rglob("manifest.json"))
        + list(root.rglob("progress.json"))
    )
    # The harvest stages write one receipt per candidate; those are checked by
    # the harvest's own accounting, not here.
    return [r for r in receipts
            if "accepted_html" not in r.parts and "quarantine" not in r.parts]


def collect(receipts: list[pathlib.Path]) -> tuple[list[dict], int]:
    declared: list[dict] = []
    unreadable = 0
    for r in receipts:
        try:
            walk(json.loads(r.read_text("utf8")), r, declared)
        except (json.JSONDecodeError, OSError):
            unreadable += 1
    return declared, unreadable


def index_declarations(declared: list[dict],
                       workspace: pathlib.Path = WORKSPACE) -> dict:
    """Collapse declarations onto the RESOLVED path.

    Keying on the raw declaration string merged artifacts that merely share a
    file NAME across different stage directories, which both understated the
    number of affected artifacts and paired a declaration with the wrong
    directory's file.  ``declarations`` records how many receipts pointed at
    each resolved artifact so a loss reports its true blast radius.
    """
    index: dict[pathlib.Path, dict] = {}
    for d in declared:
        p = resolve(d["path"], d["source"], workspace)
        if p is None:
            continue
        entry = index.get(p)
        if entry is None:
            entry = {
                "resolved": p,
                "path": d["path"],
                "bytes": d["bytes"],
                "sha256": d["sha256"],
                "key": d["key"],
                "declarations": 0,
                "sources": [],
            }
            index[p] = entry
        entry["declarations"] += 1
        if entry["bytes"] is None and d["bytes"] is not None:
            entry["bytes"] = d["bytes"]
        if not entry["sha256"] and d["sha256"]:
            entry["sha256"] = d["sha256"]
        if len(entry["sources"]) < MAX_SOURCES_KEPT:
            entry["sources"].append(d["source"])
    return index


def audit_paths(index: dict, *, verify_hash: bool = False,
                max_hash_mb: float = 200.0) -> dict:
    """Classify every resolved artifact.

    ``size_verified`` counts artifacts that are present and match their
    declared byte count.  When ``verify_hash`` is set that population is
    further split into digests actually checked, digests SKIPPED because the
    body exceeds ``max_hash_mb``, and artifacts whose declaration carries no
    sha256 at all.  A skipped digest is never reported as verified: the
    202 MB ``category_review_queue/queue.ndjson`` always exceeds the default
    limit, and counting it as "verified OK" overstated what the audit proved.
    """
    limit = max_hash_mb * 1024 * 1024
    report: dict = {
        "missing": [],
        "size": [],
        "hash": [],
        "size_verified": 0,
        "hash_verified": 0,
        "hash_skipped_too_large": 0,
        "hash_undeclared": 0,
        "directories": 0,
        "distinct_paths": len(index),
    }
    for p, d in sorted(index.items(), key=lambda kv: str(kv[0])):
        if not p.exists():
            report["missing"].append(d)
            continue
        if p.is_dir():
            report["directories"] += 1
            continue
        size = p.stat().st_size
        if isinstance(d["bytes"], int) and size != d["bytes"]:
            report["size"].append((d, size))
            continue
        if verify_hash:
            if not d["sha256"]:
                report["hash_undeclared"] += 1
            elif size > limit:
                report["hash_skipped_too_large"] += 1
            elif sha256_file(p) != d["sha256"]:
                report["hash"].append(d)
                continue
            else:
                report["hash_verified"] += 1
        report["size_verified"] += 1
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", action="store_true",
                    help="also verify sha256 (slow on large artifacts)")
    ap.add_argument("--max-hash-mb", type=float, default=200.0)
    ap.add_argument("--root", type=pathlib.Path, default=ROOT,
                    help="receipt tree to scan (default: audits/legfix5)")
    args = ap.parse_args()

    receipts = gather_receipts(args.root)
    print("receipts/manifests scanned: %d" % len(receipts))

    declared, unreadable = collect(receipts)
    if unreadable:
        print("unreadable receipts: %d" % unreadable)
    print("artifact declarations found: %d" % len(declared))

    # WORKSPACE is read here rather than relied on as a default argument so a
    # caller (or a test) can retarget the root it falls back to.
    index = index_declarations(declared, workspace=WORKSPACE)
    print("distinct resolved artifact paths: %d\n" % len(index))

    report = audit_paths(index, verify_hash=args.hash,
                         max_hash_mb=args.max_hash_mb)

    def rel(s: str) -> str:
        t = str(s).replace("\\", "/")
        w = str(WORKSPACE).replace("\\", "/")
        return t[len(w) + 1:] if t.startswith(w) else t

    missing = report["missing"]
    print("=== MISSING (%d resolved paths, %d declarations) ===" % (
        len(missing), sum(d["declarations"] for d in missing)))
    for d in missing:
        print("  %-96s  x%-3d [%s]" % (
            rel(d["resolved"]), d["declarations"], rel(d["sources"][0])))

    print("\n=== SIZE MISMATCH (%d) ===" % len(report["size"]))
    for d, actual in report["size"]:
        print("  %-84s declared=%-12s actual=%s" % (
            rel(d["resolved"]), d["bytes"], actual))

    if args.hash:
        print("\n=== HASH MISMATCH (%d) ===" % len(report["hash"]))
        for d in report["hash"]:
            print("  %s" % rel(d["resolved"]))

    print("\npresent and size-verified: %d    directories skipped: %d"
          % (report["size_verified"], report["directories"]))
    if args.hash:
        print("  sha256 verified:            %d" % report["hash_verified"])
        print("  sha256 SKIPPED (> %g MB):   %d"
              % (args.max_hash_mb, report["hash_skipped_too_large"]))
        print("  no sha256 declared:         %d" % report["hash_undeclared"])
    else:
        print("  sha256 not checked (pass --hash to verify digests)")

    return 1 if (missing or report["size"] or report["hash"]) else 0


if __name__ == "__main__":
    sys.exit(main())

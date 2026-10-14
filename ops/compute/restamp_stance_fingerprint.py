#!/usr/bin/env python3
"""One-shot re-stamp of the cached own-source stance model's input fingerprint (run ONCE on Windows, at landing).

fec/stance/own_source_stance_model.py:input_fingerprint() used to hash the ABSOLUTE path of each input; it now hashes the
ROOT-relative POSIX path, so a relocated root (scratch copy, Linux container) gets the same fingerprint.  The cached
bundle (data/stance/own_source_stance_model.joblib) carries the old value, so without this the next load_or_train() would
retrain.  This script proves the inputs are unchanged (the LEGACY fingerprint, replicated below, still equals the one stored
in the bundle), then rewrites ONLY bundle['fingerprint'] and the cache's .sha256 sidecar.  No model weight is touched and
nothing is retrained.

  python ops/compute/restamp_stance_fingerprint.py --dry-run     print stored / legacy / new fingerprints, change nothing
  python ops/compute/restamp_stance_fingerprint.py               re-stamp
  python ops/compute/restamp_stance_fingerprint.py --accept-stale  re-stamp even though the inputs changed since training

--accept-stale (owner-ruled reuse of a model trained on OLDER inputs): skips the legacy-equals-stored check and prints a loud
note.  Everything else holds: the integrity sidecar must pass before the pickle is loaded, only bundle['fingerprint'] changes,
the write is atomic.  The model is NOT retrained; schedule a retrain.

The script refuses (exit 3) while ROOT/.pipeline_run.lock exists.

Exit codes: 0 re-stamped, already re-stamped, or dry run; 1 the legacy fingerprint does not match the bundle (inputs have
changed since it was trained: retrain instead); 2 no cache, or the cache failed its integrity sidecar; 3 a pipeline run holds .pipeline_run.lock.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

FEC = Path(__file__).resolve().parents[2] / "fec"


def legacy_fingerprint(M) -> str:
    """input_fingerprint() as it was before the root-relative change: the absolute path is hashed with the bytes."""
    names = ["data/stance/cu_valence_registry.json", "data/stance/tweet_gold_queue.csv", "data/stance/cu_golden_labels.csv"]
    for channel in M.CHANNELS:
        names.extend((M.CONFIG[channel]["source"], M.CONFIG[channel]["gold"]))
    digest = hashlib.sha256(M.SCHEMA.encode("ascii"))
    for name in names:
        paths = (M.CO.candidate_paths(name, data_root=M.ROOT)
                 if name == "data/stance/cu_valence_registry.json"
                 else [os.path.join(M.ROOT, name)])
        for path in paths:
            digest.update(path.encode("utf-8"))
            if not os.path.exists(path):
                digest.update(b"MISSING")
                continue
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def restamp(M, dry_run: bool = False, accept_stale: bool = False) -> int:
    lock = os.path.join(M.ROOT, ".pipeline_run.lock")
    if os.path.exists(lock):
        print("refusing: %s exists (a pipeline run holds the writer lease)" % lock, file=sys.stderr)
        return 3
    cache = M.CACHE
    if not os.path.exists(cache):
        print("no cache at %s" % cache, file=sys.stderr)
        return 2
    if not M.safe_to_unpickle(cache):
        print("refusing: %s failed its integrity sidecar" % cache, file=sys.stderr)
        return 2
    bundle = M.joblib.load(cache)
    stored, legacy, new = bundle.get("fingerprint"), legacy_fingerprint(M), M.input_fingerprint()
    print("stored fingerprint: %s\nlegacy fingerprint: %s\nnew fingerprint:    %s" % (stored, legacy, new))
    if stored == new:
        print("already re-stamped")
        return 0
    if stored != legacy and accept_stale:
        bar = "!" * 78
        print(bar + "\n!! --accept-stale: the cached model was trained on OLDER inputs than today's; its fingerprint is\n"
              "!! being re-stamped so it LOADS, but its weights are NOT retrained.  Schedule a retrain.\n" + bar)
    elif stored != legacy:
        print("refusing: the legacy fingerprint does not equal the bundle's, so the inputs changed since training; "
              "retrain instead of re-stamping", file=sys.stderr)
        return 1
    if dry_run:
        print("dry run: nothing written")
        return 0
    bundle["fingerprint"] = new
    tmp = cache + ".tmp"
    M.joblib.dump(bundle, tmp)
    os.replace(tmp, cache)
    M.write_sha256_sidecar(cache)
    print("re-stamped %s" % cache)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the three fingerprints and write nothing")
    parser.add_argument("--accept-stale", action="store_true",
                        help="re-stamp even if the inputs changed since training (reuses a model trained on older inputs)")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(FEC))
    from stance import own_source_stance_model as M
    return restamp(M, args.dry_run, args.accept_stale)


if __name__ == "__main__":
    sys.exit(main())

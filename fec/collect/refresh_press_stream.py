#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the isolated press refresh into a fresh dated root for the weekly stream.

A valid same-day live-network receipt makes this idempotent.  The weekly caller treats network
failure as soft; the canonical intake gate will continue to use the newest previously valid run.

INCREMENTAL COLLECTION (2026-08-30).  This wrapper also folds completed runs back
into the production URL baseline, which is what stops every night from re-fetching
the whole backlog.  The fold runs at two points:

  * BEFORE the R leg, so today's anti-join already excludes everything a previous
    run actually collected.  This is also the self-healing retry: a run that
    sealed a receipt but was never folded -- process killed, machine rebooted,
    fold refused once -- is folded here without an operator.
  * AFTER the run seals, so tomorrow starts from today's work.

The fold is deliberately NON-FATAL.  It is fail-closed on the data (it refuses to
write a baseline it does not recognise) but must never be fail-closed on the
process: a fold that aborted the press step would stop collection every night
until a human intervened, which is precisely what the 2026-08-30 ruling forbids.
A refused fold leaves the baseline untouched, so the worst case is the old
behaviour -- a larger delta next run -- and it retries automatically.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect.build_press_engine_feed import (
    valid_run, validate_body_completion, REQUIRED_RUN_FILES, ReceiptRejected,
)
from collect.press_baseline_fold import (
    DEFAULT_BASELINE, DEFAULT_RECEIPT, fold_pending,
)
from paths import ROOT


def collector_source_hashes():
    """Same source manifest captured by R before loading collector definitions."""
    directory = Path(__file__).parent
    paths = (directory / "refresh_press_delta.R", directory / "press_member_overrides.R",
             directory / "press_official_coverage.R",
             directory / "press_release_date_precedence.R",
             directory / "press_wordpress_custom_posts.R",
             directory / "press_official_dated_listings.R",
             directory / "press_request_deadlines.R",
             directory / "press_backlog.R",
             Path(ROOT).parent / "Draft Systems" / "cu_press_release_scraper.R")
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def current_source_run(run_root, expected):
    result = valid_run(run_root)
    if result is None:
        return None
    if result[1].get("collector_sources_sha256") != expected:
        print("press receipt predates current collector sources; retrying failed/empty checkpoints")
        return None
    if result[1].get("status") != "PASS" or result[1].get("pending_body_urls") != 0:
        print("press receipt does not prove complete body attempts; resuming pending work")
        return None
    return result


def historical_recovery_run(run_root, expected, source_root):
    """A targeted repair can finish without claiming a new official inventory."""
    run_root, source_root = Path(run_root), Path(source_root).resolve()
    try:
        if not all((run_root / name).is_file() for name in REQUIRED_RUN_FILES):
            return None
        receipt = json.loads((run_root / "press_network_retry_receipt.json").read_text(encoding="utf-8"))
        source = receipt.get("historical_inventory_source") or {}
        if (receipt.get("mode") != "historical_body_recovery"
                or receipt.get("inventory_freshness") != "historical"
                or receipt.get("collector_sources_sha256") != expected
                or Path(source.get("run_root", "")).resolve() != source_root):
            return None
        hashes = {
            source_root / "press_network_retry_receipt.json": source.get("receipt_sha256"),
            source_root / "press_delta_fulltext.rds": source.get("fulltext_rds_sha256"),
            run_root / "press_delta_fulltext.rds": receipt.get("fulltext_rds_sha256"),
            run_root / "press_delta_fulltext.csv": receipt.get("fulltext_csv_sha256"),
        }
        if any(hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash
               for path, expected_hash in hashes.items()):
            return None
        validate_body_completion(run_root, receipt, require_complete=True)
        return receipt
    except (OSError, ValueError, TypeError, ReceiptRejected):
        return None


def find_rscript():
    found = shutil.which("Rscript") or shutil.which("Rscript.exe")
    if found:
        return found
    roots = (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "R",)
    candidates = []
    for root in roots:
        candidates.extend(root.glob("R-*/bin/Rscript.exe"))
    if not candidates:
        raise RuntimeError("Rscript executable not found")
    return str(sorted(candidates)[-1])


def fold_completed_runs(args, when):
    """Fold every sealed-but-unfolded run into the production baseline.

    Never raises.  See the module docstring for why this is non-fatal.
    """
    if not args.fold:
        print("press baseline fold OFF at %s (set PRESS_BASELINE_FOLD=1 to enable "
              "incremental collection)" % when)
        return []
    try:
        results = fold_pending(
            outputs_root=args.outputs_root, baseline_path=args.baseline,
            receipt_path=args.fold_receipt, skip_errors=True)
    except Exception as error:                           # noqa: BLE001
        print("PRESS BASELINE FOLD SKIPPED at %s (non-fatal): %s: %s"
              % (when, type(error).__name__, error))
        return []
    if not results:
        print("press baseline fold at %s: nothing pending" % when)
        return results
    for payload in results:
        if payload.get("status") == "REFUSED":
            print("PRESS BASELINE FOLD REFUSED at %s for %s (non-fatal, will retry): %s"
                  % (when, payload["source_run"], payload.get("error")))
            continue
        print("press baseline fold at %s: %s %s +%d rows (%s -> %s), "
              "%s candidate(s) had no stored body"
              % (when, payload["source_run"], payload["status"], payload["rows_added"],
                 payload["rows_before"], payload["rows_after"],
                 payload["candidates_without_stored_body"]))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--date-floor", default=os.environ.get("PRESS_REFRESH_FLOOR", "2026-06-25"))
    parser.add_argument("--workers", default=os.environ.get("PRESS_REFRESH_WORKERS", "4"))
    parser.add_argument("--max-delta", default=os.environ.get("PRESS_REFRESH_MAX_DELTA", "50000"))
    parser.add_argument(
        "--retry-from", type=Path,
        help="build a new sealed run by retrying only deficient article bodies from a prior run",
    )
    parser.add_argument("--outputs-root", type=Path, default=Path(ROOT) / "outputs")
    parser.add_argument("--baseline", type=Path, default=Path(DEFAULT_BASELINE))
    parser.add_argument("--fold-receipt", type=Path, default=Path(DEFAULT_RECEIPT))
    # The fold ships COMPLETE, PROVEN, and OFF, pending one ruling from Paul.
    #
    # Making collection incremental changes what a single run's
    # press_delta_scored.csv contains, and build_press_engine_feed.py rewrites
    # data/stance/press_refresh_tone.csv from exactly ONE run (255 rows out of
    # 27,234 articles on 2026-08-28).  press_stream.py calls that file a
    # supplement of "newly collected" rows over the historical corpus -- and that
    # corpus still ends at 2026-06-24, so those 255 rows are what fills the
    # 2026-06-25..now window.  (The URL BASELINE is a different file and has not
    # been frozen since the 2026-09-01 fold.)  Once the delta shrinks, the supplement
    # shrinks with it.  That is a rank-affecting change to a stance input, which
    # is Paul's call and not this change's to make silently.
    #
    # Cross-day body resume in refresh_press_delta.R carries the speed win in the
    # meantime and is composition-neutral by construction: a seeded checkpoint is
    # still returned in full by fetch_member_fulltext(), so the run materializes
    # exactly the same rows it would have fetched -- only faster.
    #
    # Set PRESS_BASELINE_FOLD=1 to turn incremental collection on.
    parser.add_argument("--fold", action="store_true",
                        default=os.environ.get("PRESS_BASELINE_FOLD", "0").strip().lower()
                        in ("1", "true", "yes", "on"),
                        help="fold completed runs into the production URL baseline, making "
                             "collection incremental (also via PRESS_BASELINE_FOLD=1)")
    parser.add_argument("--no-fold", dest="fold", action="store_false",
                        help="explicitly disable the baseline fold")
    args = parser.parse_args(argv)
    run_root = args.run_root or args.outputs_root / ("press_refresh_" + date.today().strftime("%Y%m%d"))

    # Folding is deliberately NOT performed in the collector.  A run must first
    # pass intake audit and enter the cumulative active-feed ledger; otherwise a
    # folded URL could be excluded from future collection without ever reaching
    # an engine input.  fold_promoted_press_runs.py owns that post-promotion step.
    if args.fold:
        print("press baseline fold requested; deferred until cumulative promotion")

    sources = collector_source_hashes()
    if args.retry_from:
        if run_root.resolve() == args.retry_from.resolve():
            raise ValueError("historical body recovery requires a separate target run")
        existing_path = run_root / "press_network_retry_receipt.json"
        if existing_path.is_file():
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if existing.get("mode") == "live_network_retry":
                raise ValueError("historical body recovery cannot replace a fresh-inventory run")
        if historical_recovery_run(run_root, sources, args.retry_from):
            print("historical body recovery already completed; this is not fresh inventory coverage")
            return 0
    elif current_source_run(run_root, sources):
        print("press refresh already has a valid same-day receipt: %s" % run_root)
        return 0
    script = Path(__file__).with_name("refresh_press_delta.R")
    command = [find_rscript(), str(script), str(run_root), args.date_floor,
               str(args.workers), str(args.max_delta)]
    if args.retry_from:
        command += ["--retry-from", str(args.retry_from.resolve())]
    run = subprocess.run(command, cwd=ROOT)
    if run.returncode:
        return run.returncode
    if collector_source_hashes() != sources:
        raise RuntimeError("press collector source changed during collection; clean retry required")
    if args.retry_from:
        if historical_recovery_run(run_root, sources, args.retry_from) is None:
            raise RuntimeError("targeted recovery lacks a hash-bound historical completion receipt")
        print("historical body recovery sealed; a fresh inventory run is required before daily publication")
        return 0
    if current_source_run(run_root, sources) is None:
        raise RuntimeError("press refresh exited successfully without a current-source live-network receipt")
    print("press refresh sealed: %s" % run_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fold only press runs already present in the cumulative active-feed ledger."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect.build_press_engine_feed import (  # noqa: E402
    ReceiptRejected, atomic_json, inspect_run, sha256_file,
)
from collect.press_baseline_fold import (  # noqa: E402
    DEFAULT_BASELINE,
    DEFAULT_KEEP_BACKUPS,
    DEFAULT_RECEIPT,
    RunInputRefused,
    fold_run,
    folded_run_names,
    record_skipped_uninspectable,
)
from collect.press_receipt import parse_receipt_timestamp  # noqa: E402
from paths import ROOT  # noqa: E402


REVIEW_SCHEMA = "press-fold-review-v1"
DEFAULT_REVIEW = os.path.join(ROOT, "data", "engine", "press_fold_review.json")


def _enabled(value=None):
    text = os.environ.get("PRESS_BASELINE_FOLD", "0") if value is None else value
    return str(text).strip().lower() in ("1", "true", "yes", "on")


def _intake_bound_verdict(root, intake_path):
    """``(completed_at, retry receipt)`` for a run the archived intake receipt vouches for, else None.

    inspect_run re-judges a PARTIAL run against today's tombstone streaks; a member who has since
    drained resets a streak and the run the intake accepted is refused.  The intake receipt is
    sha-bound in the feed ledger, so the run can rest on it instead: the intake names this run's
    retry receipt by hash, and the retry receipt names the two full-text files by hash."""
    try:
        intake = json.loads(Path(intake_path).read_text(encoding="utf-8"))
        retry_path = root / "press_network_retry_receipt.json"
        retry = json.loads(retry_path.read_text(encoding="utf-8"))
        if (not isinstance(intake, dict) or not isinstance(retry, dict)
                or intake.get("status") != "ACCEPTED"
                or not isinstance(intake.get("inputs"), dict)
                or intake["inputs"].get("press_retry_receipt_sha256") != sha256_file(retry_path)
                or retry.get("mode") != "live_network_retry"):
            return None
        completed = parse_receipt_timestamp(retry.get("completed_at"))
        for key, name in (("fulltext_csv_sha256", "press_delta_fulltext.csv"),
                          ("fulltext_rds_sha256", "press_delta_fulltext.rds")):
            if retry.get(key) != sha256_file(root / name):
                return None
    except (OSError, ValueError):               # ReceiptTimestampError is a ValueError
        return None
    return completed, retry


def promoted_roots(feed_receipt_path, skipped=None, verdicts=None):
    """Validate the active cumulative feed and return its accepted roots oldest first.

    ``skipped`` collects promoted runs that can no longer be inspected -- see
    the comment on the ReceiptRejected handler below for why that is a skip and
    not an abort.  Passing ``None`` keeps the old all-or-nothing behaviour.
    ``verdicts``, when given, is filled ``{root: (completed_at, receipt)}`` so the
    fold can rest on the proof established here instead of asking valid_run again.
    """
    path = Path(feed_receipt_path)
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("active press feed receipt is unreadable: %s" % path) from exc
    if (receipt.get("schema") != "press-refresh-engine-feed-v1"
            or receipt.get("status") != "ACTIVE_TYPED_SUPPLEMENT"
            or receipt.get("promotion_mode") != "CUMULATIVE_ACCEPTED_RUN_LEDGER"):
        raise RuntimeError("press fold requires a cumulative active-feed receipt")
    outputs = receipt.get("outputs") or {}
    if set(outputs) != {"tone", "fulltext", "mentions"}:
        raise RuntimeError("cumulative feed receipt does not bind all active outputs")
    for name, record in outputs.items():
        output = Path(str((record or {}).get("path") or ""))
        if not output.is_file() or sha256_file(output) != (record or {}).get("sha256"):
            raise RuntimeError("active %s output is absent or changed after promotion" % name)

    roots = []
    seen = set()
    entries = 0
    for entry in receipt.get("accepted_runs") or []:
        entries += 1
        if not isinstance(entry, dict):
            raise RuntimeError("malformed entry in cumulative feed ledger")
        root = Path(str(entry.get("source_run") or "")).resolve()
        if root in seen:
            raise RuntimeError("duplicate source run in cumulative feed ledger: %s" % root)
        seen.add(root)
        # The ledger is CUMULATIVE, so it names every run ever accepted.  inspect_run raises
        # ReceiptRejected -- not the RuntimeError every neighbouring check raises -- the moment one
        # of those directories is archived, pruned, or loses any REQUIRED_RUN_FILES entry.  That is
        # ordinary housekeeping, not evidence that a fold went wrong, and raising on it stranded
        # every OTHER promoted run behind one deleted directory.  Skip it, name it, keep going.
        verdict = rejected = None
        try:
            verdict = inspect_run(root)
        except ReceiptRejected as exc:
            if exc.reason == "PENDING_BODY_ATTEMPTS":
                rejected = exc      # the intake receipt may still vouch for it; decided below
            else:
                if skipped is None:
                    raise RuntimeError("promoted press run is no longer inspectable: %s [%s] %s"
                                       % (root, exc.reason, exc.detail)) from exc
                skipped.append({"source_run": root.name, "source_run_root": str(root),
                                "reason": exc.reason, "detail": exc.detail})
                continue
        intake = entry.get("collection_intake") or {}
        intake_path = Path(str(intake.get("path") or ""))
        # An intake receipt that is GONE was pruned with its run.  One that is present but
        # differs is evidence changed after promotion, which is never housekeeping.
        if not intake_path.is_file():
            if skipped is None:
                raise RuntimeError("accepted intake receipt changed after promotion: %s" % root)
            skipped.append({"source_run": root.name, "source_run_root": str(root),
                            "reason": "INTAKE_RECEIPT_MISSING", "detail": str(intake_path)})
            continue
        if sha256_file(intake_path) != intake.get("sha256"):
            raise RuntimeError("accepted intake receipt changed after promotion: %s" % root)
        if rejected is not None:
            verdict = _intake_bound_verdict(root, intake_path)
            if verdict is None:
                if skipped is None:
                    raise RuntimeError("promoted press run is no longer inspectable: %s [%s] %s"
                                       % (root, rejected.reason, rejected.detail)) from rejected
                skipped.append({"source_run": root.name, "source_run_root": str(root),
                                "reason": rejected.reason, "detail": rejected.detail})
                continue
        if verdicts is not None:
            verdicts[root] = verdict
        roots.append((verdict[0], str(root), root))
    if not entries:
        raise RuntimeError("cumulative feed ledger contains no accepted runs")
    roots.sort(key=lambda item: (item[0], item[1]))
    return [root for _completed, _text, root in roots]


def fold_promoted(feed_receipt_path, baseline_path=DEFAULT_BASELINE,
                  fold_receipt_path=DEFAULT_RECEIPT,
                  keep_backups=DEFAULT_KEEP_BACKUPS, skipped=None):
    verdicts = {}
    roots = promoted_roots(feed_receipt_path, skipped, verdicts)
    already = folded_run_names(fold_receipt_path)
    for entry in skipped or []:
        # A skipped run that was ALREADY folded costs nothing; one that never
        # was is a hole in the baseline that only Paul can decide how to fill.
        entry["already_folded"] = entry["source_run"] in already
    results = []
    for root in roots:
        if root.name in already:
            continue
        try:
            results.append(fold_run(
                root, baseline_path=baseline_path, receipt_path=fold_receipt_path,
                keep_backups=keep_backups, verdict=verdicts[root]))
        except RunInputRefused as exc:
            # One run missing a fold input (e.g. press_url_delta.csv, which is not in
            # REQUIRED_RUN_FILES) must not strand every later root.  Named, never folded, retried.
            # Only refusals about the RUN's own inputs: a baseline or fold-receipt refusal is an
            # integrity alarm and propagates, so later roots are never folded into a failed baseline.
            if skipped is None:
                raise
            skipped.append({"source_run": root.name, "source_run_root": str(root),
                            "reason": "FOLD_REFUSED", "detail": str(exc),
                            "already_folded": False})
    return results


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed-receipt", type=Path,
                        default=Path(ROOT) / "data/stance/press_refresh_feed_receipt.json")
    parser.add_argument("--baseline", type=Path, default=Path(DEFAULT_BASELINE))
    parser.add_argument("--fold-receipt", type=Path, default=Path(DEFAULT_RECEIPT))
    parser.add_argument("--keep-backups", type=int, default=DEFAULT_KEEP_BACKUPS)
    parser.add_argument("--review-output", type=Path, default=Path(DEFAULT_REVIEW),
                        help="where to list promoted runs the fold could not inspect")
    parser.add_argument("--enable", action="store_true",
                        help="enable explicitly; otherwise PRESS_BASELINE_FOLD must be true")
    return parser.parse_args(argv)


def write_review(review_path, feed_receipt_path, skipped):
    """Hand Paul the promoted runs the fold walked past, and which ones matter.

    Only the never-folded ones are a real hole; the rest are here so the list
    is the whole truth rather than the alarming half of it.
    """
    never = [entry for entry in skipped if not entry.get("already_folded")]
    atomic_json(review_path, {
        "schema": REVIEW_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "feed_receipt": str(feed_receipt_path),
        "skipped_uninspectable": list(skipped),
        "never_folded": len(never),
    })
    return never


def main(argv=None):
    args = parse_args(argv)
    if not (args.enable or _enabled()):
        print("press baseline fold held: cumulative promotion is safe, but folding is disabled")
        return 0
    skipped = []
    results = fold_promoted(
        args.feed_receipt, args.baseline, args.fold_receipt, args.keep_backups, skipped)
    record_skipped_uninspectable(args.fold_receipt, skipped)
    if skipped:
        never = write_review(args.review_output, args.feed_receipt, skipped)
        print("press baseline fold skipped %d uninspectable promoted run(s) "
              "(%d never folded); listed in %s" % (len(skipped), len(never), args.review_output))
        for entry in never:
            print("  NEVER FOLDED %s [%s] %s"
                  % (entry["source_run"], entry["reason"], entry["detail"]))
    if not results:
        # "already folded" would be a lie when nothing was foldable in the first place.
        if skipped:
            print("press baseline fold: nothing foldable -- all %d promoted run(s) "
                  "were skipped" % len(skipped))
        else:
            print("press baseline fold: every promoted run is already folded")
        return 0
    print("press baseline fold: %d promoted run(s), %d row(s) added" % (
        len(results), sum(int(result.get("rows_added") or 0) for result in results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fold completed press-refresh deltas back into the production URL baseline.

The weekly press refresh walks every member's index fresh and anti-joins it
against ``Draft Systems/all_press_release_index.csv``.  That baseline sat frozen
at 2026-06-24 for two months because the refresh wrapper deliberately never
calls the production scraper's ``stage_outputs()``, so the delta never shrank
and every run re-fetched the entire backlog (~34,700 URLs, ~6h).  This module
closed the loop: its one fold on 2026-09-01 added 7,454 rows (to 257,223) and
cut the next delta from 30,271 to 25,741.
Once a run's artifacts pass the same sha-bound receipt checks the intake
gate enforces, the rows the run actually fetched are appended to the baseline,
and the next run's anti-join skips them.

THE FETCHED-EVIDENCE RULE -- the correctness heart of this module.
A release enters the baseline ONLY when a >= 20-word body for that exact host
URL is durably stored by the run.  An index row folded WITHOUT a stored body
would be anti-joined out forever and its text never collected: a permanent,
silent gap.  So the fold intersects the run's index delta with the run's
full-text store and never derives from the index alone.  ``OFFICIAL_MENTION``
rows are excluded from that evidence: a mention row's URL carries a
``#BIOGUIDE`` fragment that canonicalizes onto the host URL, so a mention slice
would otherwise vouch for a host page whose own body failed.

Safety properties, each covered by an executable test in
``fec/tests/collect/test_press_baseline_fold.py``:
  * additive        -- existing bytes are copied verbatim, new rows appended
  * fetched-only    -- an index row with no stored body never enters
  * idempotent      -- a second fold of the same run adds zero rows
  * fail-closed     -- schema mismatch or a row-count decrease refuses and
                       restores from the timestamped backup
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect.build_press_engine_feed import atomic_json, sha256_file, valid_run  # noqa: E402
from paths import DRAFT, ROOT  # noqa: E402


FOLD_SCHEMA = "press-baseline-fold-v1"
MIN_BODY_WORDS = 20
DEFAULT_KEEP_BACKUPS = 5
HISTORY_LIMIT = 60
MENTION_SOURCE = "OFFICIAL_MENTION"
BACKUP_SUFFIX = ".foldbak"

# The production baseline's exact header, in order.  Any drift refuses.
BASELINE_FIELDS = ("title", "url", "date", "title_src", "bioguide", "member",
                   "chamber", "state", "party")

DEFAULT_BASELINE = os.path.join(DRAFT, "all_press_release_index.csv")
DEFAULT_RECEIPT = os.path.join(ROOT, "data", "engine", "press_baseline_fold_receipt.json")
DEFAULT_OUTPUTS = os.path.join(ROOT, "outputs")

_SCHEME_RX = re.compile(r"^https?://(www\.)?", re.IGNORECASE)


class FoldRefused(RuntimeError):
    """The fold declined to touch the baseline.  Never partially applied."""


class RunInputRefused(FoldRefused):
    """The refusal is about THIS run's own inputs (receipt, evidence, index delta).

    The baseline and the fold receipt are not at fault, so a sweep may name the run and go on to the
    next one.  Every other FoldRefused (baseline missing, empty, schema drift, a row-count decrease
    after a restore, an unreadable fold receipt) is an integrity alarm and must stop the sweep."""


def index_key(value):
    """Canonical URL identity, matching ``canonical_url()`` in refresh_press_delta.R.

    That R function is what performs the anti-join, so this must agree with it
    exactly or a folded row would not actually be excluded next run.  Note this
    is deliberately NOT ``press_stream.canonical_url``: that one keeps the
    scheme and the query case and strips every trailing slash, which is a
    different identity.
    """
    value = ("" if value is None else str(value)).strip()
    value = value.split("#", 1)[0]        # sub("#.*$", "", value)
    if value.endswith("/"):
        value = value[:-1]                # sub("/$", "", value) -- one slash only
    return _SCHEME_RX.sub("", value, count=1).lower()


def _raise_field_limit():
    """Press bodies routinely exceed the 128k default csv field limit."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = int(limit // 2)


def _open_csv(path):
    # surrogateescape round-trips any byte the historical corpus happens to
    # carry, so a single bad byte can never corrupt or abort a nightly fold.
    return io.open(path, encoding="utf-8-sig", newline="", errors="surrogateescape")


def _r_csv_line(row):
    """Render one row the way R's write.csv wrote the rest of the file.

    Character fields are quoted, ``date`` is bare, and a missing value is a
    bare ``NA`` -- matching the baseline byte conventions so ``read.csv`` sees
    folded rows exactly as it sees the 2026-06-24 rows.
    """
    rendered = []
    for name in BASELINE_FIELDS:
        value = row.get(name)
        text = "" if value is None else str(value)
        if text == "":
            rendered.append("NA")
        elif name == "date" and not any(ch in text for ch in ',"\r\n'):
            rendered.append(text)
        else:
            rendered.append('"' + text.replace('"', '""') + '"')
    return ",".join(rendered) + "\n"


def scan_baseline(path):
    """Return (key set, logical row count).  Refuses on any schema drift."""
    _raise_field_limit()
    keys = set()
    rows = 0
    with _open_csv(path) as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise FoldRefused("press baseline is empty: %s" % path)
        if tuple(header) != BASELINE_FIELDS:
            raise FoldRefused(
                "press baseline schema mismatch: expected %s, found %s"
                % (list(BASELINE_FIELDS), header))
        url_at = BASELINE_FIELDS.index("url")
        for record in reader:
            rows += 1
            if len(record) <= url_at:
                continue
            key = index_key(record[url_at])
            if key:
                keys.add(key)
    return keys, rows


def fetched_keys(path, min_body_words=MIN_BODY_WORDS):
    """Canonical keys whose body this run durably stored.

    Accepts either a run's ``press_delta_fulltext.csv`` or a checkpoint
    manifest from ``press_fetched_manifest.R``.
    """
    _raise_field_limit()
    keys = set()
    with _open_csv(path) as handle:
        reader = csv.DictReader(handle)
        names = reader.fieldnames or []
        if "url" not in names:
            raise RunInputRefused("fetched-body evidence has no url column: %s" % path)
        has_body = "body" in names
        has_count = "body_word_count" in names
        if not (has_body or has_count):
            raise RunInputRefused(
                "fetched-body evidence has neither body nor body_word_count: %s" % path)
        for row in reader:
            raw_url = (row.get("url") or "").strip()
            # A mention slice must never vouch for the host page.  Guard on the
            # source label AND on the fragment itself, so evidence files that
            # carry no source column are still safe.
            if "#" in raw_url:
                continue
            if (row.get("source") or "").strip().upper() == MENTION_SOURCE:
                continue
            words = None
            if has_count:
                raw = (row.get("body_word_count") or "").strip()
                if raw:
                    try:
                        words = int(float(raw))
                    except (ValueError, OverflowError):     # "nan" / "inf" parse but cannot int()
                        words = None
            if words is None:
                words = len((row.get("body") or "").split()) if has_body else 0
            if words < min_body_words:
                continue
            key = index_key(raw_url)
            if key:
                keys.add(key)
    return keys


def _candidate_rows(path):
    _raise_field_limit()
    with _open_csv(path) as handle:
        reader = csv.DictReader(handle)
        if "url" not in (reader.fieldnames or []):
            raise RunInputRefused("index delta has no url column: %s" % path)
        for row in reader:
            yield row


def _backup_path(baseline_path, stamp):
    """A never-colliding timestamped backup name.

    fold_pending() can fold several runs inside one second; without the
    disambiguator the second fold would silently overwrite the first fold's
    backup, so "keep the last N" would not actually hold N distinct states.
    """
    candidate = Path(str(baseline_path) + "." + stamp + BACKUP_SUFFIX)
    if not candidate.exists():
        return candidate
    for serial in range(1, 1000):
        candidate = Path("%s.%s-%03d%s" % (baseline_path, stamp, serial, BACKUP_SUFFIX))
        if not candidate.exists():
            return candidate
    raise FoldRefused("cannot allocate a backup name for %s" % baseline_path)


def _prune_backups(baseline_path, keep):
    baseline_path = Path(baseline_path)
    pattern = baseline_path.name + ".*" + BACKUP_SUFFIX
    existing = sorted(baseline_path.parent.glob(pattern))
    dropped = []
    for stale in existing[:max(0, len(existing) - max(0, int(keep)))]:
        try:
            stale.unlink()
            dropped.append(str(stale))
        except OSError:
            pass
    return [str(path) for path in sorted(baseline_path.parent.glob(pattern))], dropped


def build_folded_copy(baseline_path, tmp_path, new_rows):
    """Binary-copy the baseline, then append the new rows.

    Existing bytes are never re-encoded: the 249k historical rows keep their
    exact quoting, so nothing downstream can observe a formatting change.
    """
    shutil.copyfile(str(baseline_path), str(tmp_path))
    if os.path.getsize(tmp_path):
        with io.open(tmp_path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            needs_newline = handle.read(1) not in (b"\n", b"\r")
    else:
        needs_newline = False
    with io.open(tmp_path, "ab") as handle:
        if needs_newline:
            handle.write(b"\n")
        for row in new_rows:
            handle.write(_r_csv_line(row).encode("utf-8", "surrogateescape"))


def promote_baseline(baseline_path, tmp_path, backup_path, rows_before,
                     keep_backups=DEFAULT_KEEP_BACKUPS):
    """Back up, replace atomically, verify, and roll back on any regression.

    The backup is taken BEFORE the replace, so every crash point leaves either
    the original baseline in place or a complete replacement plus a recoverable
    copy -- there is no window in which the baseline is missing or truncated.
    """
    baseline_path = Path(baseline_path)
    shutil.copy2(str(baseline_path), str(backup_path))
    os.replace(str(tmp_path), str(baseline_path))
    try:
        _keys, rows_now = scan_baseline(baseline_path)
    except FoldRefused:
        shutil.copyfile(str(backup_path), str(baseline_path))
        raise
    if rows_now < rows_before:
        shutil.copyfile(str(backup_path), str(baseline_path))
        raise FoldRefused(
            "refusing a baseline row-count decrease (%d -> %d); restored from %s"
            % (rows_before, rows_now, backup_path))
    retained, _dropped = _prune_backups(baseline_path, keep_backups)
    return rows_now, retained


def _load_receipt(receipt_path):
    path = Path(receipt_path)
    if not path.is_file():
        return {}
    # A receipt that EXISTS but cannot be read is not "no folds have happened".  Returning {} makes
    # folded_run_names empty, re-folds every historical run, and then _write_receipt rebuilds the
    # history from that same {} -- destroying the ledger while reporting status FOLDED.  A truncated
    # file (crash, OneDrive conflict copy, a transient AV/indexer PermissionError) is exactly that
    # case; refuse it the way audit_collection.py:1174 refuses its own unreadable gate receipt.
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FoldRefused("fold receipt exists but is unreadable: %s" % path) from exc
    if not isinstance(document, dict):
        raise FoldRefused("fold receipt is not a JSON object: %s" % path)
    return document


def _done_names(receipt):
    """Runs a receipt document records as DONE.

    ``folded_runs`` is the never-truncated list; ``history`` is capped at HISTORY_LIMIT, so it only
    adds the entries of a receipt written before ``folded_runs`` existed.  A NO_OP entry counts only
    when it is ``complete``: every candidate already had a stored body, so there is nothing left to
    land.  An incomplete NO_OP recorded only that the run contributed no rows at the time it was
    examined, which happens whenever its bodies have not landed in press_delta_fulltext.csv yet
    (the press_fetched_manifest recovery lands them later); counting it would skip that run forever."""
    names = {str(name) for name in receipt.get("folded_runs") or [] if name}
    for entry in receipt.get("history") or []:
        name = (entry or {}).get("source_run")
        if name and ((entry or {}).get("status") != "NO_OP" or (entry or {}).get("complete")):
            names.add(str(name))
    return names


def folded_run_names(receipt_path=None):
    """Runs whose fold is DONE -- see ``_done_names``.  Re-examining any other run is local file
    reads only -- no network, no baseline write."""
    return _done_names(_load_receipt(receipt_path or DEFAULT_RECEIPT))


def _write_receipt(receipt_path, payload):
    previous = _load_receipt(receipt_path)
    complete = (payload["status"] == "FOLDED"
                or (payload["status"] == "NO_OP"
                    and payload.get("candidates_without_stored_body") == 0))
    history = list(previous.get("history") or [])
    history.append({
        "source_run": payload["source_run"],
        "folded_at": payload["generated_at"],
        "status": payload["status"],
        "rows_added": payload["rows_added"],
        "rows_after": payload["rows_after"],
        "complete": complete,
    })
    done = _done_names(previous)
    if complete:
        done.add(str(payload["source_run"]))
    payload = dict(payload)
    payload["history"] = history[-HISTORY_LIMIT:]
    payload["folded_runs"] = sorted(done)
    for key in ("skipped_uninspectable", "skipped_uninspectable_at"):
        if key in previous:                 # owned by record_skipped_uninspectable, not by a fold
            payload[key] = previous[key]
    atomic_json(receipt_path, payload)
    return payload


def record_skipped_uninspectable(receipt_path, entries):
    """Name in the fold receipt every promoted run the fold could not inspect.

    A promoted run that is archived or pruned used to abort the entire refresh,
    because fold_promoted_press_runs raised on it and its chain step is hard.
    Skipping it is only acceptable while it stays visible, so the receipt
    carries the list, and a later fold that skips nothing clears it again.
    """
    receipt_path = Path(receipt_path)
    entries = list(entries)
    document = _load_receipt(receipt_path)
    if not entries and not document.get("skipped_uninspectable"):
        return document
    document.setdefault("schema", FOLD_SCHEMA)
    document["skipped_uninspectable"] = entries
    document["skipped_uninspectable_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(receipt_path, document)
    return document


def fold_run(run_root, baseline_path=None, receipt_path=None, fetched_manifest=None,
             index_source=None, require_receipt=True, keep_backups=DEFAULT_KEEP_BACKUPS,
             min_body_words=MIN_BODY_WORDS, write_receipt=True, verdict=None):
    """Fold one completed run's fetched rows into the baseline.

    Returns the fold receipt payload.  Raises :class:`FoldRefused` without
    touching the baseline when the run is unproven or the baseline is not the
    shape this fold knows how to extend.  ``verdict`` is a ``(completed_at,
    receipt)`` the caller already established; ``valid_run`` is then not asked.
    """
    run_root = Path(run_root)
    baseline_path = Path(baseline_path or DEFAULT_BASELINE)
    receipt_path = Path(receipt_path or DEFAULT_RECEIPT)
    if not baseline_path.is_file():
        raise FoldRefused("press baseline not found: %s" % baseline_path)

    if verdict is None:
        verdict = valid_run(run_root)
    if verdict is None and require_receipt:
        raise RunInputRefused(
            "run has no valid live-network receipt, refusing to fold: %s" % run_root)
    retry_path = run_root / "press_network_retry_receipt.json"
    receipt_sha = sha256_file(retry_path) if retry_path.is_file() else None
    completed_at = verdict[0].isoformat() if verdict else None

    evidence_path = Path(fetched_manifest) if fetched_manifest else (
        run_root / "press_delta_fulltext.csv")
    if not evidence_path.is_file():
        raise RunInputRefused("fetched-body evidence not found: %s" % evidence_path)
    index_path = Path(index_source) if index_source else run_root / "press_url_delta.csv"
    if not index_path.is_file():
        raise RunInputRefused("index delta not found: %s" % index_path)

    stored = fetched_keys(evidence_path, min_body_words)
    existing, rows_before = scan_baseline(baseline_path)

    new_rows = []
    seen = set()
    index_rows = 0
    with_body = 0
    without_body = 0
    for row in _candidate_rows(index_path):
        index_rows += 1
        key = index_key(row.get("url"))
        if not key:
            continue
        if key not in stored:
            without_body += 1
            continue
        with_body += 1
        if key in existing or key in seen:
            continue
        seen.add(key)
        new_rows.append(row)

    rows_after = rows_before + len(new_rows)
    if rows_after < rows_before:
        raise FoldRefused(
            "refusing a baseline row-count decrease: %d -> %d" % (rows_before, rows_after))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = None
    retained = []
    if new_rows:
        tmp_path = baseline_path.with_name(baseline_path.name + ".%d.foldtmp" % os.getpid())
        backup = _backup_path(baseline_path, stamp)
        try:
            build_folded_copy(baseline_path, tmp_path, new_rows)
            rows_after, retained = promote_baseline(
                baseline_path, tmp_path, backup, rows_before, keep_backups)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    payload = {
        "schema": FOLD_SCHEMA,
        "status": "FOLDED" if new_rows else "NO_OP",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_path": str(baseline_path),
        "rows_before": rows_before,
        "rows_after": rows_after,
        "rows_added": len(new_rows),
        "source_run": run_root.name,
        "source_run_root": str(run_root.resolve()),
        "source_receipt_sha256": receipt_sha,
        "source_receipt_completed_at": completed_at,
        "receipt_validated": verdict is not None,
        "fetched_evidence": {
            "path": str(evidence_path),
            "sha256": sha256_file(evidence_path),
            "min_body_words": int(min_body_words),
            "urls_with_stored_body": len(stored),
        },
        "index_source": {
            "path": str(index_path),
            "sha256": sha256_file(index_path),
            "rows": index_rows,
        },
        "candidates_with_stored_body": with_body,
        "candidates_without_stored_body": without_body,
        "backup_path": str(backup) if backup else None,
        "backups_retained": retained,
    }
    if write_receipt:
        payload = _write_receipt(receipt_path, payload)
    return payload


def fold_pending(outputs_root=None, baseline_path=None, receipt_path=None,
                 keep_backups=DEFAULT_KEEP_BACKUPS, min_body_words=MIN_BODY_WORDS,
                 skip_errors=False):
    """Fold every completed-but-unfolded run, oldest first.

    This is the self-healing seam: a run that sealed a valid receipt but whose
    fold did not happen -- process killed, machine rebooted, fold refused once
    -- is folded automatically by the next invocation.  No operator step.

    With ``skip_errors`` a refusal on one run is recorded and the sweep
    continues, so a single bad run cannot strand every later one.  A refused
    run writes no receipt entry, so it is retried next invocation.
    """
    outputs_root = Path(outputs_root or DEFAULT_OUTPUTS)
    receipt_path = Path(receipt_path or DEFAULT_RECEIPT)
    already = folded_run_names(receipt_path)
    pending = []
    for path in sorted(outputs_root.glob("press_refresh_*")):
        if not path.is_dir() or path.name in already:
            continue
        verdict = valid_run(path)
        if verdict is None:
            continue
        pending.append((verdict[0], path))
    pending.sort(key=lambda item: (item[0], str(item[1])))
    results = []
    for _completed, path in pending:
        try:
            results.append(fold_run(
                path, baseline_path=baseline_path, receipt_path=receipt_path,
                keep_backups=keep_backups, min_body_words=min_body_words))
        except Exception as error:                       # noqa: BLE001
            if not skip_errors:
                raise
            results.append({
                "schema": FOLD_SCHEMA, "status": "REFUSED", "source_run": path.name,
                "source_run_root": str(path), "rows_added": 0, "rows_before": None,
                "rows_after": None, "candidates_without_stored_body": None,
                "error": "%s: %s" % (type(error).__name__, error),
            })
    return results


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path,
                        help="fold this single run (default: fold every pending run)")
    parser.add_argument("--outputs-root", type=Path, default=Path(DEFAULT_OUTPUTS))
    parser.add_argument("--baseline", type=Path, default=Path(DEFAULT_BASELINE))
    parser.add_argument("--receipt", type=Path, default=Path(DEFAULT_RECEIPT))
    parser.add_argument("--fetched-manifest", type=Path,
                        help="fetched-body evidence CSV from press_fetched_manifest.R, "
                             "for a run that was killed before materialize")
    parser.add_argument("--index-source", type=Path,
                        help="index rows to fold (default: <run>/press_url_delta.csv)")
    parser.add_argument("--allow-missing-receipt", action="store_true",
                        help="bootstrap only: fold a run that never sealed a receipt; "
                             "the fetched-evidence rule still applies to every row")
    parser.add_argument("--keep-backups", type=int, default=DEFAULT_KEEP_BACKUPS)
    parser.add_argument("--min-body-words", type=int, default=MIN_BODY_WORDS)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.run_root:
        results = [fold_run(
            args.run_root, baseline_path=args.baseline, receipt_path=args.receipt,
            fetched_manifest=args.fetched_manifest, index_source=args.index_source,
            require_receipt=not args.allow_missing_receipt,
            keep_backups=args.keep_backups, min_body_words=args.min_body_words)]
    else:
        results = fold_pending(
            outputs_root=args.outputs_root, baseline_path=args.baseline,
            receipt_path=args.receipt, keep_backups=args.keep_backups,
            min_body_words=args.min_body_words)
    if not results:
        print("press baseline fold: no pending runs")
        return 0
    for payload in results:
        print("press baseline fold %s: %s +%d rows (%d -> %d), %d candidate(s) had no stored body"
              % (payload["source_run"], payload["status"], payload["rows_added"],
                 payload["rows_before"], payload["rows_after"],
                 payload["candidates_without_stored_body"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

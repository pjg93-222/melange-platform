#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""File a press body URL the walk keeps deferring, and stop it blocking forever.

THE PROBLEM THIS SOLVES.  ``refresh_press_delta.R`` exits 3 whenever
``press_delta_pending.csv`` has a row, and ``validate_body_completion`` refuses
any receipt that is not ``status=PASS, pending_body_urls=0``.  A pending row is
NOT a body that failed -- ``press_backlog.R:199-200`` marks any URL the walk
actually attempted ``completed`` or ``exhausted``, and an exhausted URL is
already excluded from the next run.  A row stays pending only when it was never
attempted: the per-member request budget expired first
(``press_request_deadlines.R:173-181``, 600s/member) or the member's fetch threw
before the completion hook (``press_backlog.R:428``).  On 2026-09-13 all 2,200
pending rows were undated URLs belonging to nine members, and H001068 alone held
847 -- more than 600s can ever drain.  So the walk refuses its own generation
every run, for work it structurally cannot finish, and nothing is ever promoted.

PAUL'S RULING (2026-09-19).  "The failures need to be recorded and more
importantly saved so that they can be tried again, it should be if a fail is
detected it is filed and then returned too once collection for that session is
complete."  So: file every deferral, keep retrying it, and after N consecutive
runs withdraw only its power to refuse the whole generation.

WHAT A TOMBSTONE IS AND IS NOT.  A tombstone is a record in THIS file saying a
URL was listed pending in N consecutive runs without ever being attempted.  It
changes nothing in the walk: ``press_backlog_order`` still sorts never-attempted
URLs first, so the collector still lists and retries a tombstoned URL on every
run, and the 7-day maintenance lane still owns it.  The ONLY thing withdrawn is
its veto over intake.  A tombstoned URL that finally lands simply stops
appearing in the pending artifact, which resets it here.

THE JSON IS THE TRUTH, not the run directories.  Run roots are pruned; the
counts must survive that, so a run is folded into the ledger exactly once
(``runs_seen``) and never recomputed from directories that may be gone --
unless a run still on disk no longer carries the seal it was folded with
(``run_seals``), in which case the ledger is no replay and is rebuilt.
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
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect.build_press_engine_feed import sha256_file  # noqa: E402
from collect.press_baseline_fold import index_key  # noqa: E402
from paths import ROOT  # noqa: E402


TOMBSTONE_SCHEMA = "press-pending-tombstone-v4"
# v3 files may carry a pytest fixture run folded under a live run's name
# (X000001 as "press_refresh_20260913", hiding the real 09-13 run forever) or a
# pre-drain seal (10-11); v4 records each run's seal and ``update`` refuses
# foreign roots, so v3 replays.
# v2 reset a streak whenever a URL left the pending set, even when the run's
# listing never offered it (``listed_keys``); v3 holds those, so v2 replays.
# v1 counted URL streaks only.  A v1 file cannot answer the member question, so
# it is REBUILT rather than refused: replaying sealed runs is local reads only,
# and refusing would deadlock the gate behind a file nobody knows to delete.
REBUILDABLE_SCHEMAS = ("press-pending-tombstone-v1", "press-pending-tombstone-v2",
                       "press-pending-tombstone-v3")

# Three consecutive runs.  The per-member request budget is 600s
# (``JS_MEMBER_BUDGET`` in the upstream scraper); H001068 carried 847 deferred
# URLs on 2026-09-13, so even one second per URL cannot drain it.  Three
# consecutive full-budget deferrals prove the member's backlog exceeds half an
# hour of budget -- structurally stuck, not transiently slow.  One place only.
CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE = 3

PENDING_ARTIFACT = "press_delta_pending.csv"
INDEX_ARTIFACT = "press_refreshed_index.csv"
RETRY_RECEIPT = "press_network_retry_receipt.json"
# The two statuses the walk itself writes.  Anything else -- a truncated file, a
# half-written artifact, a run that died at rc 2 -- is not a budget deferral.
SEALED_STATUSES = ("PASS", "PARTIAL")
# The same four the intake gate requires of the artifact.
PENDING_COLUMNS = ("bioguide", "url", "date", "collection_status")
RUN_NAME_RX = re.compile(r"^press_refresh_(\d{4})(\d{2})(\d{2})$")

DEFAULT_TOMBSTONES = os.path.join(ROOT, "data", "engine", "press_pending_tombstones.json")
DEFAULT_OUTPUTS = os.path.join(ROOT, "outputs")


class PendingArtifactError(ValueError):
    """The pending artifact is not the shape this ledger knows how to count."""


def _raise_field_limit():
    """Pending rows carry titles, not bodies, but share the corpus' field sizes."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = int(limit // 2)


def record_key(bioguide, url):
    """One member's claim on one canonical URL.

    A URL is only ever fetched on behalf of the member whose listing produced
    it (``press_backlog.R:106-107`` refuses a cross-member checkpoint), so the
    identity is the pair, never the URL alone.  Returns ``""`` when either half
    is unusable, which the gate treats as un-provable rather than tombstoned.
    """
    member = ("" if bioguide is None else str(bioguide)).strip().upper()
    key = index_key(url)
    return "%s#%s" % (member, key) if member and key else ""


def run_stamp(run_name):
    """Midnight UTC of a ``press_refresh_YYYYMMDD`` directory, or ``None``.

    Deliberately derived from the run rather than the wall clock: replaying the
    same history must produce byte-identical records, or a rebuilt ledger would
    not agree with the one it replaced.
    """
    match = RUN_NAME_RX.match(str(run_name))
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)),
                        tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def read_pending(run_root):
    """Rows of one run's pending artifact.  Raises when the shape is wrong."""
    _raise_field_limit()
    path = Path(run_root) / PENDING_ARTIFACT
    if not path.is_file():
        raise PendingArtifactError("pending artifact not found: %s" % path)
    try:
        # surrogateescape so one odd byte in a member's title can never stall
        # the ledger; the fields this module reads are ASCII identities.
        with io.open(path, encoding="utf-8-sig", newline="", errors="surrogateescape") as handle:
            reader = csv.DictReader(handle)
            names = reader.fieldnames or []
            if not set(PENDING_COLUMNS).issubset(names):
                raise PendingArtifactError("pending artifact lacks coverage columns: %s" % path)
            return list(reader)
    except (OSError, csv.Error) as exc:
        raise PendingArtifactError("pending artifact unreadable: %s (%s)" % (path, exc)) from exc


def sealed_pending(run_root):
    """This run's pending rows, but only if the run's own receipt seals them.

    The ledger is persistent and a run is read exactly once, so a row counted
    here is counted forever.  A pending artifact is therefore only evidence of
    a deferral when the walk itself sealed it: the receipt exists, carries a
    status the walk writes, and its ``pending_body_urls_sha256`` matches the
    file on disk.  A run killed at rc 2 after writing the artifact, a file
    still being written, or a corrupted one all fail that and are skipped --
    a crash is not a budget deferral.  Returns ``None`` when unsealed.
    """
    run_root = Path(run_root)
    receipt_path = run_root / RETRY_RECEIPT
    pending_path = run_root / PENDING_ARTIFACT
    if not receipt_path.is_file() or not pending_path.is_file():
        return None
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(receipt, dict) or receipt.get("status") not in SEALED_STATUSES:
        return None
    try:
        if receipt.get("pending_body_urls_sha256") != sha256_file(pending_path):
            return None
        return read_pending(run_root)
    except (OSError, PendingArtifactError):
        return None


def listed_keys(run_root):
    """Record keys the run's own listing offered, or ``None`` when unknowable.

    Absence from the pending set only proves a drain when the listing still
    offered the URL.  On 2026-09-27 H001068's listing collapsed 1,732 -> 6 rows
    and H001077's 787 -> 14, so their 900-odd backlog URLs vanished from
    pending without one being fetched; both were back at 761/101 the next run.
    ``None`` (no readable index) keeps the old, stricter reset.
    """
    _raise_field_limit()
    path = Path(run_root) / INDEX_ARTIFACT
    try:
        with io.open(path, encoding="utf-8-sig", newline="", errors="surrogateescape") as handle:
            return {key for key in (record_key(row.get("bioguide"), row.get("url"))
                                    for row in csv.DictReader(handle)) if key}
    except (OSError, csv.Error):
        return None


def empty_ledger():
    return {"schema": TOMBSTONE_SCHEMA,
            "consecutive_deferrals_for_tombstone": CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE,
            "runs_seen": [], "run_seals": {}, "records": {}, "members": {}}


def load(tombstone_path=None):
    """The ledger, or a fresh one.  An unreadable ledger is never silently reset.

    Returning an empty ledger for a truncated file would replay every run from
    scratch and then overwrite the real counts with the replay -- the same
    failure ``press_baseline_fold._load_receipt`` refuses.
    """
    path = Path(tombstone_path or DEFAULT_TOMBSTONES)
    if not path.is_file():
        return empty_ledger()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PendingArtifactError("tombstone ledger exists but is unreadable: %s" % path) from exc
    if not isinstance(document, dict):
        raise PendingArtifactError("tombstone ledger schema mismatch: %s" % path)
    if document.get("schema") in REBUILDABLE_SCHEMAS:
        return empty_ledger()
    if document.get("schema") != TOMBSTONE_SCHEMA:
        raise PendingArtifactError("tombstone ledger schema mismatch: %s" % path)
    document.setdefault("runs_seen", [])
    document.setdefault("records", {})
    document.setdefault("members", {})
    document["consecutive_deferrals_for_tombstone"] = CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE
    return document


def _write(tombstone_path, document):
    path = Path(tombstone_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document["generated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with io.open(tmp, "w", encoding="utf-8", newline='\r\n') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(str(tmp), str(path))
    return document


def _fold_run(document, run_name, rows, listed=None):
    """Apply one run's pending rows to the ledger.  Pure; caller persists.

    Two streaks are kept, because they answer two different questions.  The URL
    streak answers "has the walk given up on THIS page", which is what a member
    that threw an exception mid-fetch (press_backlog.R:428) produces.  The
    MEMBER streak answers "is this member's backlog structurally undrainable",
    and it is the one that matters for the nine stuck members: they keep
    publishing, so every week brings fresh undated URLs that start at streak 1.
    Waiting for every URL to earn its own streak would mean waiting forever as
    long as a member publishes at least once every three runs.

    ``listed`` is the run's own listing (``listed_keys``).  A streak that the
    listing never re-offered is HELD for this run, neither counted nor reset.
    """
    records = document["records"]
    members = document.setdefault("members", {})
    carried = {}
    for key, record in records.items():
        if record.get("consecutive_deferrals"):
            carried.setdefault(key.split("#", 1)[0], []).append(key)
    stamp = run_stamp(run_name)
    present = set()
    member_rows = {}
    for row in rows:
        bioguide = (row.get("bioguide") or "").strip().upper()
        if bioguide:
            member_rows[bioguide] = member_rows.get(bioguide, 0) + 1
    for bioguide, count in member_rows.items():
        member = members.get(bioguide)
        if member is None:
            member = {"bioguide": bioguide, "first_seen_run": run_name,
                      "consecutive_runs_with_pending": 0, "pending_rows_last_run": 0,
                      "tombstoned_at": None}
            members[bioguide] = member
        member["last_seen_run"] = run_name
        member["pending_rows_last_run"] = count
        member["consecutive_runs_with_pending"] += 1
        if member["consecutive_runs_with_pending"] >= CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE:
            if not member["tombstoned_at"]:
                member["tombstoned_at"] = stamp
        else:
            member["tombstoned_at"] = None
    for bioguide, member in members.items():
        if bioguide in member_rows:
            continue
        if (listed is not None and carried.get(bioguide)
                and not any(key in listed for key in carried[bioguide])):
            continue
        # A sealed run that lists nothing pending for this member is proof the
        # member drained: the standing coverage hole is closed, so it must earn
        # the claim again from scratch.
        member["consecutive_runs_with_pending"] = 0
        member["pending_rows_last_run"] = 0
        member["tombstoned_at"] = None
    for row in rows:
        key = record_key(row.get("bioguide"), row.get("url"))
        if not key:
            # An unkeyable row cannot be proved tombstoned, so it is not
            # counted here either; the gate refuses the run on its account.
            continue
        present.add(key)
        record = records.get(key)
        if record is None:
            record = {"bioguide": (row.get("bioguide") or "").strip().upper(),
                      "url": (row.get("url") or "").strip(), "url_key": key.split("#", 1)[1],
                      "first_seen_run": run_name, "deferrals": 0, "consecutive_deferrals": 0,
                      "last_collection_status": None, "tombstoned_at": None}
            records[key] = record
        record["url"] = (row.get("url") or "").strip() or record["url"]
        record["last_seen_run"] = run_name
        record["last_collection_status"] = (row.get("collection_status") or "").strip() or None
        record["deferrals"] += 1
        record["consecutive_deferrals"] += 1
        if record["consecutive_deferrals"] >= CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE:
            if not record["tombstoned_at"]:
                record["tombstoned_at"] = stamp
        else:
            record["tombstoned_at"] = None
    for key, record in records.items():
        if key in present or (listed is not None and key not in listed):
            continue
        # Absent from this run's pending set: the walk either completed it or
        # the listing no longer offers it.  Either way the streak is over, and
        # a URL that comes back starts its three runs again.
        record["consecutive_deferrals"] = 0
        record["tombstoned_at"] = None
    document["runs_seen"].append(run_name)
    return document


def update(outputs_root=None, tombstone_path=None):
    """Fold every run not yet in ``runs_seen``, oldest first.  Idempotent.

    A run directory with no pending artifact is skipped WITHOUT being recorded:
    it is either a run that predates the artifact or one still in flight, and
    recording it would permanently hide the rows it later seals.
    """
    outputs_root = Path(outputs_root or DEFAULT_OUTPUTS)
    tombstone_path = Path(tombstone_path or DEFAULT_TOMBSTONES)
    same = lambda a, b: os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(
        os.path.realpath(str(b)))
    # The live ledger is a replay of ROOT/outputs only.  Runs are keyed by name,
    # so folding any other root (a test's tmp run) would mark that name seen and
    # hide the real run of the same name from every later fold.
    if (same(tombstone_path, os.path.join(ROOT, "data", "engine", "press_pending_tombstones.json"))
            and not same(outputs_root, os.path.join(ROOT, "outputs"))):
        raise PendingArtifactError("live tombstone ledger only folds %s, not %s"
                                   % (os.path.join(ROOT, "outputs"), outputs_root))
    document = load(tombstone_path)
    sealed = []
    for path in sorted(outputs_root.glob("press_refresh_*")):
        if not path.is_dir() or not RUN_NAME_RX.match(path.name):
            continue
        rows = sealed_pending(path)
        if rows is None:
            # Not sealed (yet).  Deliberately NOT added to runs_seen: the run
            # may seal later, and recording it would lose its rows forever.
            continue
        sealed.append((path, rows, sha256_file(path / PENDING_ARTIFACT)))
    seals = document.setdefault("run_seals", {})
    if any(path.name in document["runs_seen"] and seals.get(path.name) != seal
           for path, _rows, seal in sealed):
        # A run re-sealed after a drain (10-11), or another root folded under
        # its name (09-13): the counts no longer replay the evidence.  Replay.
        document = empty_ledger()
    folded = []
    for path, rows, seal in sealed:
        if path.name in document["runs_seen"]:
            continue
        _fold_run(document, path.name, rows, listed_keys(path))
        document["run_seals"][path.name] = seal
        folded.append(path.name)
    if folded:
        _write(tombstone_path, document)
    return document


def tombstoned_keys(document):
    return {key for key, record in (document.get("records") or {}).items()
            if (record or {}).get("consecutive_deferrals", 0) >= CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE
            and (record or {}).get("tombstoned_at")}


def tombstoned_members(document):
    return {bioguide for bioguide, member in (document.get("members") or {}).items()
            if (member or {}).get("consecutive_runs_with_pending", 0)
            >= CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE and (member or {}).get("tombstoned_at")}


def excused_by(document, bioguide, url, keys=None, members=None):
    """``"url"``, ``"member"``, or ``None`` -- why this pending row may be excused.

    URL first, so the counts partition and the receipt can distinguish a page
    the walk gave up on from a member it cannot drain at all.
    """
    key = record_key(bioguide, url)
    if not key:
        return None
    if key in (tombstoned_keys(document) if keys is None else keys):
        return "url"
    member = key.split("#", 1)[0]
    if member in (tombstoned_members(document) if members is None else members):
        return "member"
    return None


def pending_is_tombstoned_only(run_root, receipt, tombstone_path=None, outputs_root=None):
    """Is every pending row of this run one the walk has provably given up on?

    Returns ``(accepted, detail)``.  ``detail`` is a one-line refusal string
    when refused, and a dict of counts when accepted.  This is the ONLY place
    that relaxes the completion gate, so every refusal below is load-bearing:

      * the receipt must be the walk's own PARTIAL shape with a positive
        integer count -- never a receipt that failed for some other reason;
      * the pending artifact must hash to what the receipt sealed, so the rows
        being excused are the rows the walk actually produced;
      * the row count must equal ``pending_body_urls``, so no row can be
        smuggled in or out between the seal and the audit;
      * EVERY row must be keyed and EXCUSED -- its own URL deferred three
        consecutive sealed runs, or its member pending in three consecutive
        sealed runs.  One row short refuses.

    The member clause is not a loosening for convenience: the nine stuck
    members publish every week, so a pure per-URL rule would never clear while
    they keep publishing.  What it buys is visibility -- the counts below go
    into the intake receipt precisely so a member with 847 URLs the walk never
    fetches reads as a standing coverage hole rather than as a clean run.
    """
    run_root = Path(run_root)
    if receipt.get("status") != "PARTIAL":
        return False, "press body backlog remains incomplete"
    count = receipt.get("pending_body_urls")
    if type(count) is not int or count <= 0:
        return False, "press body backlog remains incomplete"
    try:
        document = update(outputs_root or run_root.parent, tombstone_path)
        rows = read_pending(run_root)
    except (PendingArtifactError, OSError) as exc:
        return False, "pending deferral ledger unusable: %s" % exc
    if len(rows) != count:
        return False, ("pending artifact has %d row(s), receipt claims %d"
                       % (len(rows), count))
    keys = tombstoned_keys(document)
    members = tombstoned_members(document)
    counts = {"url": 0, "member": 0}
    blocking = 0
    for row in rows:
        reason = excused_by(document, row.get("bioguide"), row.get("url"), keys, members)
        if reason is None:
            blocking += 1
        else:
            counts[reason] += 1
    if blocking:
        return False, ("%d of %d pending body URL(s) have neither been deferred %d consecutive "
                       "run(s) nor belong to a member pending that long; press body backlog "
                       "remains incomplete"
                       % (blocking, len(rows), CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE))
    return True, {"pending_body_urls_tombstoned": len(rows),
                  "excused_by_url": counts["url"], "excused_by_member": counts["member"],
                  "tombstoned_members": sorted(
                      members & {record_key(row.get("bioguide"), row.get("url")).split("#", 1)[0]
                                 for row in rows
                                 if record_key(row.get("bioguide"), row.get("url"))}),
                  "consecutive_deferrals_for_tombstone": CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs-root", type=Path, default=Path(DEFAULT_OUTPUTS))
    parser.add_argument("--tombstones", type=Path, default=Path(DEFAULT_TOMBSTONES))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    document = update(args.outputs_root, args.tombstones)
    records = document.get("records") or {}
    tombstoned = tombstoned_keys(document)
    members = tombstoned_members(document)
    print("press pending deferrals: %d sealed run(s) counted, %d URL(s) tracked, "
          "%d URL(s) and %d member(s) tombstoned at %d consecutive run(s)"
          % (len(document.get("runs_seen") or []), len(records), len(tombstoned),
             len(members), CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE))
    for bioguide in sorted(members):
        member = (document.get("members") or {})[bioguide]
        print("  STANDING COVERAGE HOLE %s: %d pending URL(s) in the last run, "
              "pending since %s" % (bioguide, member.get("pending_rows_last_run", 0),
                                    member.get("first_seen_run")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

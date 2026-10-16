#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Name the member sites that quietly stopped producing rows.  Never blocks.

A member whose site returns nothing is invisible today.  The upstream index
walk discards the per-member error object (``crawl_member_index`` is called
inside ``tryCatch(..., error = function(e) NULL)``) and ``bind_rows`` drops the
NULL; the only log line counts the members that DID appear, so a member that
vanished is not named, not counted, and not written to any coverage artifact --
``press_delta_pending.csv`` and ``press_historical_maintenance.csv`` are both
built from the index, so a member absent from the index is absent from them
too.  On 2026-09-13 three members did exactly this: W000802 went 39, 39, 39,
39, 41 rows and then 0, with nothing anywhere recording it.

PAUL'S RULING (2026-09-19): "some members may truly have NO action regarding a
specific area of legislation, so it should be something flagged for me".  So a
zero-row site is neither a failure nor a silent pass -- it is a review row.
This script therefore has no opinion and no veto: it reads the run history that
is already on disk, writes one CSV, and exits 0.

FLAGS.  ``REGRESSED`` -- the site yielded rows in some earlier run on disk and
zero in the newest one.  ``NEVER_SEEN`` -- the member is in the press corpus'
own member universe but has never yielded a row in any run on disk.  A healthy
member gets no row at all, so the file is a worklist rather than a census.

WHICH RUNS COUNT.  Only SEALED runs: ``press_network_retry_receipt.json`` is a
dict with ``completed_at`` and a status that is absent (legacy receipts) or
PASS / PARTIAL.  An in-flight or unsealed run is skipped like a missing index,
so a walk that is still writing cannot read as "everyone vanished".  This is
deliberately not ``valid_run``: that refuses every unexcused PARTIAL, and a
PARTIAL run has a complete index (only bodies are pending).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect.press_baseline_fold import DEFAULT_BASELINE  # noqa: E402
from paths import ROOT  # noqa: E402


INDEX_ARTIFACT = "press_refreshed_index.csv"
RECEIPT_ARTIFACT = "press_network_retry_receipt.json"
SEALED_STATUSES = ("PASS", "PARTIAL")
RUN_NAME_RX = re.compile(r"^press_refresh_\d{8}$")
REVIEW_FIELDS = ("bioguide", "member", "index_url_host", "run", "rows_this_run",
                 "rows_prev_run", "runs_with_rows", "last_run_with_rows",
                 "ever_yielded", "flag")

DEFAULT_OUTPUTS = os.path.join(ROOT, "outputs")
DEFAULT_REVIEW = os.path.join(ROOT, "data", "engine", "press_zero_row_review.csv")


def _raise_field_limit():
    """Index rows carry titles and provenance hashes, not bodies, but share limits."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = int(limit // 2)


def _open_csv(path):
    # surrogateescape: a single odd byte in one member's title must never cost
    # the whole review, which exists precisely to notice missing members.
    return io.open(path, encoding="utf-8-sig", newline="", errors="surrogateescape")


def host_of(url):
    try:
        return (urlsplit(("" if url is None else str(url)).strip()).netloc or "").lower()
    except ValueError:
        return ""


def run_is_sealed(run_dir):
    """True when the run's receipt marks it finished (see WHICH RUNS COUNT above)."""
    try:
        with io.open(Path(run_dir) / RECEIPT_ARTIFACT, encoding="utf-8-sig") as handle:
            receipt = json.load(handle)
    except (OSError, ValueError):
        return False
    return (isinstance(receipt, dict) and bool(receipt.get("completed_at"))
            and ("status" not in receipt or receipt["status"] in SEALED_STATUSES))


def read_run_index(path):
    """``{bioguide: count}`` plus the member name and host each bioguide showed."""
    counts = {}
    names = {}
    hosts = {}
    with _open_csv(path) as handle:
        reader = csv.DictReader(handle)
        if "bioguide" not in (reader.fieldnames or []):
            raise ValueError("refreshed index has no bioguide column: %s" % path)
        for row in reader:
            bioguide = (row.get("bioguide") or "").strip().upper()
            if not bioguide:
                continue
            counts[bioguide] = counts.get(bioguide, 0) + 1
            member = (row.get("member") or "").strip()
            if member and bioguide not in names:
                names[bioguide] = member
            host = host_of(row.get("url"))
            if host and bioguide not in hosts:
                hosts[bioguide] = host
    return counts, names, hosts


def read_universe(baseline_path):
    """The press corpus' own member universe, from the production URL baseline.

    The roster the walk uses lives in an .rds only R can read, so the baseline
    -- every press release the corpus has ever held -- stands in for it.  A
    member who is in the corpus but in no run on disk is exactly NEVER_SEEN.
    """
    names = {}
    hosts = {}
    path = Path(baseline_path)
    if not path.is_file():
        return names, hosts
    _raise_field_limit()
    with _open_csv(path) as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return names, hosts
        try:
            at_bioguide = header.index("bioguide")
            at_member = header.index("member")
            at_url = header.index("url")
        except ValueError:
            return names, hosts
        width = max(at_bioguide, at_member, at_url)
        for record in reader:
            if len(record) <= width:
                continue
            bioguide = record[at_bioguide].strip().upper()
            if not bioguide or bioguide == "NA":
                continue
            if bioguide not in names and record[at_member].strip() not in ("", "NA"):
                names[bioguide] = record[at_member].strip()
            if bioguide not in hosts:
                host = host_of(record[at_url])
                if host:
                    hosts[bioguide] = host
    return names, hosts


def review_rows(outputs_root, baseline_path):
    """One row per member worth Paul's attention, newest run last."""
    outputs_root = Path(outputs_root)
    _raise_field_limit()
    runs = []
    names = {}
    hosts = {}
    for path in sorted(outputs_root.glob("press_refresh_*")):
        if not path.is_dir() or not RUN_NAME_RX.match(path.name):
            continue
        index_path = path / INDEX_ARTIFACT
        if not index_path.is_file() or not run_is_sealed(path):
            continue
        try:
            counts, run_names, run_hosts = read_run_index(index_path)
        except (OSError, ValueError, csv.Error) as error:
            # One unreadable index must not cost the review of every other run.
            sys.stderr.write("skipping %s: %s\n" % (path.name, error))
            continue
        runs.append((path.name, counts))
        # Later runs win: a member who moved hosts should be reviewed at the
        # address the walk last actually used.
        names.update(run_names)
        hosts.update(run_hosts)
    if not runs:
        raise ValueError("no sealed, readable press run on disk carries %s under %s"
                         % (INDEX_ARTIFACT, outputs_root))

    baseline_names, baseline_hosts = read_universe(baseline_path)
    for source, target in ((baseline_names, names), (baseline_hosts, hosts)):
        for key, value in source.items():
            target.setdefault(key, value)

    latest_run, latest = runs[-1]
    previous = runs[-2][1] if len(runs) > 1 else {}
    universe = set(baseline_names) | {bioguide for _name, counts in runs for bioguide in counts}

    rows = []
    for bioguide in sorted(universe):
        with_rows = [name for name, counts in runs if counts.get(bioguide)]
        this_run = latest.get(bioguide, 0)
        if this_run:
            continue
        flag = "REGRESSED" if with_rows else "NEVER_SEEN"
        rows.append({
            "bioguide": bioguide,
            "member": names.get(bioguide, ""),
            "index_url_host": hosts.get(bioguide, ""),
            "run": latest_run,
            "rows_this_run": 0,
            "rows_prev_run": previous.get(bioguide, 0),
            "runs_with_rows": len(with_rows),
            "last_run_with_rows": with_rows[-1] if with_rows else "",
            "ever_yielded": "true" if with_rows else "false",
            "flag": flag,
        })
    return latest_run, len(runs), rows


def write_review(output_path, rows):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with io.open(tmp, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(str(tmp), str(path))
    return path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs-root", type=Path, default=Path(DEFAULT_OUTPUTS))
    parser.add_argument("--baseline", type=Path, default=Path(DEFAULT_BASELINE),
                        help="production URL baseline, used as the member universe")
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_REVIEW))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        latest_run, run_count, rows = review_rows(args.outputs_root, args.baseline)
    except (OSError, ValueError) as error:
        # The ONLY failure: there is nothing on disk to review.  Everything a
        # review could find is a flag for Paul, never a reason to stop a run.
        sys.stderr.write("press zero-row review could not read any run: %s\n" % error)
        return 1
    write_review(args.output, rows)
    regressed = [row for row in rows if row["flag"] == "REGRESSED"]
    print("press zero-row review at %s: %d run(s) read, %d regressed, %d never seen -> %s"
          % (latest_run, run_count, len(regressed),
             len(rows) - len(regressed), args.output))
    for row in regressed:
        print("  REGRESSED %s %s (%s) last had rows in %s"
              % (row["bioguide"], row["member"], row["index_url_host"],
                 row["last_run_with_rows"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Fail-closed hearings media acquisition map -- audio-identity design 2026-07-16 (user-directed build).

Purpose: turn the hand-curated hearings_media_map.csv (package,committee,video_url,notes) into
verified, never-overwriting yt-dlp audio-fetch commands for the speaker-identity layer.  Everything
about this path is fail-closed:

  * rows with an empty/whitespace video_url are SKIPPED (reason "no_video_url") -- the shipped map
    contains only such placeholder rows, so nothing is fetchable until the user curates real URLs;
  * rows whose package is not present in hearings_seq.db are SKIPPED (reason "package_unverified");
    if the db file itself is absent (fresh worktree) EVERY package is unverifiable-SKIP unless
    --allow-unverified-packages is passed;
  * --dry-run is the DEFAULT: commands are printed, never executed, and no file or directory is
    created.  Real fetching requires the explicit --fetch flag and prints a user-approval reminder
    banner first;
  * downloads can never overwrite existing files: yt-dlp's --download-archive skips completed ids
    and --no-overwrites is explicit;
  * --allow-unverified-packages is a dry-run planning aid only and can never be combined with
    --fetch.

The planned command per fetchable row (built, and in dry-run only printed):
  python -m yt_dlp --download-archive <archive> -f bestaudio -x --audio-format wav
         --postprocessor-args "-ar 16000 -ac 1" -o <out_dir>/<package>/%(id)s.%(ext)s <url>

No network access happens in tests; yt-dlp is imported/executed only on an explicit --fetch.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ROOT  # noqa: E402

DEFAULT_MAP = os.path.join(ROOT, "data", "stance", "hearings_media_map.csv")
DEFAULT_DB = os.path.join(ROOT, "hearings_seq.db")
DEFAULT_OUT_DIR = os.path.join(ROOT, "hearings_media")
DEFAULT_ARCHIVE = os.path.join(ROOT, "hearings_media", "download_archive.txt")
MAP_FIELDS = ("package", "committee", "video_url", "notes")

APPROVAL_BANNER = (
    "=" * 78 + "\n"
    "USER-APPROVAL REMINDER: --fetch downloads official committee video audio via\n"
    "yt-dlp over the network. Confirm the map rows were hand-curated and approved\n"
    "by the user BEFORE running with --fetch. Dry-run is the default for a reason.\n"
    + "=" * 78
)


def read_map(path):
    """Parse the hand-curated map CSV into a list of dicts with exactly MAP_FIELDS keys.

    Fail-closed on shape: a missing/renamed header column is a hard error, not a guess.
    Fully blank lines are ignored; extra columns are rejected.
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = tuple(name.strip() for name in (reader.fieldnames or ()))
        if header != MAP_FIELDS:
            raise SystemExit("hearings media map %s has header %r; expected %r"
                             % (path, header, MAP_FIELDS))
        rows = []
        for raw in reader:
            if raw.get(None) is not None:
                raise SystemExit("hearings media map %s row has extra columns: %r" % (path, raw))
            row = {key: (raw.get(key) or "").strip() for key in MAP_FIELDS}
            if not any(row.values()):
                continue
            rows.append(row)
    return rows


def load_known_packages(db_path):
    """Return the set of packages present in hearings_seq.db, or None if the db is absent.

    None means "unverifiable" (fresh worktree without data artifacts), which callers must treat
    as skip-everything unless the user passed --allow-unverified-packages.  An empty set is a
    real answer (db exists, no packages) and stays fail-closed.
    """
    if not os.path.exists(db_path):
        return None
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA query_only=1")
        known = {r[0] for r in con.execute("SELECT package FROM hearing_seq")}
    finally:
        con.close()
    return known


def build_command(url, package, out_dir, archive):
    """The exact yt-dlp argv for one fetchable row (16 kHz mono wav, per-package subdir)."""
    return [
        sys.executable, "-m", "yt_dlp",
        "--download-archive", archive,
        "--no-overwrites",
        "-f", "bestaudio",
        "-x", "--audio-format", "wav",
        "--postprocessor-args", "-ar 16000 -ac 1",
        "-o", os.path.join(out_dir, package, "%(id)s.%(ext)s"),
        url,
    ]


def plan_rows(rows, known, allow_unverified, out_dir, archive):
    """Split map rows into (planned, skipped) fail-closed.

    known: set of verified packages, or None when hearings_seq.db is absent (unverifiable).
    Skip reasons: "no_video_url" (empty/whitespace url) then "package_unverified".
    """
    planned, skipped = [], []
    for row in rows:
        package = row["package"]
        url = row["video_url"].strip()
        if not url:
            skipped.append({"package": package, "reason": "no_video_url"})
            continue
        if not allow_unverified and (known is None or package not in known):
            skipped.append({"package": package, "reason": "package_unverified"})
            continue
        planned.append({"package": package, "url": url,
                        "cmd": build_command(url, package, out_dir, archive)})
    return planned, skipped


FETCH_TIMEOUT_S = 3600  # per-item yt-dlp bound; a hung download counts as failed


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Fail-closed hearings media fetch planner (dry-run by default).")
    ap.add_argument("--map", default=DEFAULT_MAP, help="hand-curated map CSV")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="audio output root")
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE, help="yt-dlp --download-archive file")
    ap.add_argument("--db", default=DEFAULT_DB,
                    help="hearings_seq.db used to verify packages (default: paths.ROOT)")
    ap.add_argument("--dry-run", action="store_true",
                    help="force planning only; planning is already the default, and this "
                         "flag now REFUSES to be combined with --fetch instead of being "
                         "silently ignored")
    ap.add_argument("--fetch", action="store_true",
                    help="actually run yt-dlp (requires prior user approval)")
    ap.add_argument("--allow-unverified-packages", action="store_true",
                    help="admit rows whose package cannot be verified against hearings_seq.db")
    args = ap.parse_args(argv)
    if args.dry_run and args.fetch:
        raise SystemExit(
            "REFUSED: --dry-run cannot be combined with --fetch; --dry-run used to be a no-op flag that still downloaded")
    dry_run = not args.fetch
    if args.fetch and args.allow_unverified_packages:
        raise SystemExit(
            "REFUSED: --fetch cannot be combined with --allow-unverified-packages; "
            "real downloads require package verification against hearings_seq.db")

    rows = read_map(args.map)
    known = load_known_packages(args.db)
    if known is None:
        if args.allow_unverified_packages:
            print("WARNING: hearings_seq.db not found at %s -- every package is UNVERIFIABLE; "
                  "--allow-unverified-packages is ACTIVE, admitting them anyway." % args.db)
        else:
            print("WARNING: hearings_seq.db not found at %s -- every package is UNVERIFIABLE "
                  "and will be skipped fail-closed (pass --allow-unverified-packages to "
                  "override)." % args.db)
    planned, skipped = plan_rows(rows, known, args.allow_unverified_packages,
                                 args.out_dir, args.archive)

    for item in skipped:
        print("SKIP %-24s reason=%s" % (item["package"], item["reason"]))
    for item in planned:
        print("PLAN %-24s %s" % (item["package"], item["url"]))
        print("     cmd: %r" % (item["cmd"],))

    failed = []
    if dry_run:
        print("DRY-RUN: %d planned, %d skipped; nothing fetched, nothing written."
              % (len(planned), len(skipped)))
    else:
        print(APPROVAL_BANNER)
        import subprocess
        for item in planned:
            os.makedirs(os.path.join(args.out_dir, item["package"]), exist_ok=True)
            print("FETCH %s" % item["package"])
            try:
                rc = subprocess.run(item["cmd"], timeout=FETCH_TIMEOUT_S).returncode
            except subprocess.TimeoutExpired:
                print("ERROR: yt-dlp timed out after %ds for %s (continuing; archive keeps "
                      "completed downloads safe)" % (FETCH_TIMEOUT_S, item["package"]))
                failed.append(item["package"])
                continue
            if rc != 0:
                print("ERROR: yt-dlp exited %d for %s (continuing; archive keeps "
                      "completed downloads safe)" % (rc, item["package"]))
                failed.append(item["package"])
        print("FETCH DONE: %d attempted, %d skipped, %d failed."
              % (len(planned), len(skipped), len(failed)))
    return {"dry_run": dry_run, "planned": planned, "skipped": skipped, "failed": failed}


if __name__ == "__main__":
    result = main()
    sys.exit(1 if result["failed"] else 0)

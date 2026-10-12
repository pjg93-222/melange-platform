# -*- coding: utf-8 -*-
"""Tests for fec/members/fetch_hearings_media.py -- audio-identity design 2026-07-16 (user-directed build).

Fail-closed hearings media acquisition: hand-curated map -> verified packages -> dry-run yt-dlp
command plans.  No network, no yt-dlp installed, no files fetched; fixtures are tempfile CSVs and
a synthetic sqlite hearings_seq.db.  Covers the eight contract behaviors:

  1. shipped placeholder map parses; every empty-url row skips fail-closed with reason
     "no_video_url"
  2. unknown package with NO db present skips with reason "package_unverified" unless
     --allow-unverified-packages / allow_unverified
  3. dry-run emits the exact yt-dlp command list for a synthetic fake-url row and creates NO files
  4. the -o output template contains the per-package subdirectory
  5. the --download-archive path is threaded through verbatim
  6. running main twice in dry-run changes nothing on disk (idempotent)
  7. real fetching can never combine --fetch with --allow-unverified-packages
  8. the planned yt-dlp command explicitly refuses overwriting completed media
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys
import tempfile
import traceback

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(FEC)
sys.path.insert(0, FEC)

from members.fetch_hearings_media import (  # noqa: E402
    build_command, load_known_packages, main, plan_rows, read_map)

MAP_PATH = os.path.join(ROOT, "data", "stance", "hearings_media_map.csv")
FAKE_URL = "https://www.youtube.com/watch?v=FAKE_TEST_ONLY"


def snapshot_tree(base):
    """Recursive (relpath, size) listing for idempotence checks."""
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            out.append((os.path.relpath(full, base), os.path.getsize(full)))
    return out


def write_map(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["package", "committee", "video_url", "notes"])
        for row in rows:
            writer.writerow(row)


def test_shipped_map_is_placeholder_only_and_fail_closed():
    """(1) the committed map parses, every row is an empty-url placeholder, all rows skip."""
    assert os.path.exists(MAP_PATH), "hearings_media_map.csv missing at worktree root"
    rows = read_map(MAP_PATH)
    assert 2 <= len(rows) <= 3, "expected 2-3 example rows, got %d" % len(rows)
    for row in rows:
        assert set(row) == {"package", "committee", "video_url", "notes"}, row
        assert row["package"].strip(), "placeholder row must still name a package: %r" % (row,)
        assert not row["video_url"].strip(), (
            "shipped map must not contain a fetchable URL: %r" % (row,))
        assert "PLACEHOLDER" in row["notes"], "notes must mark the row PLACEHOLDER: %r" % (row,)
    planned, skipped = plan_rows(rows, known=None, allow_unverified=True,
                                 out_dir="X", archive="A")
    assert planned == [], "nothing in the shipped map may be fetchable"
    assert len(skipped) == len(rows)
    for item in skipped:
        assert item["reason"] == "no_video_url", item


def test_unverified_package_skips_without_override():
    """(2) db absent -> known=None -> every package unverifiable-SKIP unless the override."""
    rows = [{"package": "CHRG-118hhrg00000", "committee": "hsba00",
             "video_url": FAKE_URL, "notes": "synthetic"}]
    planned, skipped = plan_rows(rows, known=None, allow_unverified=False,
                                 out_dir="X", archive="A")
    assert planned == []
    assert len(skipped) == 1 and skipped[0]["reason"] == "package_unverified", skipped
    # override flag admits the row
    planned, skipped = plan_rows(rows, known=None, allow_unverified=True,
                                 out_dir="X", archive="A")
    assert len(planned) == 1 and skipped == [], (planned, skipped)
    # a real (synthetic) db verifies membership fail-closed
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "hearings_seq.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE hearing_seq(package TEXT PRIMARY KEY, date TEXT, seq TEXT)")
        con.execute("INSERT INTO hearing_seq VALUES('CHRG-118hhrg00000','2024-01-01','[]')")
        con.commit()
        con.close()
        known = load_known_packages(db)
        assert known == {"CHRG-118hhrg00000"}, known
        planned, skipped = plan_rows(rows, known=known, allow_unverified=False,
                                     out_dir="X", archive="A")
        assert len(planned) == 1 and skipped == [], (planned, skipped)
        bad = [dict(rows[0], package="CHRG-000notindb")]
        planned, skipped = plan_rows(bad, known=known, allow_unverified=False,
                                     out_dir="X", archive="A")
        assert planned == [] and skipped[0]["reason"] == "package_unverified", skipped
    # absent db path -> None (unverifiable), never an empty set
    assert load_known_packages(os.path.join(tmp, "gone.db")) is None


def test_dry_run_emits_exact_command_and_creates_no_files():
    """(3)+(4)+(5) exact yt-dlp argv; package subdir in -o template; archive threaded; no files."""
    with tempfile.TemporaryDirectory() as tmp:
        map_path = os.path.join(tmp, "map.csv")
        out_dir = os.path.join(tmp, "media")
        archive = os.path.join(tmp, "fetched.archive")
        package = "CHRG-118hhrg99999"
        write_map(map_path, [
            [package, "hsba00", FAKE_URL, "synthetic test row"],
            ["CHRG-118hhrg11111", "hsag00", "", "PLACEHOLDER - no url"],
        ])
        result = main(["--map", map_path, "--out-dir", out_dir, "--archive", archive,
                       "--allow-unverified-packages"])
        assert result["dry_run"] is True
        assert len(result["planned"]) == 1 and len(result["skipped"]) == 1
        assert result["skipped"][0]["reason"] == "no_video_url"
        cmd = result["planned"][0]["cmd"]
        expected = [
            sys.executable, "-m", "yt_dlp",
            "--download-archive", archive,
            "--no-overwrites",
            "-f", "bestaudio",
            "-x", "--audio-format", "wav",
            "--postprocessor-args", "-ar 16000 -ac 1",
            "-o", os.path.join(out_dir, package, "%(id)s.%(ext)s"),
            FAKE_URL,
        ]
        assert cmd == expected, "argv mismatch:\n got %r\nwant %r" % (cmd, expected)
        # (4) per-package subdirectory inside the output template
        template = cmd[cmd.index("-o") + 1]
        assert os.path.join(out_dir, package) in template, template
        # (5) archive path threaded verbatim
        assert cmd[cmd.index("--download-archive") + 1] == archive
        # (3) dry-run touched nothing: no out_dir, no archive file
        assert not os.path.exists(out_dir), "dry-run must not create the output dir"
        assert not os.path.exists(archive), "dry-run must not create the archive file"
        assert snapshot_tree(tmp) == [("map.csv", os.path.getsize(map_path))]


def test_dry_run_is_idempotent():
    """(6) two dry-run mains -> byte-identical disk state."""
    with tempfile.TemporaryDirectory() as tmp:
        map_path = os.path.join(tmp, "map.csv")
        write_map(map_path, [
            ["CHRG-118hhrg99999", "hsba00", FAKE_URL, "synthetic test row"],
        ])
        args = ["--map", map_path, "--out-dir", os.path.join(tmp, "media"),
                "--archive", os.path.join(tmp, "fetched.archive"),
                "--allow-unverified-packages"]
        first = main(args)
        snap1 = snapshot_tree(tmp)
        second = main(args)
        snap2 = snapshot_tree(tmp)
        assert snap1 == snap2, "dry-run changed disk state between runs"
        assert first["planned"][0]["cmd"] == second["planned"][0]["cmd"]


def test_fetch_refuses_unverified_package_override():
    with tempfile.TemporaryDirectory() as tmp:
        map_path = os.path.join(tmp, "map.csv")
        write_map(map_path, [
            ["CHRG-118hhrg99999", "hsba00", FAKE_URL, "synthetic test row"],
        ])
        try:
            main(["--map", map_path, "--fetch", "--allow-unverified-packages",
                  "--out-dir", os.path.join(tmp, "media"),
                  "--archive", os.path.join(tmp, "fetched.archive")])
        except SystemExit as exc:
            assert "cannot be combined" in str(exc), exc
        else:
            raise AssertionError("--fetch accepted --allow-unverified-packages")


def main_tests():
    tests = [
        test_shipped_map_is_placeholder_only_and_fail_closed,
        test_unverified_package_skips_without_override,
        test_dry_run_emits_exact_command_and_creates_no_files,
        test_dry_run_is_idempotent,
        test_fetch_refuses_unverified_package_override,
    ]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
        except Exception:
            failed += 1
            print("FAIL %s" % fn.__name__)
            traceback.print_exc()
        else:
            passed += 1
            print("PASS %s" % fn.__name__)
    print("test_hearings_media: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_tests())

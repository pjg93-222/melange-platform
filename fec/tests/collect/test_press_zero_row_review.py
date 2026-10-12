# -*- coding: utf-8 -*-
"""Contract tests for the zero-row member-site review (Paul's flag, 2026-09-19).

The review exists because a member site that returns nothing is invisible: the
upstream walk swallows the per-member error, drops the NULL, and logs only the
members that DID appear.  So the properties worth pinning are that it NOTICES,
that it does not cry wolf, and that it never blocks:

  * a member who yielded rows before and zero now is REGRESSED
  * a member in the corpus who has never yielded a row is NEVER_SEEN
  * a healthy member produces no row at all
  * a run directory with no index artifact is skipped, not counted as zero
  * with nothing readable at all it says so and exits 1; otherwise always 0

No network, no R, no live tree: runs are directories of CSVs in a temp dir.
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import sys
import tempfile
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from collect.press_zero_row_review import main, review_rows  # noqa: E402

INDEX_FIELDS = ("title", "url", "date", "bioguide", "member", "chamber", "state", "party")
BASELINE_FIELDS = ("title", "url", "date", "title_src", "bioguide", "member",
                   "chamber", "state", "party")


def _write_csv(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _member_rows(bioguide, member, host, count):
    return [{"title": "release %d" % index, "url": "https://%s/news/%d" % (host, index),
             "date": "2026-08-%02d" % (index + 1), "bioguide": bioguide,
             "member": member, "chamber": "House", "state": "MN", "party": "Democrat"}
            for index in range(count)]


class ZeroRowReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="press-zero-row-")
        self.outputs = os.path.join(self.tmp, "outputs")
        os.makedirs(self.outputs)
        self.baseline = os.path.join(self.tmp, "all_press_release_index.csv")
        self.output = os.path.join(self.tmp, "press_zero_row_review.csv")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, name, rows):
        _write_csv(os.path.join(self.outputs, name, "press_refreshed_index.csv"),
                   INDEX_FIELDS, rows)

    def _baseline(self, entries):
        rows = []
        for bioguide, member, host in entries:
            rows.append({"title": "old", "url": "https://%s/news/legacy" % host,
                         "date": "2026-01-01", "title_src": "NA", "bioguide": bioguide,
                         "member": member, "chamber": "House", "state": "MN",
                         "party": "Democrat"})
        _write_csv(self.baseline, BASELINE_FIELDS, rows)

    def _rows(self):
        _latest, _count, rows = review_rows(self.outputs, self.baseline)
        return {row["bioguide"]: row for row in rows}

    def test_a_member_who_yielded_before_and_zero_now_is_regressed(self):
        healthy = _member_rows("A000001", "Healthy Member", "healthy.house.gov", 3)
        self._run("press_refresh_20260901",
                  healthy + _member_rows("W000802", "Sheldon Whitehouse",
                                         "www.whitehouse.senate.gov", 39))
        self._run("press_refresh_20260906",
                  healthy + _member_rows("W000802", "Sheldon Whitehouse",
                                         "www.whitehouse.senate.gov", 41))
        self._run("press_refresh_20260913", healthy)
        self._baseline([("A000001", "Healthy Member", "healthy.house.gov"),
                        ("W000802", "Sheldon Whitehouse", "www.whitehouse.senate.gov")])
        rows = self._rows()
        self.assertNotIn("A000001", rows, "a healthy member is not a worklist item")
        self.assertEqual(rows["W000802"]["flag"], "REGRESSED")
        self.assertEqual(rows["W000802"]["rows_this_run"], 0)
        self.assertEqual(rows["W000802"]["rows_prev_run"], 41)
        self.assertEqual(rows["W000802"]["runs_with_rows"], 2)
        self.assertEqual(rows["W000802"]["last_run_with_rows"], "press_refresh_20260906")
        self.assertEqual(rows["W000802"]["ever_yielded"], "true")
        self.assertEqual(rows["W000802"]["member"], "Sheldon Whitehouse")
        self.assertEqual(rows["W000802"]["index_url_host"], "www.whitehouse.senate.gov")

    def test_a_corpus_member_absent_from_every_run_is_never_seen(self):
        self._run("press_refresh_20260913",
                  _member_rows("A000001", "Healthy Member", "healthy.house.gov", 2))
        self._baseline([("A000001", "Healthy Member", "healthy.house.gov"),
                        ("Q000009", "Quiet Member", "quiet.house.gov")])
        rows = self._rows()
        self.assertEqual(rows["Q000009"]["flag"], "NEVER_SEEN")
        self.assertEqual(rows["Q000009"]["ever_yielded"], "false")
        self.assertEqual(rows["Q000009"]["runs_with_rows"], 0)
        self.assertEqual(rows["Q000009"]["last_run_with_rows"], "")
        # Identity still comes from the corpus, so the row is actionable.
        self.assertEqual(rows["Q000009"]["member"], "Quiet Member")
        self.assertEqual(rows["Q000009"]["index_url_host"], "quiet.house.gov")

    def test_a_run_without_an_index_artifact_is_skipped_not_read_as_zero(self):
        # 2026-09-04 on disk is exactly this: a run dir with no refreshed index.
        self._run("press_refresh_20260901",
                  _member_rows("W000802", "Sheldon Whitehouse", "w.senate.gov", 39))
        os.makedirs(os.path.join(self.outputs, "press_refresh_20260904"))
        self._run("press_refresh_20260906",
                  _member_rows("W000802", "Sheldon Whitehouse", "w.senate.gov", 41))
        self._baseline([("W000802", "Sheldon Whitehouse", "w.senate.gov")])
        latest, count, rows = review_rows(self.outputs, self.baseline)
        self.assertEqual(latest, "press_refresh_20260906")
        self.assertEqual(count, 2)
        self.assertEqual(rows, [], "the empty run must not manufacture a regression")

    def test_a_missing_baseline_still_reviews_the_runs_it_can_read(self):
        self._run("press_refresh_20260901",
                  _member_rows("W000802", "Sheldon Whitehouse", "w.senate.gov", 39))
        self._run("press_refresh_20260913", [])
        rows = self._rows()
        self.assertEqual(rows["W000802"]["flag"], "REGRESSED")

    def test_main_writes_the_review_and_exits_zero(self):
        self._run("press_refresh_20260901",
                  _member_rows("W000802", "Sheldon Whitehouse", "w.senate.gov", 39))
        self._run("press_refresh_20260913", [])
        self._baseline([("W000802", "Sheldon Whitehouse", "w.senate.gov")])
        self.assertEqual(main(["--outputs-root", self.outputs, "--baseline", self.baseline,
                               "--output", self.output]), 0)
        with io.open(self.output, encoding="utf-8-sig", newline="") as handle:
            written = list(csv.DictReader(handle))
        self.assertEqual([row["bioguide"] for row in written], ["W000802"])
        self.assertEqual(written[0]["flag"], "REGRESSED")

    def test_main_exits_one_only_when_there_is_nothing_at_all_to_review(self):
        self.assertEqual(main(["--outputs-root", self.outputs, "--baseline", self.baseline,
                               "--output", self.output]), 1)
        self.assertFalse(os.path.exists(self.output))


if __name__ == "__main__":
    unittest.main(verbosity=2)

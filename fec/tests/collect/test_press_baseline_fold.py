# -*- coding: utf-8 -*-
"""Contract tests for the press baseline fold (incremental collection, 2026-08-30).

The fold is what ends the nightly re-fetch of the whole backlog, and it writes
the one file the anti-join trusts.  Every safety property it claims has an
executable negative control here:

  * an index row with no stored body must NOT enter the baseline
  * an OFFICIAL_MENTION slice must not vouch for its host page
  * a second fold must add zero rows and not rewrite the file
  * a schema mismatch must refuse without touching the baseline
  * a row-count decrease must refuse AND restore from the backup

``index_key`` must also agree exactly with ``canonical_url()`` in
refresh_press_delta.R -- the expected values below were produced by running
that R function under Rscript 4.6.0 on 2026-08-30, not by reading the code.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(FEC)
sys.path.insert(0, FEC)

from collect.press_baseline_fold import (  # noqa: E402
    BASELINE_FIELDS, FoldRefused, fetched_keys, fold_pending, fold_run,
    index_key, promote_baseline, scan_baseline,
)

# Verified against Rscript 4.6.0: canonical_url(x) in fec/collect/refresh_press_delta.R.
R_CANONICAL_URL_CASES = (
    ("https://www.Cantwell.senate.gov/news/A/", "cantwell.senate.gov/news/a"),
    ("HTTP://EXAMPLE.com/x#B001277", "example.com/x"),
    ("https://example.com/x", "example.com/x"),
    ("  https://www.example.com/x/  ", "example.com/x"),
    ("http://example.com//", "example.com/"),
    ("", ""),
    ("https://WWW.foo.House.gov/Media/Press-Releases?ID=A1B2#C000127",
     "foo.house.gov/media/press-releases?id=a1b2"),
    ("www.example.com/NoScheme/", "www.example.com/noscheme"),
    ("https://example.com/path/#", "example.com/path"),
    ("HTTPS://Www.Example.COM/A/B/", "example.com/a/b"),
    ("https://example.com/a?q=B%20C", "example.com/a?q=b%20c"),
    ("https://wwwexample.com/x", "wwwexample.com/x"),
    ("https://www.example.com", "example.com"),
    ("//example.com/x/", "//example.com/x"),
)

BASE_A = "https://www.alpha.senate.gov/news/one"
BASE_B = "https://www.beta.house.gov/media/two"
NEW_FETCHED = "https://www.gamma.senate.gov/news/fetched"
NEW_UNFETCHED = "https://www.delta.house.gov/media/unfetched"
NEW_SHORT = "https://www.epsilon.senate.gov/news/shell"


def _sha256(path):
    digest = hashlib.sha256()
    with io.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path, fields, rows):
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_baseline(path, urls, fields=BASELINE_FIELDS):
    """Write a baseline in R's write.csv conventions (bare NA, bare date)."""
    lines = [",".join('"%s"' % name for name in fields)]
    for url in urls:
        lines.append(",".join([
            '"a title"', '"%s"' % url, "2026-06-01", "NA", '"X000001"',
            '"Some Member"', '"Senate"', '"MN"', '"Democrat"',
        ][:len(fields)]))
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(lines) + "\n")


def _delta_row(url, bioguide="Y000002"):
    return {"title": "Fresh release", "url": url, "date": "2026-08-20",
            "bioguide": bioguide, "member": "New Member", "chamber": "Senate",
            "state": "TX", "party": "Republican",
            "url_key": index_key(url)}


def _make_run(root, delta_urls, fulltext_rows, seal=True):
    """Build a run root; with seal=True it passes valid_run()'s sha-bound gate."""
    os.makedirs(root, exist_ok=True)
    _write_csv(os.path.join(root, "press_url_delta.csv"),
               ["title", "url", "date", "bioguide", "member", "chamber", "state",
                "party", "url_key"],
               [_delta_row(url) for url in delta_urls])
    _write_csv(os.path.join(root, "press_delta_fulltext.csv"),
               ["url", "body", "body_word_count", "source"], fulltext_rows)
    for name in ("press_delta_host_articles.csv", "press_delta_article_mentions.csv",
                 "press_delta_scored.csv"):
        _write_csv(os.path.join(root, name), ["url"], [])
    with io.open(os.path.join(root, "press_delta_fulltext.rds"), "wb") as handle:
        handle.write(b"\x1f\x8b fixture rds bytes")
    if not seal:
        return root
    receipt = {
        "schema_version": 1, "mode": "live_network_retry",
        "started_at": "2026-08-30T00:00:00Z", "completed_at": "2026-08-30T05:00:00+00:00",
        "fulltext_csv_sha256": _sha256(os.path.join(root, "press_delta_fulltext.csv")),
        "fulltext_rds_sha256": _sha256(os.path.join(root, "press_delta_fulltext.rds")),
    }
    with io.open(os.path.join(root, "press_network_retry_receipt.json"), "w",
                 encoding="utf-8") as handle:
        json.dump(receipt, handle)
    return root


def _body(words):
    return " ".join("word%d" % i for i in range(words))


class PressBaselineFoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="press_fold_")
        self.baseline = os.path.join(self.tmp, "all_press_release_index.csv")
        self.receipt = os.path.join(self.tmp, "press_baseline_fold_receipt.json")
        self.outputs = os.path.join(self.tmp, "outputs")
        os.makedirs(self.outputs, exist_ok=True)
        _write_baseline(self.baseline, [BASE_A, BASE_B])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, name="press_refresh_20260830", delta=None, fulltext=None, seal=True):
        return _make_run(os.path.join(self.outputs, name),
                         delta if delta is not None else [NEW_FETCHED],
                         fulltext if fulltext is not None else [
                             {"url": NEW_FETCHED, "body": _body(50),
                              "body_word_count": 50, "source": "HOST"}],
                         seal=seal)

    def _fold(self, run_root, **kwargs):
        kwargs.setdefault("baseline_path", self.baseline)
        kwargs.setdefault("receipt_path", self.receipt)
        return fold_run(run_root, **kwargs)

    def _baseline_urls(self):
        with io.open(self.baseline, encoding="utf-8-sig", newline="") as handle:
            return [row["url"] for row in csv.DictReader(handle)]

    # ---- the fetched-evidence rule -------------------------------------

    def test_only_rows_with_a_stored_body_enter_the_baseline(self):
        run = self._run(delta=[NEW_FETCHED, NEW_UNFETCHED],
                        fulltext=[{"url": NEW_FETCHED, "body": _body(50),
                                   "body_word_count": 50, "source": "HOST"}])
        payload = self._fold(run)
        self.assertEqual(payload["rows_added"], 1)
        self.assertEqual(payload["candidates_without_stored_body"], 1)
        urls = self._baseline_urls()
        self.assertIn(NEW_FETCHED, urls)
        # NEGATIVE CONTROL: folding this would make the release permanently
        # invisible to the anti-join with no text ever collected.
        self.assertNotIn(NEW_UNFETCHED, urls)

    def test_short_body_does_not_count_as_stored(self):
        run = self._run(delta=[NEW_SHORT],
                        fulltext=[{"url": NEW_SHORT, "body": _body(5),
                                   "body_word_count": 5, "source": "HOST"}])
        payload = self._fold(run)
        self.assertEqual(payload["rows_added"], 0)
        self.assertNotIn(NEW_SHORT, self._baseline_urls())

    def test_official_mention_slice_does_not_vouch_for_its_host(self):
        # The mention URL canonicalizes onto the host URL, so without the guard
        # a mention slice would fold a host page whose own body failed.
        run = self._run(delta=[NEW_FETCHED],
                        fulltext=[{"url": NEW_FETCHED + "#B001277", "body": _body(80),
                                   "body_word_count": 80, "source": "OFFICIAL_MENTION"}])
        payload = self._fold(run)
        self.assertEqual(payload["rows_added"], 0)
        self.assertNotIn(NEW_FETCHED, self._baseline_urls())

    def test_fragment_url_is_rejected_even_without_a_source_column(self):
        path = os.path.join(self.tmp, "evidence_no_source.csv")
        _write_csv(path, ["url", "body_word_count"],
                   [{"url": NEW_FETCHED + "#B001277", "body_word_count": 80}])
        self.assertEqual(fetched_keys(path), set())

    def test_host_row_with_body_is_accepted(self):
        path = os.path.join(self.tmp, "evidence_host.csv")
        _write_csv(path, ["url", "body_word_count", "source"],
                   [{"url": NEW_FETCHED, "body_word_count": 80, "source": "HOST"}])
        self.assertEqual(fetched_keys(path), {index_key(NEW_FETCHED)})

    # ---- additivity and idempotency ------------------------------------

    def test_second_fold_adds_zero_rows_and_leaves_the_file_untouched(self):
        run = self._run()
        first = self._fold(run)
        self.assertEqual(first["rows_added"], 1)
        digest = _sha256(self.baseline)
        second = self._fold(run)
        self.assertEqual(second["rows_added"], 0)
        self.assertEqual(second["status"], "NO_OP")
        self.assertEqual(_sha256(self.baseline), digest)
        self.assertIsNone(second["backup_path"])

    def test_existing_rows_are_preserved_byte_for_byte(self):
        with io.open(self.baseline, "rb") as handle:
            before = handle.read()
        self._fold(self._run())
        with io.open(self.baseline, "rb") as handle:
            after = handle.read()
        self.assertTrue(after.startswith(before),
                        "the fold must append, never rewrite existing rows")

    def test_folded_row_carries_the_key_the_anti_join_will_match(self):
        run = self._run()
        self._fold(run)
        keys, rows = scan_baseline(self.baseline)
        self.assertEqual(rows, 3)
        # This is the property that actually makes the next run incremental.
        self.assertIn(index_key(NEW_FETCHED), keys)

    def test_duplicate_delta_rows_fold_once(self):
        run = self._run(delta=[NEW_FETCHED, NEW_FETCHED + "/"],
                        fulltext=[{"url": NEW_FETCHED, "body": _body(50),
                                   "body_word_count": 50, "source": "HOST"}])
        payload = self._fold(run)
        self.assertEqual(payload["rows_added"], 1)

    def test_row_already_in_the_baseline_is_not_re_added(self):
        run = self._run(delta=[BASE_A],
                        fulltext=[{"url": BASE_A, "body": _body(50),
                                   "body_word_count": 50, "source": "HOST"}])
        payload = self._fold(run)
        self.assertEqual(payload["rows_added"], 0)
        self.assertEqual(payload["rows_after"], 2)

    # ---- fail-closed refusals ------------------------------------------

    def test_schema_mismatch_refuses_without_touching_the_baseline(self):
        bad_fields = ("title", "url", "date", "SOURCE_OF_TITLE", "bioguide",
                      "member", "chamber", "state", "party")
        _write_baseline(self.baseline, [BASE_A], fields=bad_fields)
        with io.open(self.baseline, "rb") as handle:
            before = handle.read()
        with self.assertRaises(FoldRefused):
            self._fold(self._run())
        with io.open(self.baseline, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_row_count_decrease_refuses_and_restores_from_backup(self):
        with io.open(self.baseline, "rb") as handle:
            before = handle.read()
        _keys, rows_before = scan_baseline(self.baseline)
        truncated = os.path.join(self.tmp, "truncated.foldtmp")
        _write_baseline(truncated, [])          # header only: a lost-rows write
        backup = self.baseline + ".20260830T000000Z.foldbak"
        with self.assertRaises(FoldRefused):
            promote_baseline(self.baseline, truncated, backup, rows_before)
        with io.open(self.baseline, "rb") as handle:
            self.assertEqual(handle.read(), before, "baseline must be restored")

    def test_unsealed_run_refuses_by_default(self):
        run = self._run(seal=False)
        with self.assertRaises(FoldRefused):
            self._fold(run)
        self.assertEqual(len(self._baseline_urls()), 2)

    def test_unsealed_run_may_be_bootstrapped_but_still_needs_stored_bodies(self):
        run = self._run(seal=False, delta=[NEW_FETCHED, NEW_UNFETCHED],
                        fulltext=[{"url": NEW_FETCHED, "body": _body(50),
                                   "body_word_count": 50, "source": "HOST"}])
        payload = self._fold(run, require_receipt=False)
        self.assertEqual(payload["rows_added"], 1)
        self.assertFalse(payload["receipt_validated"])
        self.assertNotIn(NEW_UNFETCHED, self._baseline_urls())

    def test_missing_evidence_refuses(self):
        run = self._run()
        os.remove(os.path.join(run, "press_delta_fulltext.csv"))
        with self.assertRaises(FoldRefused):
            self._fold(run, require_receipt=False)

    # ---- receipt, backups, and the pending sweep -------------------------

    def test_receipt_records_the_fold(self):
        run = self._run()
        payload = self._fold(run)
        with io.open(self.receipt, encoding="utf-8") as handle:
            stored = json.loads(handle.read())
        for field in ("schema", "status", "rows_before", "rows_after", "rows_added",
                      "source_run_root", "source_receipt_sha256", "backup_path",
                      "fetched_evidence", "index_source", "history"):
            self.assertIn(field, stored)
        self.assertEqual(stored["schema"], "press-baseline-fold-v1")
        self.assertEqual(stored["rows_before"], 2)
        self.assertEqual(stored["rows_after"], 3)
        self.assertTrue(os.path.isfile(payload["backup_path"]))
        self.assertEqual(stored["history"][-1]["source_run"], os.path.basename(run))

    def test_backup_retention_keeps_the_last_n(self):
        for index in range(4):
            url = "https://www.zeta%d.senate.gov/news/x" % index
            run = self._run(name="press_refresh_2026083%d" % index, delta=[url],
                            fulltext=[{"url": url, "body": _body(30),
                                       "body_word_count": 30, "source": "HOST"}])
            self._fold(run, keep_backups=2)
        backups = [name for name in os.listdir(self.tmp) if name.endswith(".foldbak")]
        self.assertEqual(len(backups), 2, backups)

    def test_fold_pending_folds_each_run_once(self):
        self._run(name="press_refresh_20260830")
        url = "https://www.eta.senate.gov/news/two"
        _make_run(os.path.join(self.outputs, "press_refresh_20260831"), [url],
                  [{"url": url, "body": _body(40), "body_word_count": 40,
                    "source": "HOST"}])
        first = fold_pending(outputs_root=self.outputs, baseline_path=self.baseline,
                             receipt_path=self.receipt)
        self.assertEqual(sum(item["rows_added"] for item in first), 2)
        second = fold_pending(outputs_root=self.outputs, baseline_path=self.baseline,
                              receipt_path=self.receipt)
        self.assertEqual(second, [], "a folded run must not be folded again")
        self.assertEqual(len(self._baseline_urls()), 4)

    def test_fold_pending_ignores_a_run_with_no_valid_receipt(self):
        self._run(name="press_refresh_20260829", seal=False)
        self.assertEqual(
            fold_pending(outputs_root=self.outputs, baseline_path=self.baseline,
                         receipt_path=self.receipt), [])
        self.assertEqual(len(self._baseline_urls()), 2)

    # ---- R equivalence ---------------------------------------------------

    def test_index_key_matches_r_canonical_url(self):
        for source, expected in R_CANONICAL_URL_CASES:
            self.assertEqual(index_key(source), expected, source)

    def test_index_key_is_not_the_press_stream_canonicalizer(self):
        # A real divergence, pinned so a future "unify these" refactor fails loudly.
        from press_stream import canonical_url as stream_canonical
        self.assertNotEqual(index_key(BASE_A), stream_canonical(BASE_A))


if __name__ == "__main__":
    unittest.main(verbosity=2)

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
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(FEC)
sys.path.insert(0, FEC)

from collect import fold_promoted_press_runs, press_baseline_fold  # noqa: E402
from collect.build_press_engine_feed import ReceiptRejected  # noqa: E402
from collect.press_baseline_fold import (  # noqa: E402
    BASELINE_FIELDS, FoldRefused, RunInputRefused, fetched_keys, fold_pending, fold_run,
    folded_run_names, index_key, promote_baseline, record_skipped_uninspectable,
    scan_baseline,
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


class FoldReceiptLedgerTest(unittest.TestCase):
    """The receipt is the only record of which runs are folded: it must not lose or misread any."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="press_fold_ledger_")
        self.baseline = os.path.join(self.tmp, "all_press_release_index.csv")
        self.receipt = os.path.join(self.tmp, "press_baseline_fold_receipt.json")
        self.outputs = os.path.join(self.tmp, "outputs")
        os.makedirs(self.outputs, exist_ok=True)
        _write_baseline(self.baseline, [BASE_A, BASE_B])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, name="press_refresh_20260830", delta=None, fulltext=None):
        return _make_run(os.path.join(self.outputs, name),
                         delta if delta is not None else [NEW_FETCHED],
                         fulltext if fulltext is not None else [
                             {"url": NEW_FETCHED, "body": _body(50),
                              "body_word_count": 50, "source": "HOST"}])

    def _pending(self):
        return fold_pending(outputs_root=self.outputs, baseline_path=self.baseline,
                            receipt_path=self.receipt)

    def _fold_one(self, run):
        return fold_run(run, baseline_path=self.baseline, receipt_path=self.receipt)

    def _stored(self):
        with io.open(self.receipt, encoding="utf-8") as handle:
            return json.load(handle)

    def test_done_list_survives_a_history_longer_than_the_cap(self):
        for index in range(4):
            url = "https://www.theta%d.senate.gov/news/x" % index
            self._run(name="press_refresh_2026083%d" % index, delta=[url],
                      fulltext=[{"url": url, "body": _body(30),
                                 "body_word_count": 30, "source": "HOST"}])
        with mock.patch.object(press_baseline_fold, "HISTORY_LIMIT", 2):
            self.assertEqual(len(self._pending()), 4)
        stored = self._stored()
        self.assertEqual(len(stored["history"]), 2, "history stays capped")
        self.assertEqual(stored["folded_runs"], sorted(
            "press_refresh_2026083%d" % index for index in range(4)))
        self.assertEqual(len(folded_run_names(self.receipt)), 4)
        # NEGATIVE CONTROL: with only the capped history, the two oldest runs would fold again.
        self.assertEqual(self._pending(), [])

    def test_a_complete_no_op_counts_as_done(self):
        self._run(delta=[BASE_A], fulltext=[{"url": BASE_A, "body": _body(50),
                                             "body_word_count": 50, "source": "HOST"}])
        first = self._pending()
        self.assertEqual([item["status"] for item in first], ["NO_OP"])
        self.assertEqual(folded_run_names(self.receipt), {"press_refresh_20260830"})
        self.assertEqual(self._pending(), [], "a finished no-op must not be rescanned")

    def test_a_no_op_with_unstored_candidates_is_examined_again(self):
        self._run(delta=[NEW_UNFETCHED])           # its body has not landed yet
        first = self._pending()
        self.assertEqual(first[0]["status"], "NO_OP")
        self.assertEqual(first[0]["candidates_without_stored_body"], 1)
        self.assertEqual(folded_run_names(self.receipt), set())
        self.assertEqual(len(self._pending()), 1)

    def test_a_crash_between_replace_and_receipt_is_healed_as_done(self):
        self._run()
        self.assertEqual(len(self._pending()), 1)
        os.remove(self.receipt)                    # the baseline was replaced, the receipt was lost
        again = self._pending()
        self.assertEqual([item["status"] for item in again], ["NO_OP"])
        self.assertEqual(again[0]["rows_added"], 0)
        self.assertEqual(self._pending(), [])

    def test_a_receipt_written_before_folded_runs_existed_is_still_honoured(self):
        with io.open(self.receipt, "w", encoding="utf-8") as handle:
            json.dump({"schema": "press-baseline-fold-v1", "history": [
                {"source_run": "press_refresh_20260801", "status": "FOLDED"},
                {"source_run": "press_refresh_20260802", "status": "NO_OP"}]}, handle)
        self.assertEqual(folded_run_names(self.receipt), {"press_refresh_20260801"})
        self._fold_one(self._run(name="press_refresh_20260803"))
        self.assertEqual(self._stored()["folded_runs"],
                         ["press_refresh_20260801", "press_refresh_20260803"])

    def test_skipped_uninspectable_survives_a_fold(self):
        record_skipped_uninspectable(self.receipt, [
            {"source_run": "press_refresh_20260101", "reason": "MISSING_RUN_FILES",
             "detail": "gone"}])
        before = self._stored()
        self._fold_one(self._run())
        after = self._stored()
        self.assertEqual(after["status"], "FOLDED")
        self.assertEqual(after["skipped_uninspectable"], before["skipped_uninspectable"])
        self.assertEqual(after["skipped_uninspectable_at"], before["skipped_uninspectable_at"])

    def test_a_receipt_that_is_not_an_object_refuses(self):
        for text in ("[]", '"x"', "null"):
            with io.open(self.receipt, "w", encoding="utf-8") as handle:
                handle.write(text)
            with self.subTest(text=text):
                with self.assertRaises(FoldRefused):
                    folded_run_names(self.receipt)
                with self.assertRaises(FoldRefused):
                    self._pending()

    def test_an_infinite_word_count_falls_back_to_the_body(self):
        path = os.path.join(self.tmp, "evidence_inf.csv")
        _write_csv(path, ["url", "body", "body_word_count"], [
            {"url": NEW_FETCHED, "body": _body(30), "body_word_count": "inf"},
            {"url": NEW_UNFETCHED, "body": _body(3), "body_word_count": "inf"}])
        self.assertEqual(fetched_keys(path), {index_key(NEW_FETCHED)})

    def test_a_verdict_the_caller_established_replaces_valid_run(self):
        run = _make_run(os.path.join(self.outputs, "press_refresh_20260830"), [NEW_FETCHED],
                        [{"url": NEW_FETCHED, "body": _body(50), "body_word_count": 50,
                          "source": "HOST"}], seal=False)
        completed = datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc)
        with mock.patch.object(press_baseline_fold, "valid_run",
                               side_effect=AssertionError("valid_run must not be asked")):
            payload = fold_run(run, baseline_path=self.baseline, receipt_path=self.receipt,
                               verdict=(completed, {}))
        self.assertEqual(payload["rows_added"], 1)
        self.assertTrue(payload["receipt_validated"])
        self.assertEqual(payload["source_receipt_completed_at"], completed.isoformat())


class PromotedFoldTest(unittest.TestCase):
    """fold_promoted: the intake receipt vouches for a run today's streaks would refuse."""

    PENDING = ReceiptRejected("PENDING_BODY_ATTEMPTS", "streak reset by a drained member")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="press_fold_promoted_")
        self.baseline = os.path.join(self.tmp, "all_press_release_index.csv")
        self.fold_receipt = os.path.join(self.tmp, "fold.json")
        self.outputs = os.path.join(self.tmp, "outputs")
        os.makedirs(self.outputs, exist_ok=True)
        _write_baseline(self.baseline, [BASE_A, BASE_B])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, name, url):
        return _make_run(os.path.join(self.outputs, name), [url],
                         [{"url": url, "body": _body(50), "body_word_count": 50,
                           "source": "HOST"}])

    def _feed(self, runs, intake_status="ACCEPTED"):
        outputs = {}
        for name in ("tone", "fulltext", "mentions"):
            path = os.path.join(self.tmp, "%s.csv" % name)
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write("url\n")
            outputs[name] = {"path": path, "sha256": _sha256(path)}
        accepted = []
        for root in runs:
            intake_path = os.path.join(self.tmp, "intake_%s.json" % os.path.basename(root))
            with io.open(intake_path, "w", encoding="utf-8") as handle:
                json.dump({"schema": "collection-intake-receipt-v1", "status": intake_status,
                           "inputs": {"press_retry_receipt_sha256": _sha256(
                               os.path.join(root, "press_network_retry_receipt.json"))}}, handle)
            accepted.append({"source_run": root, "collection_intake": {
                "path": intake_path, "sha256": _sha256(intake_path)}})
        return self._write_feed(outputs, accepted)

    def _write_feed(self, outputs, accepted):
        feed = os.path.join(self.tmp, "feed.json")
        with io.open(feed, "w", encoding="utf-8") as handle:
            json.dump({"schema": "press-refresh-engine-feed-v1",
                       "status": "ACTIVE_TYPED_SUPPLEMENT",
                       "promotion_mode": "CUMULATIVE_ACCEPTED_RUN_LEDGER",
                       "outputs": outputs, "accepted_runs": accepted}, handle)
        return feed

    def _baseline_urls(self):
        with io.open(self.baseline, encoding="utf-8-sig", newline="") as handle:
            return [row["url"] for row in csv.DictReader(handle)]

    def _rewrite_retry(self, root, **changes):
        path = os.path.join(root, "press_network_retry_receipt.json")
        with io.open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        document.update(changes)
        with io.open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

    def test_a_promoted_partial_run_folds_on_the_intake_receipt(self):
        run = self._run("press_refresh_20260830", NEW_FETCHED)
        feed = self._feed([run])
        skipped = []
        with mock.patch.object(fold_promoted_press_runs, "inspect_run",
                               side_effect=self.PENDING), \
                mock.patch.object(press_baseline_fold, "valid_run",
                                  side_effect=AssertionError("fold must rest on the intake")):
            results = fold_promoted_press_runs.fold_promoted(
                feed, self.baseline, self.fold_receipt, skipped=skipped)
        self.assertEqual(skipped, [])
        self.assertEqual([item["rows_added"] for item in results], [1])
        self.assertTrue(results[0]["receipt_validated"])
        self.assertIn(NEW_FETCHED, self._baseline_urls())

    def test_the_fallback_refuses_what_the_intake_does_not_vouch_for(self):
        def intake_not_accepted(run):
            return self._feed([run], intake_status="REJECTED")

        def retry_changed_after_intake(run):
            feed = self._feed([run])
            self._rewrite_retry(run, note="edited after the intake")
            return feed

        def retry_not_live(run):
            self._rewrite_retry(run, mode="historical_body_recovery")
            return self._feed([run])

        def completed_at_unparsable(run):
            self._rewrite_retry(run, completed_at="sometime")
            return self._feed([run])

        def fulltext_hash_wrong(run):
            self._rewrite_retry(run, fulltext_csv_sha256="0" * 64)
            return self._feed([run])

        cases = (intake_not_accepted, retry_changed_after_intake, retry_not_live,
                 completed_at_unparsable, fulltext_hash_wrong)
        for number, case in enumerate(cases):
            run = self._run("press_refresh_2026090%d" % number, NEW_FETCHED)
            feed = case(run)
            skipped = []
            with self.subTest(case=case.__name__), mock.patch.object(
                    fold_promoted_press_runs, "inspect_run", side_effect=self.PENDING):
                self.assertEqual(fold_promoted_press_runs.fold_promoted(
                    feed, self.baseline, self.fold_receipt, skipped=skipped), [])
                self.assertEqual([item["reason"] for item in skipped],
                                 ["PENDING_BODY_ATTEMPTS"])
                with self.assertRaises(RuntimeError):
                    fold_promoted_press_runs.promoted_roots(feed)
        self.assertEqual(self._baseline_urls(), [BASE_A, BASE_B])

    def test_another_rejection_reason_never_reaches_the_fallback(self):
        run = self._run("press_refresh_20260830", NEW_FETCHED)
        feed = self._feed([run])
        skipped = []
        with mock.patch.object(fold_promoted_press_runs, "inspect_run",
                               side_effect=ReceiptRejected("FULLTEXT_CSV_SHA_MISMATCH", "x")):
            self.assertEqual(fold_promoted_press_runs.fold_promoted(
                feed, self.baseline, self.fold_receipt, skipped=skipped), [])
        self.assertEqual([item["reason"] for item in skipped], ["FULLTEXT_CSV_SHA_MISMATCH"])

    def test_a_root_without_its_index_delta_is_skipped_and_the_next_still_folds(self):
        first = self._run("press_refresh_20260830", NEW_FETCHED)
        os.remove(os.path.join(first, "press_url_delta.csv"))
        second_url = "https://www.kappa.senate.gov/news/two"
        second = self._run("press_refresh_20260831", second_url)
        feed = self._feed([first, second])
        skipped = []
        results = fold_promoted_press_runs.fold_promoted(
            feed, self.baseline, self.fold_receipt, skipped=skipped)
        self.assertEqual([item["source_run"] for item in results], ["press_refresh_20260831"])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["source_run"], "press_refresh_20260830")
        self.assertEqual(skipped[0]["reason"], "FOLD_REFUSED")
        self.assertIn("index delta not found", skipped[0]["detail"])
        self.assertFalse(skipped[0]["already_folded"])
        self.assertIn(second_url, self._baseline_urls())
        self.assertNotIn(NEW_FETCHED, self._baseline_urls())
        with self.assertRaises(FoldRefused):          # no collector -> old all-or-nothing
            fold_promoted_press_runs.fold_promoted(feed, self.baseline, self.fold_receipt)

    def test_a_baseline_refusal_is_an_alarm_not_a_per_root_skip(self):
        # Only a refusal about the RUN's own inputs may be skipped; one about the baseline or the fold
        # receipt must stop the sweep, even with a collector, so later roots never fold into a
        # baseline that just failed its integrity check.
        run = self._run("press_refresh_20260830", NEW_FETCHED)
        feed = self._feed([run])

        def missing():
            os.remove(self.baseline)

        def empty():
            io.open(self.baseline, "w").close()

        def schema_drift():
            _write_csv(self.baseline, ["url", "title"], [{"url": BASE_A, "title": "t"}])

        def unreadable_fold_receipt():
            with io.open(self.fold_receipt, "w", encoding="utf-8") as handle:
                handle.write("{truncated")

        for damage in (missing, empty, schema_drift, unreadable_fold_receipt):
            with self.subTest(damage=damage.__name__):
                _write_baseline(self.baseline, [BASE_A, BASE_B])
                if os.path.exists(self.fold_receipt):
                    os.remove(self.fold_receipt)
                damage()
                skipped = []
                with self.assertRaises(FoldRefused) as caught:
                    fold_promoted_press_runs.fold_promoted(
                        feed, self.baseline, self.fold_receipt, skipped=skipped)
                self.assertNotIsInstance(caught.exception, RunInputRefused)
                self.assertEqual(skipped, [])
                with self.assertRaises(FoldRefused):
                    fold_promoted_press_runs.fold_promoted(
                        feed, self.baseline, self.fold_receipt)

    def test_a_malformed_ledger_entry_is_a_named_error(self):
        feed = self._feed([])
        with io.open(feed, encoding="utf-8") as handle:
            document = json.load(handle)
        document["accepted_runs"] = ["press_refresh_20260829"]
        with io.open(feed, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with self.assertRaises(RuntimeError) as caught:
            fold_promoted_press_runs.promoted_roots(feed, [])
        self.assertIn("malformed entry", str(caught.exception))

    def test_a_duplicate_root_is_caught_even_when_the_first_was_skipped(self):
        feed = self._feed([])
        missing = os.path.join(self.outputs, "press_refresh_20260829")
        with io.open(feed, encoding="utf-8") as handle:
            document = json.load(handle)
        document["accepted_runs"] = [{"source_run": missing}, {"source_run": missing}]
        with io.open(feed, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with self.assertRaises(RuntimeError) as caught:
            fold_promoted_press_runs.promoted_roots(feed, [])
        self.assertIn("duplicate source run", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

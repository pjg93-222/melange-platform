# -*- coding: utf-8 -*-
"""Contract tests for the deferred-body tombstone ledger and the gate it relaxes.

This is the only thing in the press lane that makes an evidence gate MORE
permissive, so every test below is a negative control on that permission:

  * a receipt that is not the walk's own PARTIAL shape is refused
  * a pending artifact whose hash differs from the receipt is refused
  * ONE row short of tombstoned refuses the whole run
  * a row count that disagrees with the receipt is refused
  * a row whose member/URL identity is unusable is never tombstoned
  * the complete-walk path is unchanged: PASS with rows still refuses

and on the ledger's own arithmetic: three CONSECUTIVE runs, a streak that a
single absence resets, and an update() that is safe to run repeatedly.

The R boundary is never crossed.  Runs are directories carrying the artifacts
the walk seals, exactly as test_press_baseline_fold.py builds them.
"""
from __future__ import annotations

import ast
import datetime
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from collect import press_pending_tombstones as tombstones  # noqa: E402
from collect.build_press_engine_feed import (  # noqa: E402
    ReceiptRejected, atomic_json, discover_run, validate_body_completion,
)
from collect.fold_promoted_press_runs import fold_promoted, promoted_roots  # noqa: E402
from collect.press_pending_tombstones import (  # noqa: E402
    CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE, PendingArtifactError, load,
    pending_is_tombstoned_only, record_key, tombstoned_keys, tombstoned_members, update,
)

PENDING_HEADER = "bioguide,url,date,evidence_key,coverage_status,collection_status\n"
URL_A = "https://huffman.house.gov/news/a"
URL_B = "https://huffman.house.gov/news/b"


def _sha256(path):
    digest = hashlib.sha256()
    with io.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_pending(run_root, pairs, header=PENDING_HEADER, seal="PARTIAL"):
    """One run's pending artifact, in R write.csv shape (quoted, bare date).

    ``seal`` writes the retry receipt that makes the artifact count as evidence:
    ``None`` leaves the run unsealed, ``"BROKEN_SHA"`` seals it with a hash that
    does not match, and any status string seals it with that status.
    """
    os.makedirs(run_root, exist_ok=True)
    lines = [header]
    for bioguide, url in pairs:
        lines.append('"%s","%s",NA,"ev","blocking_current_uncertainty",'
                     '"body_pending_network_attempt"\n' % (bioguide, url))
    path = os.path.join(run_root, "press_delta_pending.csv")
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(lines))
    if seal is not None:
        broken = seal == "BROKEN_SHA"
        atomic_json(os.path.join(run_root, "press_network_retry_receipt.json"), {
            "mode": "live_network_retry",
            "status": "PARTIAL" if broken else seal,
            "pending_body_urls": len(pairs),
            "pending_body_urls_sha256": "0" * 64 if broken else _sha256(path),
        })
    return path


class TombstoneLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="press-tombstones-")
        self.outputs = os.path.join(self.tmp, "outputs")
        os.makedirs(self.outputs)
        self.ledger = os.path.join(self.tmp, "tombstones.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, name, pairs):
        return _write_pending(os.path.join(self.outputs, name), pairs)

    def test_a_url_is_tombstoned_only_on_the_third_consecutive_run(self):
        for index, name in enumerate(("press_refresh_20260901", "press_refresh_20260902",
                                      "press_refresh_20260903")):
            self._run(name, [("H001068", URL_A)])
            document = update(self.outputs, self.ledger)
            record = document["records"][record_key("H001068", URL_A)]
            self.assertEqual(record["consecutive_deferrals"], index + 1)
            self.assertEqual(bool(record["tombstoned_at"]),
                             index + 1 >= CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE)
        self.assertEqual(len(tombstoned_keys(document)), 1)
        self.assertEqual(record["first_seen_run"], "press_refresh_20260901")
        self.assertEqual(record["last_seen_run"], "press_refresh_20260903")
        self.assertEqual(record["deferrals"], 3)
        # Derived from the run, not the clock, so a replay reproduces it byte for byte.
        self.assertEqual(record["tombstoned_at"], "2026-09-03T00:00:00+00:00")

    def test_an_absent_run_resets_the_streak_and_clears_the_tombstone(self):
        for name in ("press_refresh_20260901", "press_refresh_20260902",
                     "press_refresh_20260903"):
            self._run(name, [("H001068", URL_A)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(len(tombstoned_keys(document)), 1)
        # The body finally landed, so the walk stops listing it.
        self._run("press_refresh_20260904", [("H001068", URL_B)])
        document = update(self.outputs, self.ledger)
        record = document["records"][record_key("H001068", URL_A)]
        self.assertEqual(record["consecutive_deferrals"], 0)
        self.assertIsNone(record["tombstoned_at"])
        self.assertEqual(record["deferrals"], 3, "the lifetime count is history, not a streak")
        # And it must earn the tombstone again from scratch.
        for name in ("press_refresh_20260905", "press_refresh_20260906"):
            self._run(name, [("H001068", URL_A)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(document["records"][record_key("H001068", URL_A)]
                         ["consecutive_deferrals"], 2)
        self.assertEqual(tombstoned_keys(document), set())

    def test_update_is_idempotent_and_seeds_a_whole_history_at_once(self):
        names = ["press_refresh_2026090%d" % day for day in range(1, 9)]
        for name in names:
            self._run(name, [("H001068", URL_A), ("T000472", URL_B)])
        first = update(self.outputs, self.ledger)
        self.assertEqual(first["runs_seen"], names, "all eight seeded in run order")
        self.assertEqual(len(tombstoned_keys(first)), 2)
        stamp = os.path.getmtime(self.ledger)
        second = update(self.outputs, self.ledger)
        self.assertEqual(second["runs_seen"], names)
        self.assertEqual(os.path.getmtime(self.ledger), stamp,
                         "a second update with nothing new must not rewrite the ledger")

    # ---- only sealed evidence may enter the ledger -----------------------

    def test_an_unsealed_run_is_ignored_and_counted_once_it_seals(self):
        run = os.path.join(self.outputs, "press_refresh_20260901")
        _write_pending(run, [("H001068", URL_A)], seal=None)
        self.assertEqual(update(self.outputs, self.ledger)["runs_seen"], [],
                         "a run still being written is not evidence of anything")
        _write_pending(run, [("H001068", URL_A)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(document["runs_seen"], ["press_refresh_20260901"])
        self.assertEqual(document["records"][record_key("H001068", URL_A)]
                         ["consecutive_deferrals"], 1)

    def test_a_crashed_walk_is_never_counted_as_a_deferral(self):
        # rc 2 writes no PASS/PARTIAL status.  A crash is not a budget deferral,
        # and the ledger is read-once, so counting it would be permanent.
        for index, status in enumerate(("FAILED", "ERROR", "", "PENDING")):
            run = os.path.join(self.outputs, "press_refresh_2026090%d" % (index + 1))
            _write_pending(run, [("H001068", URL_A)], seal=status)
        self.assertEqual(update(self.outputs, self.ledger)["runs_seen"], [])

    def test_a_pending_artifact_the_receipt_does_not_seal_is_never_counted(self):
        run = os.path.join(self.outputs, "press_refresh_20260901")
        _write_pending(run, [("H001068", URL_A)], seal="BROKEN_SHA")
        self.assertEqual(update(self.outputs, self.ledger)["runs_seen"], [])

    def test_a_pass_run_with_an_empty_artifact_resets_every_streak(self):
        for name in ("press_refresh_20260901", "press_refresh_20260902",
                     "press_refresh_20260903"):
            self._run(name, [("H001068", URL_A)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(len(tombstoned_keys(document)), 1)
        self.assertEqual(tombstoned_members(document), {"H001068"})
        _write_pending(os.path.join(self.outputs, "press_refresh_20260904"), [], seal="PASS")
        document = update(self.outputs, self.ledger)
        self.assertEqual(tombstoned_keys(document), set())
        self.assertEqual(tombstoned_members(document), set())

    # ---- member-level streaks --------------------------------------------

    def test_a_member_pending_three_consecutive_runs_is_tombstoned(self):
        for index, name in enumerate(("press_refresh_20260901", "press_refresh_20260902",
                                      "press_refresh_20260903")):
            # A DIFFERENT url each run: no URL ever earns its own streak.
            self._run(name, [("H001068", "%s-%d" % (URL_A, index))])
            document = update(self.outputs, self.ledger)
            member = document["members"]["H001068"]
            self.assertEqual(member["consecutive_runs_with_pending"], index + 1)
            self.assertEqual(member["pending_rows_last_run"], 1)
        self.assertEqual(tombstoned_keys(document), set(), "no URL qualifies on its own")
        self.assertEqual(tombstoned_members(document), {"H001068"})
        self.assertEqual(member["first_seen_run"], "press_refresh_20260901")
        self.assertEqual(member["tombstoned_at"], "2026-09-03T00:00:00+00:00")

    def test_a_member_that_drains_once_must_earn_the_claim_again(self):
        for name in ("press_refresh_20260901", "press_refresh_20260902",
                     "press_refresh_20260903"):
            self._run(name, [("H001068", URL_A), ("T000472", URL_B)])
        self.assertEqual(tombstoned_members(update(self.outputs, self.ledger)),
                         {"H001068", "T000472"})
        self._run("press_refresh_20260904", [("H001068", URL_A)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(tombstoned_members(document), {"H001068"})
        drained = document["members"]["T000472"]
        self.assertEqual(drained["consecutive_runs_with_pending"], 0)
        self.assertEqual(drained["pending_rows_last_run"], 0)
        self.assertIsNone(drained["tombstoned_at"])

    def test_a_listing_that_never_offered_the_backlog_does_not_reset_it(self):
        # 2026-09-27: H001068's listing collapsed 1,732 -> 6 rows, its 798
        # pending URLs vanished unfetched, and the old fold read that as a drain.
        def index(name, pairs):
            with io.open(os.path.join(self.outputs, name, "press_refreshed_index.csv"), "w",
                         encoding="utf-8", newline="") as handle:
                handle.write("".join("%s,%s\n" % p for p in [("bioguide", "url")] + pairs))
        for name in ("press_refresh_20260901", "press_refresh_20260902"):
            self._run(name, [("H001068", URL_A), ("T000472", URL_B)])
            index(name, [("H001068", URL_A), ("T000472", URL_B)])
        # T000472 is listed and not pending (a real drain); H001068 is not listed at all.
        self._run("press_refresh_20260903", [])
        index("press_refresh_20260903", [("T000472", URL_B)])
        self._run("press_refresh_20260904", [("H001068", URL_A)])
        index("press_refresh_20260904", [("H001068", URL_A), ("T000472", URL_B)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(document["members"]["H001068"]["consecutive_runs_with_pending"], 3)
        self.assertEqual(tombstoned_members(document), {"H001068"})
        self.assertEqual(tombstoned_keys(document), {record_key("H001068", URL_A)})
        self.assertEqual(document["members"]["T000472"]["consecutive_runs_with_pending"], 0)
        self.assertEqual(document["records"][record_key("T000472", URL_B)]
                         ["consecutive_deferrals"], 0)

    def test_a_v1_ledger_is_rebuilt_rather_than_refused(self):
        # v1 has no member history, so trusting it would under-count; refusing
        # it would deadlock the gate behind a file nobody knows to delete.
        atomic_json(self.ledger, {"schema": "press-pending-tombstone-v1",
                                  "runs_seen": ["press_refresh_20260901"], "records": {}})
        self._run("press_refresh_20260901", [("H001068", URL_A)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(document["schema"], tombstones.TOMBSTONE_SCHEMA)
        self.assertEqual(document["runs_seen"], ["press_refresh_20260901"])
        self.assertEqual(document["members"]["H001068"]["consecutive_runs_with_pending"], 1)

    def test_a_fixture_polluted_v3_ledger_replays_every_sealed_run(self):
        # 2026-10-11: a pytest fixture run named press_refresh_20260913 was
        # folded into the live v3 ledger, so the real 09-13 run never was.
        atomic_json(self.ledger, {
            "schema": "press-pending-tombstone-v3", "runs_seen": ["press_refresh_20260913"],
            "records": {}, "members": {"X000001": {
                "bioguide": "X000001", "consecutive_runs_with_pending": 1,
                "pending_rows_last_run": 1, "tombstoned_at": None}}})
        for name in ("press_refresh_20260913", "press_refresh_20260920"):
            self._run(name, [("H001068", URL_A)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(document["runs_seen"],
                         ["press_refresh_20260913", "press_refresh_20260920"])
        self.assertNotIn("X000001", document["members"])
        self.assertEqual(document["members"]["H001068"]["consecutive_runs_with_pending"], 2)

    def test_a_run_resealed_after_a_drain_is_replayed_not_kept_stale(self):
        # 2026-10-11 was folded, then drained and re-sealed without T000472.
        for name in ("press_refresh_20260901", "press_refresh_20260902"):
            self._run(name, [("T000472", URL_B)])
        self._run("press_refresh_20260903", [("T000472", URL_B), ("H001068", URL_A)])
        self.assertEqual(tombstoned_members(update(self.outputs, self.ledger)), {"T000472"})
        self._run("press_refresh_20260903", [("H001068", URL_A)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(document["members"]["T000472"]["consecutive_runs_with_pending"], 0)
        self.assertEqual(tombstoned_members(document), set())

    def test_the_live_ledger_refuses_a_foreign_outputs_root(self):
        # ROOT is redirected so the "live" ledger is a tmp file even on failure.
        saved = tombstones.ROOT
        tombstones.ROOT = self.tmp
        try:
            live = os.path.join(self.tmp, "data", "engine", "press_pending_tombstones.json")
            foreign = os.path.join(self.tmp, "pytest_tmp")
            _write_pending(os.path.join(foreign, "press_refresh_20260913"), [("X000001", URL_A)])
            with self.assertRaises(PendingArtifactError):
                update(foreign, live)
            self.assertFalse(os.path.exists(live))
            self._run("press_refresh_20260913", [("H001068", URL_A)])
            self.assertEqual(update(self.outputs, live)["runs_seen"], ["press_refresh_20260913"])
        finally:
            tombstones.ROOT = saved

    def test_a_run_without_a_pending_artifact_is_skipped_not_recorded(self):
        # Runs before 2026-09-11 predate the artifact; a run still in flight has
        # not sealed it yet.  Recording either would hide its rows forever.
        os.makedirs(os.path.join(self.outputs, "press_refresh_20260828"))
        self._run("press_refresh_20260913", [("H001068", URL_A)])
        document = update(self.outputs, self.ledger)
        self.assertEqual(document["runs_seen"], ["press_refresh_20260913"])

    def test_an_unreadable_ledger_refuses_instead_of_resetting_the_counts(self):
        with io.open(self.ledger, "w", encoding="utf-8") as handle:
            handle.write("{truncated")
        with self.assertRaises(PendingArtifactError):
            load(self.ledger)

    def test_a_pending_artifact_missing_coverage_columns_is_skipped(self):
        # Corrupt is corrupt: it cannot be counted, and it must not be recorded
        # as seen either.  The gate then refuses on its un-excused rows.
        run = os.path.join(self.outputs, "press_refresh_20260901")
        _write_pending(run, [("H001068", URL_A)], header="bioguide,url,date\n")
        self.assertEqual(update(self.outputs, self.ledger)["runs_seen"], [])
        with self.assertRaises(PendingArtifactError):
            tombstones.read_pending(run)


class CompletionGateTests(unittest.TestCase):
    """The gate itself: what validate_body_completion now accepts, and what it will not."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="press-gate-")
        self.outputs = os.path.join(self.tmp, "outputs")
        os.makedirs(self.outputs)
        self.ledger = os.path.join(self.tmp, "tombstones.json")
        self._default = tombstones.DEFAULT_TOMBSTONES
        tombstones.DEFAULT_TOMBSTONES = self.ledger

    def tearDown(self):
        tombstones.DEFAULT_TOMBSTONES = self._default
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _history(self, pairs, runs=("press_refresh_20260901", "press_refresh_20260902")):
        for name in runs:
            _write_pending(os.path.join(self.outputs, name), pairs)

    def _target(self, pairs, name="press_refresh_20260903"):
        run = os.path.join(self.outputs, name)
        path = _write_pending(run, pairs)
        return run, {"status": "PARTIAL", "pending_body_urls": len(pairs),
                     "pending_body_urls_sha256": _sha256(path)}

    def test_a_fully_tombstoned_partial_run_is_accepted_and_counted(self):
        pairs = [("H001068", URL_A), ("T000472", URL_B)]
        self._history(pairs)
        run, receipt = self._target(pairs)
        proof = validate_body_completion(run, receipt)
        self.assertEqual(proof["pending_body_urls_tombstoned"], 2)
        self.assertEqual(proof["excused_by_url"], 2)
        self.assertEqual(proof["excused_by_member"], 0)
        self.assertEqual(proof["consecutive_deferrals_for_tombstone"],
                         CONSECUTIVE_DEFERRALS_FOR_TOMBSTONE)

    def test_a_stuck_member_that_keeps_publishing_is_still_accepted(self):
        # The whole point of the member clause: H001068 publishes every run, so
        # each run brings a NEW undated URL that starts its own streak at 1.  A
        # pure per-URL rule would refuse forever while the member keeps posting.
        for index, name in enumerate(("press_refresh_20260901", "press_refresh_20260902")):
            _write_pending(os.path.join(self.outputs, name),
                           [("H001068", "%s-%d" % (URL_A, index))])
        run, receipt = self._target([("H001068", URL_A + "-fresh")])
        proof = validate_body_completion(run, receipt)
        self.assertEqual(proof["excused_by_url"], 0)
        self.assertEqual(proof["excused_by_member"], 1)
        self.assertEqual(proof["tombstoned_members"], ["H001068"])

    def test_a_healthy_members_first_pending_url_still_refuses(self):
        # The member clause must not excuse a member nobody has seen stuck.
        for index, name in enumerate(("press_refresh_20260901", "press_refresh_20260902")):
            _write_pending(os.path.join(self.outputs, name),
                           [("H001068", "%s-%d" % (URL_A, index))])
        run, receipt = self._target([("H001068", URL_A + "-fresh"), ("A000001", URL_B)])
        with self.assertRaises(ReceiptRejected) as caught:
            validate_body_completion(run, receipt)
        self.assertEqual(caught.exception.reason, "PENDING_BODY_ATTEMPTS")
        self.assertIn("1 of 2", str(caught.exception))

    def test_one_row_short_of_tombstoned_refuses_the_whole_run(self):
        self._history([("H001068", URL_A)])
        run, receipt = self._target([("H001068", URL_A), ("T000472", URL_B)])
        with self.assertRaises(ReceiptRejected) as caught:
            validate_body_completion(run, receipt)
        self.assertEqual(caught.exception.reason, "PENDING_BODY_ATTEMPTS")
        self.assertIn("1 of 2", str(caught.exception))

    def test_two_consecutive_deferrals_are_not_enough(self):
        pairs = [("H001068", URL_A)]
        self._history(pairs, runs=("press_refresh_20260901",))
        run, receipt = self._target(pairs)
        with self.assertRaises(ReceiptRejected) as caught:
            validate_body_completion(run, receipt)
        self.assertEqual(caught.exception.reason, "PENDING_BODY_ATTEMPTS")

    def test_a_failed_walk_is_never_excused(self):
        # An rc-2 walk writes no PARTIAL status.  Only the walk's own budgeted
        # partial shape may reach the tombstone path at all.
        pairs = [("H001068", URL_A)]
        self._history(pairs)
        run, receipt = self._target(pairs)
        # "PASS" is in the list on purpose: a receipt claiming PASS while still
        # counting pending URLs is inconsistent, and must not buy the relaxation.
        for status in ("FAILED", "ERROR", None, "PASS"):
            broken = dict(receipt, status=status)
            with self.assertRaises(ReceiptRejected) as caught:
                validate_body_completion(run, broken)
            self.assertEqual(caught.exception.reason, "PENDING_BODY_ATTEMPTS", status)

    def test_a_pending_hash_mismatch_refuses_even_when_every_row_is_tombstoned(self):
        pairs = [("H001068", URL_A)]
        self._history(pairs)
        run, receipt = self._target(pairs)
        broken = dict(receipt, pending_body_urls_sha256="0" * 64)
        with self.assertRaises(ReceiptRejected) as caught:
            validate_body_completion(run, broken)
        self.assertEqual(caught.exception.reason, "PENDING_BODY_PROOF_MISMATCH")

    def test_a_row_count_that_disagrees_with_the_receipt_refuses(self):
        pairs = [("H001068", URL_A), ("T000472", URL_B)]
        self._history(pairs)
        run, receipt = self._target(pairs)
        broken = dict(receipt, pending_body_urls=1)
        with self.assertRaises(ReceiptRejected) as caught:
            validate_body_completion(run, broken)
        self.assertEqual(caught.exception.reason, "PENDING_BODY_ATTEMPTS")
        self.assertIn("receipt claims 1", str(caught.exception))

    def test_an_unkeyable_row_can_never_be_tombstoned(self):
        # No bioguide means no member owns the URL, so nothing can prove the
        # streak belongs to this row.  It must block, not pass unnoticed.
        pairs = [("", URL_A)]
        self._history(pairs)
        run, receipt = self._target(pairs)
        with self.assertRaises(ReceiptRejected) as caught:
            validate_body_completion(run, receipt)
        self.assertEqual(caught.exception.reason, "PENDING_BODY_ATTEMPTS")

    def test_the_complete_walk_path_is_unchanged(self):
        run = os.path.join(self.outputs, "press_refresh_20260903")
        path = _write_pending(run, [])
        receipt = {"status": "PASS", "pending_body_urls": 0,
                   "pending_body_urls_sha256": _sha256(path)}
        self.assertEqual(validate_body_completion(run, receipt), {})
        # ... and a PASS receipt whose artifact still holds rows still refuses.
        path = _write_pending(run, [("H001068", URL_A)])
        receipt = {"status": "PASS", "pending_body_urls": 0,
                   "pending_body_urls_sha256": _sha256(path)}
        with self.assertRaises(ReceiptRejected) as caught:
            validate_body_completion(run, receipt)
        self.assertEqual(caught.exception.reason, "PENDING_BODY_PROOF_MISMATCH")

    def test_a_legacy_receipt_with_neither_key_still_behaves_as_before(self):
        run = os.path.join(self.outputs, "press_refresh_20260903")
        os.makedirs(run, exist_ok=True)
        self.assertEqual(validate_body_completion(run, {"mode": "live_network_retry"}), {})
        with self.assertRaises(ReceiptRejected) as caught:
            validate_body_completion(run, {"mode": "live_network_retry"},
                                     require_complete=True)
        self.assertEqual(caught.exception.reason, "MISSING_BODY_COMPLETION_PROOF")

    def test_the_returned_proof_is_never_rebound_by_a_later_loop(self):
        # Found against the real 2026-09-13 run: the variable this function
        # returns was named `proof`, and the official-coverage block iterates
        # `for proof in coverage["members"]`.  Accept/refuse was still correct
        # (that travels by exception), but the RETURN carried a coverage proof,
        # so the intake receipt reported 0 tombstoned and no members -- exactly
        # the "looks clean" outcome the member counts exist to prevent.
        import ast
        import inspect
        from collect import build_press_engine_feed as feed
        tree = ast.parse(inspect.getsource(feed.validate_body_completion))
        function = tree.body[0]
        returned = {node.value.id for node in ast.walk(function)
                    if isinstance(node, ast.Return) and isinstance(node.value, ast.Name)}
        self.assertTrue(returned, "the completion proof must be returned by name")
        rebound = set()
        for node in ast.walk(function):
            targets = []
            if isinstance(node, (ast.For, ast.AsyncFor)):
                targets = [node.target]
            elif isinstance(node, ast.comprehension):
                targets = [node.target]
            for target in targets:
                rebound |= {name.id for name in ast.walk(target)
                            if isinstance(name, ast.Name)}
        self.assertEqual(returned & rebound, set(),
                         "a loop rebinds the name this function returns")

    def test_the_gate_reads_the_ledger_it_just_refreshed(self):
        # The third run is written AFTER the ledger was last persisted; the gate
        # must fold it in itself rather than decide on a stale file.
        pairs = [("H001068", URL_A)]
        self._history(pairs)
        update(self.outputs, self.ledger)
        self.assertEqual(tombstoned_keys(load(self.ledger)), set())
        run, receipt = self._target(pairs)
        self.assertEqual(validate_body_completion(run, receipt)
                         ["pending_body_urls_tombstoned"], 1)

    # ---- weekly_refresh._press_partial_excused: the hard step's one excused exit 3 ------------
    # The helper's REAL source is AST-extracted and exec'd with MB_ROOT = this temp dir;
    # weekly_refresh.py is never imported (its module level runs the whole refresh).

    def _arm(self):
        """The helper, plus the step's start time and the logs/emits it writes."""
        self.started = time.time() - 60
        self.today = datetime.date.fromtimestamp(self.started)
        self.logs, self.emits = [], []
        with io.open(os.path.join(FEC, "weekly_refresh.py"), encoding="utf-8") as handle:
            source = handle.read()
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == "_press_partial_excused")
        namespace = {
            "os": os, "datetime": datetime, "MB_ROOT": self.tmp, "log": self.logs.append,
            "_emit": lambda kind, status, summary, **kwargs: self.emits.append(
                (kind, status, summary, kwargs)),
        }
        exec(ast.get_source_segment(source, node), namespace)
        return namespace["_press_partial_excused"]

    def _day(self, offset):
        day = self.today + datetime.timedelta(days=offset)
        return "press_refresh_" + day.strftime("%Y%m%d"), day

    def _seal(self, offset, pairs, status="PARTIAL", completed=None, mode="live_network_retry"):
        """A run sealed the way the walk seals it: every required file plus a matching receipt.

        offset 0 is the step's own run, completed just now (after the step began); older runs
        complete at noon UTC of their own day so discover_run orders them as it would live.
        """
        name, day = self._day(offset)
        run = os.path.join(self.outputs, name)
        pending = _write_pending(run, pairs, seal=None)
        for artifact in ("press_delta_fulltext.csv", "press_delta_fulltext.rds",
                         "press_delta_host_articles.csv", "press_delta_article_mentions.csv",
                         "press_delta_scored.csv"):
            with io.open(os.path.join(run, artifact), "w", encoding="utf-8") as handle:
                handle.write("url\n")
        if completed is None:
            completed = (time.time() if offset == 0 else datetime.datetime(
                day.year, day.month, day.day, 12, tzinfo=datetime.timezone.utc).timestamp())
        atomic_json(os.path.join(run, "press_network_retry_receipt.json"), {
            "mode": mode, "status": status, "pending_body_urls": len(pairs),
            "pending_body_urls_sha256": _sha256(pending),
            "completed_at": datetime.datetime.fromtimestamp(
                completed, datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "fulltext_csv_sha256": _sha256(os.path.join(run, "press_delta_fulltext.csv")),
            "fulltext_rds_sha256": _sha256(os.path.join(run, "press_delta_fulltext.rds")),
        })
        return run

    def _history_and_today(self, pairs, **today):
        for offset in (-2, -1):
            self._seal(offset, pairs)
        return self._seal(0, pairs, **today)

    def _assert_refused(self, excused, fragment):
        self.assertIs(False, excused(3, self.started), self.logs)
        self.assertIn("press PARTIAL not excused", self.logs[-1])
        self.assertIn(fragment, self.logs[-1])
        self.assertEqual([], self.emits, "a refusal emits nothing")

    def test_weekly_excusal_accepts_a_run_whose_every_row_is_excused_by_url(self):
        excused = self._arm()
        self._history_and_today([("H001068", URL_A), ("T000472", URL_B)])
        self.assertIs(True, excused(3, self.started), self.logs)
        self.assertIn("press PARTIAL excused", self.logs[-1])
        self.assertIn("all 2 pending body URL(s)", self.logs[-1])
        self.assertEqual(("refresh.step", "partial"), self.emits[0][:2])
        self.assertEqual(2, self.emits[0][3]["metadata"]["pending_body_urls"])
        self.assertEqual(os.path.join(self.outputs, self._day(0)[0]),
                         self.emits[0][3]["metadata"]["run_root"])

    def test_weekly_excusal_accepts_a_run_whose_stuck_member_is_excused_by_member(self):
        excused = self._arm()
        for index, offset in enumerate((-2, -1)):
            self._seal(offset, [("H001068", "%s-%d" % (URL_A, index))])
        self._seal(0, [("H001068", URL_A + "-fresh")])
        self.assertIs(True, excused(3, self.started), self.logs)
        self.assertEqual(("refresh.step", "partial"), self.emits[0][:2])

    def test_weekly_excusal_refuses_a_run_with_one_unexcused_row(self):
        excused = self._arm()
        for offset in (-2, -1):
            self._seal(offset, [("H001068", URL_A)])
        self._seal(0, [("H001068", URL_A), ("M000312", URL_B)])
        self._assert_refused(excused, "PENDING_BODY_ATTEMPTS")

    def test_weekly_excusal_refuses_a_receipt_older_than_the_step(self):
        excused = self._arm()
        self._history_and_today([("H001068", URL_A)], completed=self.started - 3600)
        self._assert_refused(excused, "not a PARTIAL run sealed by this step")

    def test_weekly_excusal_ignores_every_exit_code_but_3(self):
        excused = self._arm()
        self._history_and_today([("H001068", URL_A)])      # would be excused on exit 3
        for returncode in (0, 1, 2, 124):
            self.assertIs(False, excused(returncode, self.started))
        self.assertEqual([], self.logs)
        self.assertEqual([], self.emits)
        self.assertFalse(os.path.exists(self.ledger), "not even the ledger is touched")

    def test_weekly_excusal_refuses_a_pass_receipt_even_on_exit_3(self):
        excused = self._arm()
        self._seal(0, [], status="PASS")
        self._assert_refused(excused, "not a PARTIAL run sealed by this step")

    def test_weekly_excusal_refuses_a_run_the_intake_would_skip_as_not_live(self):
        excused = self._arm()
        self._history_and_today([("H001068", URL_A)], mode="historical_body_recovery")
        self._assert_refused(excused, "MODE_NOT_LIVE_NETWORK_RETRY")

    def test_weekly_excusal_refuses_a_fulltext_hash_mismatch(self):
        excused = self._arm()
        run = self._history_and_today([("H001068", URL_A)])
        with io.open(os.path.join(run, "press_delta_fulltext.csv"), "a",
                     encoding="utf-8") as handle:
            handle.write("tampered\n")
        self._assert_refused(excused, "FULLTEXT_CSV_SHA_MISMATCH")

    def test_weekly_excusal_refuses_when_todays_run_root_is_missing(self):
        excused = self._arm()
        for offset in (-3, -2, -1):                    # older runs, all fully excused
            self._seal(offset, [("H001068", URL_A)])
        self._assert_refused(excused, "MISSING_RUN_FILES")

    def test_weekly_excusal_refuses_though_discover_run_would_audit_last_weeks_run(self):
        # M000312 is pending 14 and 7 days ago and again today, but today also carries a brand
        # new member at streak 1.  Today's run is invalid; the ledger replay then makes LAST
        # WEEK's run (M000312 only) valid retroactively, and discover_run -- what the press
        # intake audit uses -- hands the intake that older run.  The helper must not excuse
        # the exit 3: the audit would not be auditing this step's run.
        excused = self._arm()
        self._seal(-14, [("M000312", URL_A + "-w1")])
        self._seal(-7, [("M000312", URL_A + "-w2")])
        self._seal(0, [("M000312", URL_A + "-w3"), ("M001176", URL_B)])
        picked, _ = discover_run(self.outputs)
        self.assertEqual(self._day(-7)[0], os.path.basename(str(picked)))
        self._assert_refused(excused, "PENDING_BODY_ATTEMPTS")


class FoldSkipTests(unittest.TestCase):
    """A pruned promoted run must be named and stepped over, never fatal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="press-fold-skip-")
        self.outputs = os.path.join(self.tmp, "outputs")
        os.makedirs(self.outputs)
        self.feed = os.path.join(self.tmp, "feed.json")
        self.fold_receipt = os.path.join(self.tmp, "fold.json")
        self.missing = os.path.join(self.outputs, "press_refresh_20260828")
        outputs = {}
        for name in ("tone", "fulltext", "mentions"):
            path = os.path.join(self.tmp, "%s.csv" % name)
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write("url\n")
            outputs[name] = {"path": path, "sha256": _sha256(path)}
        atomic_json(self.feed, {
            "schema": "press-refresh-engine-feed-v1",
            "status": "ACTIVE_TYPED_SUPPLEMENT",
            "promotion_mode": "CUMULATIVE_ACCEPTED_RUN_LEDGER",
            "outputs": outputs,
            "accepted_runs": [{"source_run": self.missing,
                               "collection_intake": {"path": "", "sha256": ""}}],
        })

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_without_a_collector_the_old_all_or_nothing_behaviour_is_kept(self):
        with self.assertRaises(RuntimeError) as caught:
            promoted_roots(self.feed)
        self.assertIn("no longer inspectable", str(caught.exception))

    def test_a_never_folded_pruned_run_is_skipped_recorded_and_flagged(self):
        skipped = []
        self.assertEqual(fold_promoted(self.feed, fold_receipt_path=self.fold_receipt,
                                       skipped=skipped), [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["source_run"], "press_refresh_20260828")
        self.assertEqual(skipped[0]["reason"], "MISSING_RUN_FILES")
        self.assertFalse(skipped[0]["already_folded"],
                         "a run that never reached the baseline is a real hole")

    def test_an_already_folded_pruned_run_is_skipped_without_alarm(self):
        atomic_json(self.fold_receipt, {
            "schema": "press-baseline-fold-v1",
            "history": [{"source_run": "press_refresh_20260828", "status": "FOLDED",
                         "rows_added": 7454, "rows_after": 257223,
                         "folded_at": "2026-09-01T06:57:39+00:00"}],
        })
        skipped = []
        fold_promoted(self.feed, fold_receipt_path=self.fold_receipt, skipped=skipped)
        self.assertTrue(skipped[0]["already_folded"])

    def test_an_empty_ledger_still_refuses(self):
        with io.open(self.feed, encoding="utf-8") as handle:
            document = json.loads(handle.read())
        document["accepted_runs"] = []
        atomic_json(self.feed, document)
        with self.assertRaises(RuntimeError) as caught:
            promoted_roots(self.feed, [])
        self.assertIn("no accepted runs", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

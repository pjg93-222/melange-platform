"""Tests for the culling control plane's ``review_current`` decision.

``review_current`` used to be decided from schema + ``membership_count`` +
``discovery_manifest_sha256`` alone, none of which can tell two review ledgers
apart.  Two 62,687-row ledgers with the identical schema -- the consumed
200,363,847 B one and the unread 312,129,291 B staging one, 83 versus 94 fields
per row -- satisfied all three identically, so the gate reported the same truth
whichever it was pointed at.

Every test drives ``main()`` over a synthetic control-plane tree and asserts on
``gates.membership_adjudication_complete``, which is the published surface
``review_current`` feeds and which exists on every revision of this script --
that is what makes the pre-fix comparison meaningful.

Nothing under ``audits/`` is read or written.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from fec.legislation import build_legislation_culling_control_plane as plane


CANDIDATES = 3
MEMBERSHIPS = 5
REVIEW_SCHEMA = "acu-legislation-membership-review-v3"


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def ndjson(rows: list[dict]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")


def ledger_body(fields: int) -> bytes:
    """A review ledger with MEMBERSHIPS rows and ``fields`` columns per row.

    Row count is held constant so the only thing that distinguishes two
    ledgers is the body itself -- the exact situation the old gate was blind
    to.
    """
    rows = []
    for index in range(MEMBERSHIPS):
        row = {
            "canonical_key": f"117:hr:{index + 1}",
            "query_id": "q_a",
            "disposition": "unresolved",
        }
        for extra in range(fields - 3):
            row[f"evidence_{extra}"] = f"column {extra} for row {index}"
        rows.append(row)
    return ndjson(rows)


class ReviewLedgerBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    # -- fixture ----------------------------------------------------------

    def build_tree(
        self,
        *,
        ledger_fields: int = 83,
        declare_outputs: bool = True,
        declared_sha: str | None = None,
        declared_bytes: int | None = None,
        write_ledger: bool = True,
        discovery_sha_override: str | None = None,
    ) -> dict:
        """Write a minimal control-plane tree and run ``main()`` over it."""

        audit = self.root / "audits" / "legfix5"
        review_dir = audit / "candidate_membership_review"

        # discovery
        records = ndjson([{"canonical_key": f"117:hr:{n + 1}"}
                          for n in range(CANDIDATES)])
        memberships = ndjson([{"canonical_key": f"117:hr:{n + 1}",
                               "query_id": "q_a"} for n in range(MEMBERSHIPS)])
        discovery_dir = audit / "congress_advanced_pages_20260725"
        discovery_dir.mkdir(parents=True, exist_ok=True)
        (discovery_dir / "records.ndjson").write_bytes(records)
        (discovery_dir / "memberships.ndjson").write_bytes(memberships)
        discovery_manifest = discovery_dir / "manifest.json"
        write_json(
            discovery_manifest,
            {
                "status": "complete",
                "primary_instrument":
                    "Congress.gov Advanced Search - Legislation",
                "quorum_used": False,
                "selected_query_ids": [],
                "queries": [],
                "union": {
                    "unique_record_count": CANDIDATES,
                    "membership_count": MEMBERSHIPS,
                    "records_file": "records.ndjson",
                    "memberships_file": "memberships.ndjson",
                    "records_sha256": None,
                    "memberships_sha256": None,
                },
            },
        )
        discovery_sha = plane.sha256_file(discovery_manifest)

        # the review ledger body
        body = ledger_body(ledger_fields)
        ledger_path = review_dir / "membership_review_ledger.ndjson"
        review_dir.mkdir(parents=True, exist_ok=True)
        if write_ledger:
            ledger_path.write_bytes(body)

        relative_ledger = "audits/legfix5/candidate_membership_review/" \
                          "membership_review_ledger.ndjson"
        review: dict = {
            "schema": REVIEW_SCHEMA,
            "membership_count": MEMBERSHIPS,
            "discovery_manifest_sha256":
                discovery_sha_override or discovery_sha,
            "unresolved_count": 0,
            "promotion_gate_passed": True,
            "ledger_file": relative_ledger,
        }
        if declare_outputs:
            review["outputs"] = {
                "ledger": {
                    "path": relative_ledger,
                    "bytes": (declared_bytes if declared_bytes is not None
                              else len(body)),
                    "sha256": declared_sha or sha256_bytes(body),
                    "record_count": MEMBERSHIPS,
                }
            }
        review_manifest = review_dir / "manifest.json"
        write_json(review_manifest, review)

        # remaining required inputs; their own gates are not under test
        progress = audit / "progress.json"
        write_json(progress, {"status": "complete", "current": CANDIDATES,
                              "total": CANDIDATES, "failure_count": 0})
        billstatus = audit / "run_manifest.json"
        write_json(billstatus, {"status": "COMPLETE", "partition": {
            "all_candidates": CANDIDATES, "top_level_completed": CANDIDATES,
            "top_level_planned": CANDIDATES, "top_level_failed": 0}})
        quorum = audit / "quorum.json"
        write_json(quorum, {"schema":
                            "acu-legislation-quorum-recall-floor-audit-v1",
                            "status": "PASS",
                            "counts": {"unresolved": 0,
                                       "floor_unique_identifiers": 0,
                                       "present_in_primary_union": 0}})
        identifier = audit / "identifier.json"
        write_json(identifier, {
            "schema": "congress-identifier-failure-resolution-v1",
            "status": "PASS", "expected_query_total": 210,
            "completion_checks": {"all": True},
            "counts": {"unresolved_query_count": 0}})
        baseline = audit / "baseline.json"
        write_json(baseline, {"status": "NOT_A_CONTROL"})

        out = self.root / "out" / "current.json"
        argv = [
            "build_legislation_culling_control_plane.py",
            "--discovery-manifest", str(discovery_manifest),
            "--full-text-progress", str(progress),
            "--billstatus-manifest", str(billstatus),
            "--review-manifest", str(review_manifest),
            "--quorum-floor-receipt", str(quorum),
            "--identifier-receipt", str(identifier),
            "--baseline-control", str(baseline),
            "--junk-root", str(audit / "no_junk_pile"),
            "--candidate-evidence-manifest", str(audit / "absent_evidence.json"),
            "--partition-manifest", str(audit / "absent_partition.json"),
            "--out", str(out),
        ]

        saved_root, saved_argv = plane.ROOT, sys.argv
        plane.ROOT = self.root
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                plane.main()
        finally:
            plane.ROOT, sys.argv = saved_root, saved_argv
        return json.loads(out.read_text(encoding="utf-8"))

    def review_current(self, result: dict) -> bool:
        """``review_current`` as published through the integration gate.

        Read from ``gates`` rather than any newer diagnostic key so the same
        assertion is meaningful against the pre-fix script.  The fixture sets
        ``unresolved_count = 0`` and ``promotion_gate_passed = True`` so this
        gate tracks ``review_current`` exactly.
        """
        return result["gates"]["membership_adjudication_complete"] is True

    # -- the binding ------------------------------------------------------

    def test_a_ledger_matching_its_declared_hash_is_current(self) -> None:
        result = self.build_tree()
        self.assertTrue(self.review_current(result), result.get("review_binding"))
        self.assertTrue(result["review_binding"]["ledger_body_verified"])

    def test_a_different_ledger_body_with_identical_counts_is_not_current(
        self,
    ) -> None:
        """The 83-field and 94-field ledgers share schema, row count and
        ``discovery_manifest_sha256``.  Only the body tells them apart."""

        wrong = sha256_bytes(ledger_body(94))
        result = self.build_tree(ledger_fields=83, declared_sha=wrong,
                                 declared_bytes=len(ledger_body(94)))
        self.assertFalse(self.review_current(result))

    def test_a_manifest_declaring_no_ledger_hash_is_not_current(self) -> None:
        """The consumed manifest carries ``ledger_file`` and no ``outputs``
        block, so nothing binds its body to it."""

        result = self.build_tree(declare_outputs=False)
        self.assertFalse(self.review_current(result))
        binding = result["review_binding"]["ledger"]
        self.assertFalse(binding["verified"])
        self.assertIn("declares no sha256", binding["reason"])

    def test_a_ledger_body_that_was_swapped_out_is_not_current(self) -> None:
        """Manifest and counts are untouched; only the file on disk changed."""

        declared = sha256_bytes(ledger_body(83))
        result = self.build_tree(ledger_fields=94, declared_sha=declared,
                                 declared_bytes=len(ledger_body(83)))
        self.assertFalse(self.review_current(result))

    def test_a_ledger_whose_byte_count_drifted_is_not_current(self) -> None:
        result = self.build_tree(declared_bytes=999_999)
        self.assertFalse(self.review_current(result))
        self.assertIn("byte count",
                      result["review_binding"]["ledger"]["reason"])

    def test_a_missing_ledger_body_is_not_current(self) -> None:
        result = self.build_tree(write_ledger=False)
        self.assertFalse(self.review_current(result))
        self.assertIn("not on disk",
                      result["review_binding"]["ledger"]["reason"])

    # -- non-regression ---------------------------------------------------

    def test_a_stale_discovery_manifest_hash_is_still_not_current(self) -> None:
        result = self.build_tree(discovery_sha_override="0" * 64)
        self.assertFalse(self.review_current(result))

    def test_the_control_plane_still_declares_it_wrote_nothing_operational(
        self,
    ) -> None:
        result = self.build_tree()
        self.assertEqual(
            result["safety"],
            {
                "candidate_deletion_performed": False,
                "scoring_performed": False,
                "production_write_performed": False,
                "main_corpus_write_performed": False,
                "sync_or_promotion_performed": False,
                "historical_downstream_receipts_inherited": False,
            },
        )


if __name__ == "__main__":
    unittest.main()

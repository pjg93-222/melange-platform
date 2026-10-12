from __future__ import annotations

import gzip
import json
from pathlib import Path
import tempfile
import unittest

from fec.audit import audit_legfix5_coverage_reconciliation as auditor


DECLARED = sorted(auditor.DECLARED_MISSING_URL_KEYS)


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, separators=(",", ":")) + "\n" for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


class CoverageReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def scaffold(
        self,
        *,
        records: list[str],
        edges: list[tuple[str, str]],
        ledger_edges: list[tuple[str, str]] | None = None,
        billstatus_keys: list[str] | None = None,
        planned: list[tuple[str, str]] | None = None,
        accepted: list[tuple[str, str]] | None = None,
        missing_url_keys: list[str] | None = None,
        congress_text_keys: list[str] | None = None,
        congress_text: list[tuple[str, str]] | None = None,
        corrupt_full_text_receipts: int = 0,
        corrupt_billstatus_records: int = 0,
    ) -> dict:
        write_ndjson(
            self.root / "records.ndjson",
            [{"canonical_key": key} for key in records],
        )
        write_ndjson(
            self.root / "memberships.ndjson",
            [{"canonical_key": k, "query_id": q} for k, q in edges],
        )
        write_ndjson(
            self.root / "ledger.ndjson",
            [
                {"canonical_key": k, "query_id": q, "disposition": "unresolved"}
                for k, q in (ledger_edges if ledger_edges is not None else edges)
            ],
        )

        billstatus = self.root / "billstatus"
        billstatus.mkdir(exist_ok=True)
        for key in billstatus_keys if billstatus_keys is not None else records:
            (billstatus / f"{key.replace(':', '_')}.json").write_text(
                json.dumps({"canonical_key": key}), encoding="utf-8"
            )

        run = self.root / "govinfo"
        write_ndjson(
            run / "all_jobs.ndjson",
            [
                {"canonical_key": k, "job_id": j}
                for k, j in (planned if planned is not None else [])
            ],
        )
        acc_dir = run / "receipts" / "accepted"
        acc_dir.mkdir(parents=True, exist_ok=True)
        for key, job in accepted if accepted is not None else []:
            (acc_dir / f"{job}.json").write_text(
                json.dumps({"canonical_key": key, "job_id": job}),
                encoding="utf-8",
            )
        (run / "receipts" / "failure").mkdir(parents=True, exist_ok=True)
        planned_rows = planned if planned is not None else []
        accepted_rows = accepted if accepted is not None else []
        (run / "completed.json").write_text(
            json.dumps(
                {
                    "run_id": "test-run",
                    "plan_sha256": "0" * 64,
                    "dispatch_count": len(planned_rows),
                    "accepted_count": len(accepted_rows),
                    "failure_count": len(planned_rows) - len(accepted_rows),
                    "retry_waits": 0,
                    "attempts_per_job": 1,
                    "missing_url_keys": (
                        missing_url_keys
                        if missing_url_keys is not None
                        else DECLARED
                    ),
                }
            ),
            encoding="utf-8",
        )

        text = self.root / "fulltext"
        text.mkdir(exist_ok=True)
        for key in congress_text_keys or []:
            leaf = text / key.replace(":", "_")
            leaf.mkdir(parents=True, exist_ok=True)
            (leaf / "receipt.json").write_text(
                json.dumps({"canonical_key": key, "status": "accepted"}),
                encoding="utf-8",
            )
        for key, archive in congress_text or []:
            self.write_full_text_receipt(text, key, archive)
        for index in range(corrupt_full_text_receipts):
            leaf = text / f"corrupt_{index}"
            leaf.mkdir(parents=True, exist_ok=True)
            (leaf / "receipt.json").write_text("{ not json", encoding="utf-8")
        for index in range(corrupt_billstatus_records):
            (billstatus / f"corrupt_{index}.json").write_text(
                "{ not json", encoding="utf-8"
            )

        args = auditor.parse_args(
            [
                "--records", str(self.root / "records.ndjson"),
                "--memberships", str(self.root / "memberships.ndjson"),
                "--ledger", str(self.root / "ledger.ndjson"),
                "--billstatus", str(billstatus),
                "--full-text-receipts", str(text),
                "--govinfo-run", str(run),
                "--out", str(self.root / "out"),
            ]
        )
        return auditor.audit(args)

    def write_full_text_receipt(
        self, text_root: Path, key: str, archive: str
    ) -> None:
        """Write one accepted full-text receipt and, per ``archive``, the
        gzipped body it claims to have written.

        ``present``    body on disk, ``byte_length`` agrees
        ``absent``     receipt asserts a body that is not there
        ``truncated``  body on disk but shorter than ``byte_length`` claims
        ``undeclared`` receipt names no archive at all
        """
        leaf = text_root / key.replace(":", "_")
        leaf.mkdir(parents=True, exist_ok=True)
        body = f"<html>{key}</html>".encode("utf-8")
        row: dict = {"canonical_key": key, "status": "accepted"}
        if archive != "undeclared":
            archive_path = leaf / "page.html.gz"
            row["html_file"] = str(archive_path)
            row["byte_length"] = len(body)
            if archive == "present":
                archive_path.write_bytes(gzip.compress(body))
            elif archive == "truncated":
                archive_path.write_bytes(gzip.compress(body[:-3]))
        (leaf / "receipt.json").write_text(
            json.dumps(row), encoding="utf-8"
        )

    def check(self, receipt: dict, name: str) -> dict:
        for item in receipt["findings"]:
            if item["check"] == name:
                return item
        raise AssertionError(f"missing check: {name}")

    def test_clean_corpus_passes_every_check(self) -> None:
        keys = DECLARED + ["117:hr:1"]
        receipt = self.scaffold(
            records=keys,
            edges=[(k, "q_a") for k in keys],
            planned=[("117:hr:1", "job1")],
            accepted=[("117:hr:1", "job1")],
        )
        self.assertEqual(receipt["status"], "PASS_COVERAGE_RECONCILIATION")
        self.assertTrue(all(item["pass"] for item in receipt["findings"]))

    def test_edge_gaps_are_reported_in_both_directions(self) -> None:
        receipt = self.scaffold(
            records=["117:hr:1", "117:hr:2"],
            edges=[("117:hr:1", "q_a"), ("117:hr:2", "q_b")],
            ledger_edges=[("117:hr:1", "q_a"), ("117:hr:9", "q_ghost")],
        )
        self.assertEqual(receipt["status"], "COVERAGE_GAPS_PRESENT")
        self.assertEqual(
            receipt["review_ledger"]["discovery_edges_absent_from_ledger"], 1
        )
        self.assertEqual(
            receipt["review_ledger"]["ledger_edges_absent_from_discovery"], 1
        )
        self.assertFalse(
            self.check(
                receipt, "review_ledger_covers_the_discovery_membership_universe"
            )["pass"]
        )
        self.assertFalse(
            self.check(
                receipt, "review_ledger_introduces_no_edge_outside_discovery"
            )["pass"]
        )

    def test_amendment_without_billstatus_is_expected_but_a_bill_is_not(
        self,
    ) -> None:
        receipt = self.scaffold(
            records=["116:hamdt:10", "117:hr:1"],
            edges=[("116:hamdt:10", "q_a"), ("117:hr:1", "q_a")],
            billstatus_keys=["117:hr:1"],
        )
        self.assertTrue(
            self.check(
                receipt,
                "no_candidate_lacks_billstatus_without_being_an_amendment",
            )["pass"]
        )

        self.setUp()
        receipt = self.scaffold(
            records=["116:hamdt:10", "117:hr:1"],
            edges=[("116:hamdt:10", "q_a"), ("117:hr:1", "q_a")],
            billstatus_keys=[],
        )
        finding = self.check(
            receipt, "no_candidate_lacks_billstatus_without_being_an_amendment"
        )
        self.assertFalse(finding["pass"])
        self.assertIn("117:hr:1", finding["sample"])

    def test_missing_url_exceptions_must_be_exactly_the_declared_ten(self) -> None:
        receipt = self.scaffold(
            records=["117:hr:1"],
            edges=[("117:hr:1", "q_a")],
            missing_url_keys=DECLARED[:9],
        )
        self.assertFalse(
            self.check(
                receipt, "declared_missing_url_exceptions_are_exactly_the_ten"
            )["pass"]
        )

    def test_partial_and_zero_version_acceptance_are_counted(self) -> None:
        receipt = self.scaffold(
            records=["117:hr:1", "117:hr:2", "117:hr:3"],
            edges=[(f"117:hr:{n}", "q_a") for n in (1, 2, 3)],
            planned=[
                ("117:hr:1", "a1"),
                ("117:hr:2", "b1"),
                ("117:hr:2", "b2"),
                ("117:hr:3", "c1"),
            ],
            accepted=[("117:hr:1", "a1"), ("117:hr:2", "b1")],
        )
        text = receipt["govinfo_official_text"]
        self.assertEqual(text["all_versions_accepted"], 1)
        self.assertEqual(text["partially_accepted"], 1)
        self.assertEqual(text["zero_accepted"], 1)
        self.assertEqual(text["zero_accepted_keys"], ["117:hr:3"])

    # -- archived bodies, not assertions about them -----------------------

    def test_accepted_receipts_without_their_archived_bodies_are_a_gap(
        self,
    ) -> None:
        """``text_relevance_httr_full_stage_20260726`` held 2,886 accepted
        receipts against ONE surviving archive.  Counting the receipts reported
        full coverage over a 2,885-file loss."""

        keys = ["117:hr:1", "117:hr:2", "117:hr:3"]
        receipt = self.scaffold(
            records=keys,
            edges=[(k, "q_a") for k in keys],
            congress_text=[
                ("117:hr:1", "present"),
                ("117:hr:2", "absent"),
                ("117:hr:3", "absent"),
            ],
        )
        text = receipt["congress_gov_full_text"]
        self.assertEqual(text["candidates_with_asserted_text"], 3)
        self.assertEqual(text["candidates_with_present_text"], 1)
        self.assertEqual(text["candidates_with_asserted_but_absent_text"], 2)
        self.assertEqual(text["candidates_with_accepted_text"], 1)
        self.assertEqual(text["candidates_without_accepted_text"], 2)

        finding = self.check(
            receipt, "every_accepted_full_text_receipt_has_its_archived_body"
        )
        self.assertFalse(finding["pass"])
        self.assertEqual(receipt["status"], "COVERAGE_GAPS_PRESENT")

    def test_present_archives_are_counted_as_real_coverage(self) -> None:
        keys = ["117:hr:1", "117:hr:2"]
        receipt = self.scaffold(
            records=keys,
            edges=[(k, "q_a") for k in keys],
            congress_text=[(k, "present") for k in keys],
        )
        text = receipt["congress_gov_full_text"]
        self.assertEqual(text["candidates_with_present_text"], 2)
        self.assertEqual(text["candidates_with_asserted_but_absent_text"], 0)
        self.assertEqual(text["coverage_fraction_of_candidates"], 1.0)
        self.assertTrue(
            self.check(
                receipt,
                "every_accepted_full_text_receipt_has_its_archived_body",
            )["pass"]
        )

    def test_a_truncated_archive_does_not_count_as_present(self) -> None:
        """The body is on disk but shorter than the receipt's ``byte_length``,
        so the acceptance is not backed by the text it claims."""

        receipt = self.scaffold(
            records=["117:hr:1"],
            edges=[("117:hr:1", "q_a")],
            congress_text=[("117:hr:1", "truncated")],
        )
        text = receipt["congress_gov_full_text"]
        self.assertEqual(text["candidates_with_asserted_text"], 1)
        self.assertEqual(text["candidates_with_present_text"], 0)
        self.assertEqual(text["archived_body_states"].get("truncated"), 1)
        self.assertFalse(
            self.check(
                receipt,
                "every_accepted_full_text_receipt_has_its_archived_body",
            )["pass"]
        )

    def test_an_acceptance_naming_no_archive_at_all_is_a_gap(self) -> None:
        receipt = self.scaffold(
            records=["117:hr:1"],
            edges=[("117:hr:1", "q_a")],
            congress_text=[("117:hr:1", "undeclared")],
        )
        text = receipt["congress_gov_full_text"]
        self.assertEqual(text["candidates_with_present_text"], 0)
        self.assertEqual(text["archived_body_states"].get("undeclared"), 1)
        self.assertFalse(
            self.check(
                receipt,
                "every_accepted_full_text_receipt_has_its_archived_body",
            )["pass"]
        )

    # -- one exception policy, not two ------------------------------------

    def test_an_unreadable_full_text_receipt_is_counted_not_skipped(
        self,
    ) -> None:
        """A locked or truncated receipt used to ``continue``, shrinking the
        coverage count while the check it fed still reported PASS."""

        receipt = self.scaffold(
            records=["117:hr:1"],
            edges=[("117:hr:1", "q_a")],
            congress_text=[("117:hr:1", "present")],
            corrupt_full_text_receipts=2,
        )
        self.assertEqual(
            receipt["evidence_readability"]["unreadable_per_item_receipts"], 2
        )
        self.assertFalse(
            self.check(receipt, "no_evidence_receipt_was_unreadable")["pass"]
        )
        self.assertEqual(receipt["status"], "COVERAGE_GAPS_PRESENT")

    def test_an_unreadable_billstatus_record_is_counted_not_skipped(
        self,
    ) -> None:
        receipt = self.scaffold(
            records=["117:hr:1"],
            edges=[("117:hr:1", "q_a")],
            corrupt_billstatus_records=1,
        )
        self.assertEqual(
            receipt["evidence_readability"]["unreadable_per_item_receipts"], 1
        )
        self.assertFalse(
            self.check(receipt, "no_evidence_receipt_was_unreadable")["pass"]
        )

    def test_readable_evidence_reports_a_clean_readability_finding(
        self,
    ) -> None:
        receipt = self.scaffold(
            records=["117:hr:1"],
            edges=[("117:hr:1", "q_a")],
            congress_text=[("117:hr:1", "present")],
        )
        self.assertEqual(
            receipt["evidence_readability"]["unreadable_per_item_receipts"], 0
        )
        self.assertTrue(
            self.check(receipt, "no_evidence_receipt_was_unreadable")["pass"]
        )
        self.assertEqual(
            receipt["evidence_readability"]["per_item_receipt_policy"],
            "counted_and_surfaced",
        )

    def test_audit_declares_it_wrote_nothing_operational(self) -> None:
        receipt = self.scaffold(
            records=["117:hr:1"], edges=[("117:hr:1", "q_a")]
        )
        self.assertEqual(
            receipt["safety"],
            {
                "collection_performed": False,
                "culling_performed": False,
                "scoring_performed": False,
                "main_corpus_write_performed": False,
            },
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fec.legislation import build_legislation_candidate_evidence as builder
from fec.legislation.legislation_culling_integration_contract import (
    CullingContractError,
    validate_candidate_evidence,
)


MARKER = "Amendment Text:"
BODY = f"<html><body>preamble {MARKER} operative provisions here</body></html>"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def record(key: str, *, title: str = "An Act to do a thing") -> dict:
    congress, kind, number = key.split(":")
    return {
        "canonical_key": key,
        "title": title,
        "canonical_url": (
            f"https://www.congress.gov/bill/{congress}th-congress/"
            f"house-bill/{number}"
        ),
    }


def billstatus(key: str, *, summaries: list | None = None) -> dict:
    return {
        "schema": "govinfo-billstatus-enrichment-record-v1",
        "canonical_key": key,
        "summaries": summaries if summaries is not None else [],
        "actions": [{"action_index": 0, "action_type": "IntroReferral"}],
        "sponsors": [{"bioguide_id": "A000376"}],
        "cosponsors": [
            {"bioguide_id": "S001168", "is_original_cosponsor": "True"},
            {"bioguide_id": "X000001", "is_original_cosponsor": "False"},
        ],
    }


class CandidateEvidenceBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def build(
        self,
        *,
        records: list[dict],
        memberships: list[dict],
        billstatus_rows: list[dict] | None = None,
        full_text_body: bytes | None = None,
        receipt_overrides: dict | None = None,
    ) -> dict:
        records_path = self.root / "records.ndjson"
        memberships_path = self.root / "memberships.ndjson"
        write_ndjson(records_path, records)
        write_ndjson(memberships_path, memberships)

        billstatus_dir = self.root / "billstatus"
        billstatus_dir.mkdir(exist_ok=True)
        for row in billstatus_rows or []:
            (billstatus_dir / f"{row['canonical_key'].replace(':', '_')}.json").write_bytes(
                json.dumps(row, separators=(",", ":")).encode("utf-8")
            )

        receipts_dir = self.root / "receipts"
        if full_text_body is not None or receipt_overrides is not None:
            key = records[0]["canonical_key"]
            leaf = receipts_dir / key.replace(":", "_")
            leaf.mkdir(parents=True, exist_ok=True)
            archive = leaf / "page.html.gz"
            body = full_text_body if full_text_body is not None else BODY.encode()
            archive.write_bytes(gzip.compress(body))
            url = (
                "https://www.congress.gov/amendment/116th-congress/"
                "house-amendment/10/text"
            )
            receipt = {
                "schema": "congress-httr-cookie-replay-receipt-v1",
                "status": "accepted",
                "canonical_key": key,
                "expected_url": url,
                "final_url": url,
                "expected_marker": MARKER,
                "body_sha256": sha256_bytes(body),
                "cookie_values_persisted": False,
                "html_file": str(archive),
                "validation": {
                    "status_200": True,
                    "exact_url": True,
                    "html_content_type": True,
                    "not_challenge": True,
                    "expected_main_marker": True,
                },
            }
            receipt.update(receipt_overrides or {})
            (leaf / "receipt.json").write_text(
                json.dumps(receipt, separators=(",", ":")), encoding="utf-8"
            )
        receipts_dir.mkdir(exist_ok=True)

        out = self.root / "out"
        args = builder.parse_args(
            [
                "--records", str(records_path),
                "--memberships", str(memberships_path),
                "--billstatus", str(billstatus_dir),
                "--full-text-receipts", str(receipts_dir),
                "--out", str(out),
            ]
        )
        return builder.build(args)

    def envelopes(self) -> list[dict]:
        path = self.root / "out" / "candidate_evidence.ndjson"
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_membership_query_ids_exactly_match_discovered_universe(self) -> None:
        self.build(
            records=[record("117:hr:1")],
            memberships=[
                {"canonical_key": "117:hr:1", "query_id": "q_b"},
                {"canonical_key": "117:hr:1", "query_id": "q_a"},
                {"canonical_key": "117:hr:1", "query_id": "q_a"},
            ],
        )
        envelope = self.envelopes()[0]
        # Deduplicated and sorted, so the culling engine's universe-equality
        # gate can never trip on ordering or repetition.
        self.assertEqual(envelope["membership_query_ids"], ["q_a", "q_b"])

    def test_candidate_without_membership_is_omitted_not_invented(self) -> None:
        receipt = self.build(
            records=[record("117:hr:1"), record("117:hr:2")],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        self.assertEqual(receipt["counts"]["candidates"], 1)
        self.assertEqual(receipt["counts"]["candidates_without_membership"], 1)
        self.assertEqual(
            [row["canonical_key"] for row in self.envelopes()], ["117:hr:1"]
        )

    def test_title_requires_a_congress_gov_source(self) -> None:
        bad = record("117:hr:1")
        bad["canonical_url"] = "https://www.govinfo.gov/app/details/BILLS-117hr1ih"
        self.build(
            records=[bad],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        title = self.envelopes()[0]["official_evidence"]["title"]
        self.assertEqual(title["status"], "missing")

    def test_absent_summary_is_officially_unavailable_with_receipt(self) -> None:
        self.build(
            records=[record("117:hr:1")],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
            billstatus_rows=[billstatus("117:hr:1", summaries=[])],
        )
        summary = self.envelopes()[0]["official_evidence"]["summary"]
        self.assertEqual(summary["status"], "officially_unavailable")
        self.assertTrue(summary["unavailability_reason"])
        self.assertRegex(summary["availability_receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_govinfo_summary_is_never_relabelled_as_congress_gov(self) -> None:
        self.build(
            records=[record("117:hr:1")],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
            billstatus_rows=[
                billstatus("117:hr:1", summaries=[{"text": "A summary."}])
            ],
        )
        summary = self.envelopes()[0]["official_evidence"]["summary"]
        self.assertEqual(
            summary["status"], "available_pending_congress_gov_fetch"
        )
        self.assertNotIn("source_url", summary)

    def test_full_text_accepted_only_when_archive_reverifies(self) -> None:
        self.build(
            records=[record("117:hr:1")],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
            full_text_body=BODY.encode(),
        )
        full_text = self.envelopes()[0]["official_evidence"]["full_text"]
        self.assertEqual(full_text["status"], "accepted")
        self.assertIs(full_text["archive_reverified"], True)
        self.assertEqual(
            full_text["decompressed_body_sha256"], full_text["body_sha256"]
        )
        self.assertRegex(
            full_text["marker_bounded_text_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertRegex(
            full_text["extraction_receipt_sha256"], r"^[0-9a-f]{64}$"
        )

    def test_archive_digest_drift_fails_closed(self) -> None:
        self.build(
            records=[record("117:hr:1")],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
            full_text_body=BODY.encode(),
            receipt_overrides={"body_sha256": "0" * 64},
        )
        full_text = self.envelopes()[0]["official_evidence"]["full_text"]
        self.assertEqual(full_text["status"], "archive_drift")

    def test_missing_marker_fails_closed(self) -> None:
        self.build(
            records=[record("117:hr:1")],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
            full_text_body=b"<html><body>no marker anywhere</body></html>",
        )
        full_text = self.envelopes()[0]["official_evidence"]["full_text"]
        self.assertEqual(full_text["status"], "marker_absent")

    def test_evidence_is_never_review_ready_without_category_adjudication(
        self,
    ) -> None:
        # Even a candidate with title, summary, and reverified full text must
        # not become semantically review-ready while the 19-category review is
        # absent.  Missing review is flagged, never junk.
        self.build(
            records=[record("117:hr:1")],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
            billstatus_rows=[billstatus("117:hr:1", summaries=[])],
            full_text_body=BODY.encode(),
        )
        envelope = self.envelopes()[0]
        self.assertEqual(envelope["category_evidence"], [])
        readiness = validate_candidate_evidence(envelope)
        self.assertTrue(readiness["title_ready"])
        self.assertTrue(readiness["summary_explicitly_unavailable"])
        self.assertTrue(readiness["full_text_ready"])
        self.assertFalse(readiness["category_audit_ready"])
        self.assertFalse(readiness["keyword_review_ready"])
        self.assertFalse(readiness["semantic_review_ready"])
        self.assertFalse(readiness["integration_evidence_ready"])

    def test_emitted_envelope_matches_the_consumer_contract(self) -> None:
        self.build(
            records=[record("117:hr:1")],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
            billstatus_rows=[billstatus("117:hr:1")],
        )
        # Must be accepted by the contract validator as a well-formed
        # envelope; readiness flags are allowed to be False.
        try:
            validate_candidate_evidence(self.envelopes()[0])
        except CullingContractError as error:  # pragma: no cover
            self.fail(f"contract rejected a well-formed envelope: {error}")

    def test_amendment_relationships_are_not_required(self) -> None:
        self.build(
            records=[record("116:hamdt:10")],
            memberships=[{"canonical_key": "116:hamdt:10", "query_id": "q_a"}],
        )
        relationships = self.envelopes()[0]["relationships"]
        self.assertEqual(relationships["status"], "not_required")

    def test_receipt_declares_no_culling_or_corpus_write(self) -> None:
        receipt = self.build(
            records=[record("117:hr:1")],
            memberships=[{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        self.assertEqual(
            receipt["safety"],
            {
                "category_review_performed": False,
                "keyword_review_performed": False,
                "culling_performed": False,
                "scoring_performed": False,
                "main_corpus_write_performed": False,
            },
        )


if __name__ == "__main__":
    unittest.main()

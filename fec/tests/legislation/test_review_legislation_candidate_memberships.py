from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from legislation import review_legislation_candidate_memberships as REVIEW  # noqa: E402


def exclusion_decision() -> dict:
    excerpt = (
        "This bill limits design patent infringement liability for component "
        "parts used to repair the exterior of a motor vehicle."
    )
    return {
        "canonical_key": "118:hr:1707",
        "query_id": "examination_supervision",
        "disposition": "exclude_false_positive",
        "decision_type": "membership_category_relevance",
        "reason_code": "short_act_alias_collision_different_subject",
        "reason": (
            "The operative provisions concern motor-vehicle design-patent "
            "liability, not credit-union examination or supervision."
        ),
        "evidence_basis": "official_title_and_provisions",
        "evidence": "Congress.gov official title and CRS summary.",
        "official_source_url": (
            "https://www.congress.gov/bill/118th-congress/house-bill/1707"
        ),
        "official_text_version": "CRS Summary - Introduced in House",
        "official_section": "Summary: H.R.1707 - 118th Congress",
        "official_evidence_excerpt": excerpt,
        "official_evidence_sha256": hashlib.sha256(
            excerpt.encode("utf-8")
        ).hexdigest(),
        "official_retrieved_at": "2026-07-25",
        "text_unavailable": False,
        "evidence_sources_checked": [
            "official_title",
            "official_crs_summary",
        ],
        "reviewer_confidence": "high",
        "adjudication_state": "human_ratified",
        "reviewer": "Human reviewer",
        "reviewed_at": "2026-07-25T12:00:00Z",
    }


class ReviewDecisionAuthorityTests(unittest.TestCase):
    def test_specific_official_title_and_provisions_can_support_exclusion(self):
        REVIEW.validate_decision(exclusion_decision())

    def test_generic_or_ambiguous_act_excerpt_cannot_support_exclusion(self):
        for excerpt in ("An Act.", "SMART Act"):
            with self.subTest(excerpt=excerpt):
                row = exclusion_decision()
                row["official_evidence_excerpt"] = excerpt
                row["official_evidence_sha256"] = hashlib.sha256(
                    excerpt.encode("utf-8")
                ).hexdigest()
                with self.assertRaisesRegex(
                    ValueError, "generic or ambiguous Act language"
                ):
                    REVIEW.validate_decision(row)

    def test_article_or_acu_press_absence_cannot_support_exclusion(self):
        row = exclusion_decision()
        row["evidence_sources_checked"].append("no ACU article found")
        with self.assertRaisesRegex(ValueError, "press absence is neutral"):
            REVIEW.validate_decision(row)

    def test_official_title_only_basis_cannot_be_relabeled_as_resolved(self):
        row = exclusion_decision()
        row["evidence_basis"] = "official_title"
        with self.assertRaisesRegex(
            ValueError, "exclude_false_positive decisions require"
        ):
            REVIEW.validate_decision(row)

    def test_identity_mismatch_label_without_exact_identity_proof_fails(self):
        row = exclusion_decision()
        row["evidence_basis"] = "congress_qualified_identity_mismatch"
        row["official_evidence_excerpt"] = "An Act."
        row["official_evidence_sha256"] = hashlib.sha256(
            b"An Act."
        ).hexdigest()
        with self.assertRaisesRegex(
            ValueError, "identity_mismatch_expected_canonical_key"
        ):
            REVIEW.validate_decision(row)

    def test_identity_mismatch_rejects_spoofed_same_identity_url(self):
        row = exclusion_decision()
        row["evidence_basis"] = "congress_qualified_identity_mismatch"
        row["identity_mismatch_expected_canonical_key"] = "118:hr:1707"
        row["identity_mismatch_observed_canonical_key"] = "118:hr:1707"
        with self.assertRaisesRegex(ValueError, "same identity"):
            REVIEW.validate_decision(row)

    def test_legitimate_exact_identity_mismatch_is_machine_verifiable(self):
        row = exclusion_decision()
        row["evidence_basis"] = "congress_qualified_identity_mismatch"
        row["identity_mismatch_expected_canonical_key"] = "118:hr:1707"
        row["identity_mismatch_observed_canonical_key"] = "117:hr:3664"
        row["official_source_url"] = (
            "https://www.congress.gov/bill/117th-congress/house-bill/3664"
        )
        # Generic text is not the proof here; the exact expected/observed
        # identities and official URL are.
        row["official_evidence_excerpt"] = "An Act."
        row["official_evidence_sha256"] = hashlib.sha256(
            b"An Act."
        ).hexdigest()
        REVIEW.validate_decision(row)

    def test_generic_official_title_cannot_resolve_inclusion(self):
        row = exclusion_decision()
        row["disposition"] = "include"
        row["evidence_basis"] = "official_title"
        row["official_section"] = "Official title"
        row["official_evidence_excerpt"] = "SMART Act"
        row["official_evidence_sha256"] = hashlib.sha256(
            b"SMART Act"
        ).hexdigest()
        with self.assertRaisesRegex(
            ValueError, "generic or ambiguous Act language"
        ):
            REVIEW.validate_decision(row)

    def test_generic_citation_boilerplate_cannot_resolve_inclusion(self):
        for excerpt in (
            "This Act may be cited as the Financial Act.",
            "This bill may be cited as the Affordable Banking Act.",
            "An Act to amend the law and for other purposes.",
        ):
            with self.subTest(excerpt=excerpt):
                row = exclusion_decision()
                row["disposition"] = "include"
                row["evidence_basis"] = "official_title"
                row["official_section"] = "Official title"
                row["official_evidence_excerpt"] = excerpt
                row["official_evidence_sha256"] = hashlib.sha256(
                    excerpt.encode("utf-8")
                ).hexdigest()
                with self.assertRaisesRegex(
                    ValueError, "generic or ambiguous Act language"
                ):
                    REVIEW.validate_decision(row)

    def test_specific_official_title_survives_boilerplate_filter(self):
        row = exclusion_decision()
        excerpt = (
            "This Act may be cited as the Credit Union Board "
            "Modernization Act."
        )
        row["disposition"] = "include"
        row["evidence_basis"] = "official_title"
        row["official_section"] = "Official title"
        row["official_evidence_excerpt"] = excerpt
        row["official_evidence_sha256"] = hashlib.sha256(
            excerpt.encode("utf-8")
        ).hexdigest()
        REVIEW.validate_decision(row)

    def test_specific_official_summary_can_resolve_inclusion(self):
        row = exclusion_decision()
        excerpt = (
            "This bill requires federal credit unions to implement specific "
            "cybersecurity safeguards and incident reporting controls."
        )
        row["disposition"] = "include"
        row["evidence_basis"] = "official_summary"
        row["official_section"] = "CRS summary"
        row["official_evidence_excerpt"] = excerpt
        row["official_evidence_sha256"] = hashlib.sha256(
            excerpt.encode("utf-8")
        ).hexdigest()
        REVIEW.validate_decision(row)


class ReviewOutputDurabilityTests(unittest.TestCase):
    def test_ndjson_publish_retries_transient_windows_replace_denial(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.ndjson"
            rows = [{"canonical_key": "119:hr:1", "query_id": "example"}]
            real_replace = REVIEW.os.replace
            attempts = 0

            def flaky_replace(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("transient sharing violation")
                return real_replace(source, destination)

            with mock.patch.object(
                REVIEW.os, "replace", side_effect=flaky_replace
            ):
                REVIEW.write_ndjson(path, rows)

            self.assertEqual(attempts, 2)
            self.assertEqual(
                [json.loads(line) for line in path.read_text().splitlines()],
                rows,
            )
            self.assertFalse(
                path.with_name(
                    f".{path.name}.tmp-{REVIEW.os.getpid()}"
                ).exists()
            )
            receipt = REVIEW.output_file_receipt(path, len(rows))
            self.assertEqual(receipt["record_count"], 1)
            self.assertEqual(receipt["bytes"], path.stat().st_size)
            self.assertEqual(receipt["sha256"], REVIEW.sha256_file(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)

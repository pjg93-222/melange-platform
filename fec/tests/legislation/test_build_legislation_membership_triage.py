# -*- coding: utf-8 -*-
"""Focused contracts for deterministic, non-scoring membership triage."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from legislation import build_legislation_membership_triage as TRIAGE  # noqa: E402


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_ndjson(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_ndjson(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class Fixture:
    def __init__(self, base: Path):
        self.base = base
        self.run = base / "final-run"
        self.relationships = self.run / "relationships"
        self.text = self.run / "text_relevance"
        self.articles = self.run / "article_cross_reference"
        self.registry = base / "registry.json"
        self.review = base / "membership_review_ledger.ndjson"

        records = [
            self.record("119:hr:100", "Known Credit Union Act"),
            self.record(
                "119:hr:200", "Financial Institution Cybersecurity Standards Act"
            ),
            self.record("119:hr:300", "Community Finance Modernization Act"),
            self.record(
                "118:hr:400", "Save Money on Auto Repair Transportation Act"
            ),
            self.record(
                "119:hr:401", "Save Money on Auto Repair Transportation Act"
            ),
            self.record("119:hr:500", "SMART Act"),
            self.record("119:hr:600", "Maritime Cargo Processing Act"),
        ]
        memberships = [
            self.membership("119:hr:100", "examination_supervision"),
            self.membership("119:hr:200", "examination_supervision"),
            self.membership("119:hr:300", "examination_supervision"),
            self.membership("119:hr:300", "payments"),
            self.membership("118:hr:400", "examination_supervision"),
            self.membership("119:hr:401", "examination_supervision"),
            self.membership("119:hr:500", "examination_supervision"),
            self.membership("119:hr:600", "payments"),
        ]
        write_ndjson(self.run / "records.ndjson", records)
        write_ndjson(self.run / "memberships.ndjson", memberships)
        write_json(
            self.run / "manifest.json",
            {
                "schema": "merged-run-v1",
                "status": "complete",
                "completed_at": "2026-07-26T01:00:00Z",
                "union": {
                    "records_file": "records.ndjson",
                    "memberships_file": "memberships.ndjson",
                },
            },
        )

        categories = [
            {
                "id": "examination_supervision",
                "label": "Examination & Supervision",
                "scoring_category": True,
                "known_bills": [
                    {
                        "congress": 119,
                        "compact_cite": "hr100",
                        "source_paragraph": 10,
                        "source_text": "Known Credit Union Act (H.R.100 — SUPPORT)",
                    }
                ],
            },
            {
                "id": "payments",
                "label": "Payments",
                "scoring_category": True,
                "known_bills": [],
            },
        ]
        write_json(self.registry, {"schema": "registry-v1", "categories": categories})

        metadata = [
            {
                "canonical_key": row["canonical_key"],
                "official_title": row["title"],
                "summary": self.summary(row["canonical_key"]),
                "canonical_url": row["canonical_url"],
            }
            for row in records
        ]
        write_ndjson(self.relationships / "measure_metadata.ndjson", metadata)
        write_json(
            self.relationships / "manifest.json",
            {
                "schema": "relationships-v1",
                "status": "complete",
                "completed_at": "2026-07-26T01:10:00Z",
                "measure_metadata_file": "measure_metadata.ndjson",
            },
        )

        text_rows = []
        for membership in memberships:
            key = membership["canonical_key"]
            query_id = membership["query_id"]
            row = {
                "canonical_key": key,
                "query_id": query_id,
                "category_label": query_id,
                "evidence_role": "category_membership",
                "evidence_state": "no_localized_identifier_evidence",
                "text_available": True,
                "version_label": "Introduced in House",
                "identifier_evidence": [],
                "source_url": self.url(key) + "/text",
            }
            if key == "119:hr:200":
                row.update(
                    evidence_state="localized_official_text_evidence",
                    identifier_evidence=[
                        {
                            "localized_match": True,
                            "context": (
                                "This section requires financial institutions, "
                                "including credit unions, to maintain cybersecurity controls."
                            ),
                        }
                    ],
                )
            elif key == "119:hr:500":
                row.update(
                    evidence_state="no_localized_identifier_evidence",
                    identifier_evidence=[
                        {
                            "localized_match": False,
                            "act_reference_only": True,
                            "context": "The SMART Act is referenced without operative action.",
                        }
                    ],
                )
            elif key == "119:hr:600":
                context = (
                    "Section 4 requires ocean freight carriers to submit cargo "
                    "manifests before a vessel enters port."
                )
                row.update(
                    evidence_state="localized_official_text_evidence",
                    identifier_evidence=[
                        {
                            "localized_match": True,
                            "localized_legislative_action_found": True,
                            "affirmative_unrelated_domain": True,
                            "unrelated_domain": "ocean_freight_carrier_operations",
                            "unrelated_reason": (
                                "The localized operative provision regulates ocean "
                                "freight carriers, not financial payment systems."
                            ),
                            "official_section": "Section 4 — Cargo manifests",
                            "context": context,
                            "context_sha256": TRIAGE.sha256_text(context),
                        }
                    ],
                )
            text_rows.append(row)
        write_ndjson(self.text / "text_relevance_evidence.ndjson", text_rows)
        write_json(
            self.text / "manifest.json",
            {
                "schema": "congress-text-relevance-enrichment-manifest-v2",
                "status": "complete",
                "completed_at": "2026-07-26T01:20:00Z",
                "ledger_file": "text_relevance_evidence.ndjson",
            },
        )

        # Deliberately no article matches. Absence must not cull anything.
        write_ndjson(self.articles / "membership_evidence.ndjson", [])
        write_json(
            self.articles / "manifest.json",
            {
                "schema": "article-cross-reference-v3",
                "status": "complete",
                "completed_at": "2026-07-26T01:30:00Z",
                "membership_evidence_file": "membership_evidence.ndjson",
            },
        )

        review_rows = [
            self.review_row("119:hr:100", "examination_supervision"),
            self.review_row("119:hr:200", "examination_supervision"),
            self.review_row("119:hr:300", "examination_supervision"),
            self.review_row("119:hr:300", "payments"),
            {
                **self.review_row("118:hr:400", "examination_supervision"),
                "disposition": "exclude_false_positive",
                "adjudication_state": "human_ratified",
                "reviewer": "Human reviewer",
                "reviewed_at": "2026-07-25T12:00:00Z",
                "reason": "Motor-vehicle design-patent legislation.",
                "official_title": "Save Money on Auto Repair Transportation Act",
            },
            self.review_row("119:hr:401", "examination_supervision"),
            {
                **self.review_row("119:hr:500", "examination_supervision"),
                "ambiguous_short_act_alias_probe": True,
                "matched_short_act_title_aliases": ["SMART Act"],
            },
            self.review_row("119:hr:600", "payments"),
        ]
        write_ndjson(self.review, review_rows)

    @staticmethod
    def url(key):
        congress, measure_type, number = key.split(":")
        return (
            f"https://www.congress.gov/bill/{congress}th-congress/"
            f"{measure_type}/{number}"
        )

    @classmethod
    def record(cls, key, title):
        congress, measure_type, number = key.split(":")
        return {
            "canonical_key": key,
            "congress": int(congress),
            "type": measure_type,
            "number": int(number),
            "display_number": f"{measure_type.upper()}.{number}",
            "title": title,
            "canonical_url": cls.url(key),
        }

    @staticmethod
    def membership(key, query_id):
        return {
            "canonical_key": key,
            "query_id": query_id,
            "record_kind": "category",
            "membership_non_dispositive": False,
        }

    @staticmethod
    def review_row(key, query_id):
        return {
            "canonical_key": key,
            "query_id": query_id,
            "category_label": query_id,
            "scoring_category": True,
            "disposition": "unresolved",
            "candidate_retained_in_discovery_corpus": True,
        }

    @staticmethod
    def summary(key):
        if key == "119:hr:200":
            return (
                "Requires covered financial institutions, including credit unions, "
                "to implement cybersecurity safeguards."
            )
        if key in {"118:hr:400", "119:hr:401"}:
            return "Limits design-patent liability for motor-vehicle repair parts."
        return None

    def build(self, name):
        out = self.base / name
        manifest = TRIAGE.build(
            run_dir=self.run,
            registry_path=self.registry,
            relationships_dir=self.relationships,
            text_dir=self.text,
            article_dir=self.articles,
            review_ledger_path=self.review,
            out_dir=out,
            workspace_root=self.base,
        )
        return out, manifest


class MembershipTriageTests(unittest.TestCase):
    def test_broad_industry_relevance_and_cross_category_measure_survive(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            out, manifest = fixture.build("audit-triage")
            ledger = read_ndjson(out / "triage_ledger.ndjson")
            by_identity = {
                (row["canonical_key"], row["query_id"]): row for row in ledger
            }
            broad = by_identity[("119:hr:200", "examination_supervision")]
            self.assertEqual(
                "unresolved_broad_industry_relevance_retained",
                broad["triage_bucket"],
            )
            self.assertIsNone(broad["machine_proposal"])

            cross_rows = [
                row for row in ledger if row["canonical_key"] == "119:hr:300"
            ]
            self.assertEqual(2, len(cross_rows))
            self.assertEqual(
                {"examination_supervision", "payments"},
                {row["query_id"] for row in cross_rows},
            )
            self.assertTrue(
                all(row["cross_category_membership_count"] == 2 for row in cross_rows)
            )
            self.assertTrue(
                all(
                    row["triage_bucket"]
                    == "unresolved_cross_category_measure_retained"
                    for row in cross_rows
                )
            )
            self.assertEqual(0, manifest["candidate_deletion_count"])
            self.assertEqual(0, manifest["article_absence_used_as_evidence_count"])
            self.assertTrue(
                all(
                    row["disposition"] == "unresolved"
                    for row in ledger
                    if row["triage_status"] != "ratified_existing"
                )
            )

    def test_known_outline_and_false_positive_proposals_cannot_score(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            out, manifest = fixture.build("audit-triage")
            proposals = read_ndjson(out / "proposed_decisions.ndjson")
            by_key = {
                (row["canonical_key"], row["query_id"]): row for row in proposals
            }
            known = by_key[("119:hr:100", "examination_supervision")]
            collision = by_key[("119:hr:401", "examination_supervision")]
            operative = by_key[("119:hr:600", "payments")]

            self.assertEqual("include", known["proposed_disposition"])
            self.assertEqual(
                "exclude_false_positive", collision["proposed_disposition"]
            )
            self.assertEqual(
                "exclude_false_positive", operative["proposed_disposition"]
            )
            for proposal in proposals:
                self.assertEqual("unresolved", proposal["disposition"])
                self.assertEqual("machine_proposed", proposal["adjudication_state"])
                self.assertFalse(proposal["scoring_eligible"])
                self.assertTrue(proposal["requires_human_ratification"])
                self.assertRegex(
                    proposal["official_source_url"],
                    r"^https://www\.congress\.gov/",
                )
                self.assertEqual(
                    TRIAGE.sha256_text(proposal["official_evidence_excerpt"]),
                    proposal["official_evidence_sha256"],
                )
                self.assertTrue(proposal["proposal_provenance"])

            ledger = read_ndjson(out / "triage_ledger.ndjson")
            template = next(
                row for row in ledger if row["canonical_key"] == "118:hr:400"
            )
            self.assertEqual("ratified_existing_decision", template["triage_bucket"])
            self.assertIsNone(template["machine_proposal"])
            self.assertFalse(manifest["machine_proposals_are_scoring_eligible"])
            self.assertFalse(manifest["acceptance_for_scoring"])
            triage_known = next(
                row for row in ledger if row["canonical_key"] == "119:hr:100"
            )
            self.assertEqual("unresolved", triage_known["disposition"])
            self.assertEqual("machine_proposed", triage_known["adjudication_state"])

    def test_short_alias_and_article_absence_remain_unresolved_and_auditable(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            out, manifest = fixture.build("audit-triage")
            ledger = read_ndjson(out / "triage_ledger.ndjson")
            short = next(row for row in ledger if row["canonical_key"] == "119:hr:500")
            self.assertEqual(
                "unresolved_short_alias_non_dispositive",
                short["triage_bucket"],
            )
            self.assertEqual("unresolved", short["triage_status"])
            self.assertIsNone(short["machine_proposal"])
            self.assertTrue(short["article_absence_is_never_exclusion_evidence"])
            self.assertEqual(0, manifest["generic_act_used_as_evidence_count"])
            self.assertTrue(
                (
                    out
                    / "buckets"
                    / "unresolved_short_alias_non_dispositive.ndjson"
                ).is_file()
            )
            for bucket in TRIAGE.BUCKETS:
                self.assertTrue((out / "buckets" / f"{bucket}.ndjson").is_file())

    def test_generation_is_byte_deterministic_except_directory_name(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            out1, manifest1 = fixture.build("audit-triage-one")
            out2, manifest2 = fixture.build("audit-triage-two")
            self.assertEqual(manifest1["generation_id"], manifest2["generation_id"])
            self.assertEqual(manifest1, manifest2)
            for relative, receipt in manifest1["outputs"].items():
                self.assertEqual(
                    receipt["sha256"],
                    TRIAGE.sha256_file(out2 / relative),
                    relative,
                )
                self.assertEqual(
                    (out1 / relative).read_bytes(),
                    (out2 / relative).read_bytes(),
                    relative,
                )

    def test_output_path_refuses_non_staging_location(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            fixture = Fixture(base)
            with self.assertRaisesRegex(TRIAGE.TriageError, "audit/staging/triage"):
                TRIAGE.build(
                    run_dir=fixture.run,
                    registry_path=fixture.registry,
                    relationships_dir=fixture.relationships,
                    text_dir=fixture.text,
                    article_dir=fixture.articles,
                    review_ledger_path=fixture.review,
                    out_dir=base / "plain-output",
                    workspace_root=base,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

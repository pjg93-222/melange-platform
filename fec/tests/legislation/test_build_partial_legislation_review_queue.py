from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from legislation import build_partial_legislation_review_queue as queue


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2) + "\n", encoding="utf-8"
    )


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def proposal(
    *,
    canonical_key: str,
    query_id: str,
    bucket: str,
    priority: int,
    official_title: str | None,
    official_summary: str | None = None,
    false_positive_reason: str | None = None,
    ambiguous: bool = False,
    unsupported: bool = False,
) -> dict:
    return {
        "canonical_key": canonical_key,
        "display_number": canonical_key,
        "title": f"Candidate {canonical_key}",
        "canonical_url": f"https://example.test/{canonical_key}",
        "raw_query_id": query_id,
        "review_target_query_id": query_id,
        "category_label": query_id,
        "raw_record_kind": "category",
        "raw_scoring_category": True,
        "raw_membership_non_dispositive": False,
        "source_page": 1,
        "source_rank": 1,
        "result_url": f"https://example.test/result/{canonical_key}",
        "coverage_probe_unclassified": (
            bucket
            in {
                "recall_only_possible_category_miss",
                "title_probe_parent_category_confirmation_collision",
            }
        ),
        "source_title_alias_probe_ids": [],
        "source_title_aliases": [],
        "ambiguous_short_act_alias_probe": ambiguous,
        "title_alias_probe_is_non_dispositive": False,
        "named_outline_bill": False,
        "matched_identifier_phrases": [],
        "official_title_identifier_matches": [],
        "official_summary_identifier_matches": [],
        "matched_named_act_references_non_dispositive": [],
        "rejected_non_specific_identifier_phrases": [],
        "matched_specific_title_aliases": [],
        "matched_short_act_title_aliases": [],
        "short_act_title_requires_independent_evidence": True,
        "generic_act_language_can_never_establish_relevance": True,
        "official_title": official_title,
        "official_title_type": (
            "Official Title as Introduced" if official_title else None
        ),
        "official_summary": official_summary,
        "official_source_url": (
            f"https://govinfo.test/{canonical_key}"
            if official_title or official_summary
            else None
        ),
        "official_source_sha256": (
            "a" * 64 if official_title or official_summary else None
        ),
        "parsed_evidence_sha256": (
            "b" * 64 if official_title or official_summary else None
        ),
        "official_evidence_available": bool(
            official_title or official_summary
        ),
        "unsupported_amendment": unsupported,
        "unsupported_amendment_evidence": (
            {"candidate_retained": True} if unsupported else None
        ),
        "official_text_evidence_state": "official_text_unavailable",
        "live_identifier_query_hits": [],
        "candidate_category_edge_origins": [],
        "trusted_article_action_match_count": 0,
        "trusted_article_strong_corroboration_count": 0,
        "disposition": "unresolved",
        "ratification_state": "NOT_RATIFIED",
        "human_review_required": True,
        "proposal_only": True,
        "candidate_retained": True,
        "exclusion_performed": False,
        "membership_disposition_authority": False,
        "ratification_authority": False,
        "scoring_authority": False,
        "scoring_weight": 0,
        "review_priority": priority,
        "review_bucket": bucket,
        "likely_false_positive_reason": false_positive_reason,
        "proposal_class": "fixture",
    }


def fixture(root: Path) -> Path:
    prefill_id = "f" * 64
    prefill_dir = root / "prefill" / prefill_id
    records = [
        {
            "canonical_key": "119:hr:1",
            "display_number": "H.R.1",
            "title": "Candidate 119:hr:1",
            "canonical_url": "https://example.test/119:hr:1",
            "record_kind": "bill",
        },
        {
            "canonical_key": "119:hr:2",
            "display_number": "H.R.2",
            "title": "Candidate 119:hr:2",
            "canonical_url": "https://example.test/119:hr:2",
            "record_kind": "bill",
        },
        {
            "canonical_key": "119:hamdt:3",
            "display_number": "119:hamdt:3",
            "title": "Candidate 119:hamdt:3",
            "canonical_url": "https://example.test/119:hamdt:3",
            "record_kind": "amendment",
        },
        {
            "canonical_key": "119:hr:4",
            "display_number": "H.R.4",
            "title": "Candidate 119:hr:4",
            "canonical_url": "https://example.test/119:hr:4",
            "record_kind": "bill",
        },
    ]
    definitions = [
        (
            "119:hr:1",
            "query_a",
            "official_title_or_summary_nexus",
            6,
            "Credit Union Act",
            None,
            None,
            False,
            False,
        ),
        (
            "119:hr:2",
            "query_b",
            "ambiguous_short_act_collision",
            2,
            "Unrelated Act",
            None,
            "Short Act alias matched.",
            True,
            False,
        ),
        (
            "119:hamdt:3",
            "query_c",
            "official_text_unavailable_or_not_enriched",
            9,
            None,
            None,
            None,
            False,
            True,
        ),
        (
            "119:hr:4",
            "query_d",
            "recall_only_possible_category_miss",
            1,
            "Credit Union Recall Bill",
            None,
            None,
            False,
            False,
        ),
    ]
    memberships = []
    proposals = []
    for (
        key,
        query_id,
        bucket,
        priority,
        official_title,
        official_summary,
        reason,
        ambiguous,
        unsupported,
    ) in definitions:
        record = next(row for row in records if row["canonical_key"] == key)
        memberships.append(
            {
                "query_id": query_id,
                "label": query_id,
                "scoring_category": True,
                "record_kind": "category",
                "parent_category_id": None,
                "parent_category_label": None,
                "identifier_text": "credit union",
                "title_alias": None,
                "ambiguous_short_act_alias": False,
                "membership_non_dispositive": False,
                "source_paragraph": "fixture",
                "canonical_key": key,
                "source_page": 1,
                "source_rank": 1,
                "result_url": f"https://example.test/result/{key}",
            }
        )
        row = proposal(
            canonical_key=key,
            query_id=query_id,
            bucket=bucket,
            priority=priority,
            official_title=official_title,
            official_summary=official_summary,
            false_positive_reason=reason,
            ambiguous=ambiguous,
            unsupported=unsupported,
        )
        row["display_number"] = record["display_number"]
        proposals.append(row)
    # Match the prefill's deterministic review order.
    proposals.sort(
        key=lambda row: (
            row["review_priority"],
            row["review_bucket"],
            row["review_target_query_id"],
            row["canonical_key"],
            row["raw_query_id"],
        )
    )
    unsupported_rows = [
        {
            "canonical_key": "119:hamdt:3",
            "candidate_retained": True,
            "exclusion_performed": False,
        }
    ]
    output_rows = {
        "selected_records": records,
        "selected_memberships": memberships,
        "membership_review_proposals": proposals,
        "unsupported_amendments": unsupported_rows,
    }
    outputs = {}
    for name, rows in output_rows.items():
        path = prefill_dir / f"{name}.ndjson"
        write_ndjson(path, rows)
        outputs[name] = queue.file_item(path, record_count=len(rows))
    manifest = {
        "schema": "legislation-partial-review-prefill-manifest-v1",
        "status": "PASS_NON_RATIFIED_PARTIAL",
        "prefill_id": prefill_id,
        "partial_discovery_only": True,
        "stale_complete_manifest_authority_used": False,
        "completed_query_count": 4,
        "required_query_count": 62,
        "selected_record_count": 4,
        "selected_membership_count": 4,
        "proposal_count": 4,
        "all_selected_memberships_have_proposals": True,
        "membership_logical_sha256": queue.stable_json_sha256(memberships),
        "outputs": outputs,
        "review_prefill_only": True,
        "human_ratification_required": True,
        "candidate_creation_performed": False,
        "candidate_exclusion_performed": False,
        "semantic_exclusion_performed": False,
        "ratified_decision_performed": False,
        "automatic_scoring_performed": False,
        "sync_or_promotion_performed": False,
        "production_write_performed": False,
        "absence_is_neutral": True,
        "membership_disposition_authority": False,
        "ratification_authority": False,
        "scoring_authority": False,
        "scoring_weight": 0,
        "acceptance_passed": False,
    }
    manifest_path = prefill_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path


class PartialReviewQueueTests(unittest.TestCase):
    def test_confirmation_collision_is_ambiguous_not_coverage_miss(
        self,
    ) -> None:
        row = proposal(
            canonical_key="119:hr:7056",
            query_id="probe_tailor",
            bucket=(
                "title_probe_parent_category_confirmation_collision"
            ),
            priority=2,
            official_title="To index statutory thresholds.",
        )
        self.assertEqual(queue.risk_rank(row), 1)
        self.assertTrue(queue.ambiguous(row))
        self.assertFalse(queue.exclusion_like(row))

    def test_lossless_queue_orders_risk_and_carries_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = fixture(root)
            out, report, created = queue.build_queue(
                prefill_manifest_path=manifest,
                out_root=root / "out",
            )
            self.assertTrue(created)
            self.assertEqual(
                report["status"], "PASS_LOSSLESS_PROPOSAL_ONLY"
            )
            self.assertEqual(report["selected_membership_count"], 4)
            self.assertEqual(report["staged_queue_count"], 4)
            self.assertTrue(
                report["all_memberships_present_exactly_once"]
            )
            rows = list(
                queue.iter_ndjson(out / "staged_review_queue.ndjson")
            )
            self.assertEqual(
                [row["queue_risk_rank"] for row in rows], [1, 2, 3, 4]
            )
            self.assertEqual(
                len({row["staged_membership_id"] for row in rows}), 4
            )
            for row in rows:
                self.assertIn("official_title_summary_evidence", row)
                self.assertIn("candidate_provenance", row)
                self.assertIn("query_membership_provenance", row)
                self.assertTrue(row["proposal_only"])
                self.assertTrue(row["candidate_retained"])
                self.assertFalse(row["exclusion_performed"])
                self.assertFalse(row["scoring_authority"])

    def test_visibility_views_overlap_without_culling(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = fixture(root)
            out, report, _ = queue.build_queue(
                prefill_manifest_path=manifest,
                out_root=root / "out",
            )
            main_ids = {
                row["staged_membership_id"]
                for row in queue.iter_ndjson(
                    out / "staged_review_queue.ndjson"
                )
            }
            self.assertEqual(report["exclusion_like_visibility_count"], 1)
            self.assertEqual(report["ambiguous_visibility_count"], 1)
            self.assertEqual(
                report["missing_official_evidence_visibility_count"], 1
            )
            for name in (
                "exclusion_like_visibility.ndjson",
                "ambiguous_visibility.ndjson",
                "missing_official_evidence_visibility.ndjson",
            ):
                rows = list(queue.iter_ndjson(out / name))
                self.assertEqual(len(rows), 1)
                self.assertIn(rows[0]["staged_membership_id"], main_ids)
                self.assertTrue(rows[0]["candidate_retained"])
                self.assertFalse(rows[0]["exclusion_performed"])
                self.assertTrue(
                    rows[0][
                        "visibility_is_overlapping_non_dispositive_view"
                    ]
                )

    def test_tampered_prefill_output_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = fixture(root)
            proposals = manifest.parent / "membership_review_proposals.ndjson"
            proposals.write_text(
                proposals.read_text(encoding="utf-8") + "{}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(queue.ContractError, "SHA-256"):
                queue.build_queue(
                    prefill_manifest_path=manifest,
                    out_root=root / "out",
                )

    def test_duplicate_proposal_membership_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = fixture(root)
            manifest = queue.read_object(manifest_path)
            proposals_path = (
                manifest_path.parent / "membership_review_proposals.ndjson"
            )
            rows = list(queue.iter_ndjson(proposals_path))
            rows[-1] = dict(rows[0])
            write_ndjson(proposals_path, rows)
            manifest["outputs"]["membership_review_proposals"] = (
                queue.file_item(proposals_path, record_count=4)
            )
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(
                queue.ContractError, "Duplicate proposal membership"
            ):
                queue.build_queue(
                    prefill_manifest_path=manifest_path,
                    out_root=root / "out",
                )

    def test_existing_queue_authority_tamper_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = fixture(root)
            out, _report, _ = queue.build_queue(
                prefill_manifest_path=manifest_path,
                out_root=root / "out",
            )
            queue_manifest_path = out / "manifest.json"
            queue_manifest = queue.read_object(queue_manifest_path)
            queue_manifest["scoring_authority"] = True
            write_json(queue_manifest_path, queue_manifest)
            with self.assertRaisesRegex(
                queue.ContractError, "identity drift"
            ):
                queue.build_queue(
                    prefill_manifest_path=manifest_path,
                    out_root=root / "out",
                )


if __name__ == "__main__":
    unittest.main()

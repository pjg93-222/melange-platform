from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from legislation import augment_partial_review_prefill_structured_relevance as augment


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )


def read_rows(path: Path) -> list[dict]:
    return list(augment.iter_ndjson(path))


def node(tag: str, text: str | None = None, *children: dict) -> dict:
    value: dict = {"tag": tag}
    if text is not None:
        value["text"] = text
    if children:
        value["children"] = list(children)
    return value


def source_proposal(
    *,
    key: str,
    query_id: str,
) -> dict:
    return {
        "canonical_key": key,
        "raw_query_id": query_id,
        "review_target_query_id": "payments",
        "category_label": "Payments",
        "review_bucket": "official_text_unavailable_or_not_enriched",
        "proposal_class": "unresolved_context_review",
        "source_page": 1,
        "source_rank": 1,
        "disposition": "unresolved",
        "proposed_disposition": None,
        "ratification_state": "NOT_RATIFIED",
        "human_review_required": True,
        "proposal_only": True,
        "candidate_retained": True,
        "scoring_weight": 0,
        "usable_as_membership_review_ledger": False,
        "decision_import_allowed": False,
        "scoring_input_allowed": False,
        "exclusion_performed": False,
        "membership_disposition_authority": False,
        "ratification_authority": False,
        "scoring_authority": False,
    }


def section(
    name: str,
    xpath: str,
    data: dict,
    *,
    raw_hash: str,
) -> dict:
    return {
        "status": "present",
        "source_xpath": xpath,
        "source_record_sha256": raw_hash,
        "parsed_fragment_sha256": augment.canonical_json_sha256(data),
        "data": data,
    }


def fixture(root: Path) -> tuple[Path, Path, Path]:
    registry_path = root / "registry.json"
    write_json(
        registry_path,
        {
            "categories": [
                {
                    "id": "payments",
                    "label": "Payments",
                    "scoring_category": True,
                    "identifiers": [
                        {
                            "text": "Consumer credit",
                            "source_paragraph": 1,
                        },
                        {
                            "text": "Financial Services Committee",
                            "source_paragraph": 2,
                        },
                        {
                            "text": (
                                "Financial Services Committee Referred"
                            ),
                            "source_paragraph": 2,
                        },
                        {
                            "text": "payment scam prevention",
                            "source_paragraph": 3,
                        },
                        {
                            "text": "Reform Act",
                            "source_paragraph": 4,
                        },
                        {
                            "text": "committee report credit unions",
                            "source_paragraph": 5,
                        },
                        {
                            "text": "cost estimate credit union",
                            "source_paragraph": 6,
                        },
                    ],
                    "title_aliases": [
                        "GENIUS Act",
                        "Credit Card Competition Act",
                    ],
                    "known_bills": [
                        {
                            "congress": 119,
                            "number": "S.2",
                            "compact_cite": "s2",
                            "source_paragraph": 7,
                        }
                    ],
                }
            ],
            "coverage_probes": [],
        },
    )

    prefill_dir = root / "prefill"
    records_path = prefill_dir / "selected_records.ndjson"
    memberships_path = prefill_dir / "selected_memberships.ndjson"
    proposals_path = prefill_dir / "membership_review_proposals.ndjson"
    unresolved_path = prefill_dir / "unsupported_amendments.ndjson"
    write_rows(
        records_path,
        [
            {"canonical_key": "119:hr:1"},
            {"canonical_key": "119:hamdt:9"},
        ],
    )
    write_rows(
        memberships_path,
        [
            {
                "canonical_key": "119:hr:1",
                "query_id": "payments",
            },
            {
                "canonical_key": "119:hamdt:9",
                "query_id": "payments",
            },
        ],
    )
    write_rows(
        proposals_path,
        [
            source_proposal(key="119:hr:1", query_id="payments"),
            source_proposal(key="119:hamdt:9", query_id="payments"),
        ],
    )
    unresolved_row = {
        "canonical_key": "119:hamdt:9",
        "candidate_retained": True,
        "absence_is_neutral": True,
        "proposal_only": True,
        "proposed_disposition": None,
        "scoring_weight": 0,
        "usable_as_membership_review_ledger": False,
        "decision_import_allowed": False,
        "scoring_input_allowed": False,
        "exclusion_performed": False,
        "membership_disposition_authority": False,
        "ratification_authority": False,
        "scoring_authority": False,
    }
    write_rows(unresolved_path, [unresolved_row])
    prefill_manifest_path = prefill_dir / "manifest.json"
    prefill_manifest = {
        "schema": "legislation-partial-review-prefill-manifest-v1",
        "status": "PASS_NON_RATIFIED_PARTIAL",
        "primary_discovery": augment.PRIMARY,
        "quorum_used": False,
        "partial_discovery_only": True,
        "review_prefill_only": True,
        "human_ratification_required": True,
        "absence_is_neutral": True,
        "selected_record_count": 2,
        "selected_membership_count": 2,
        "proposal_count": 2,
        "all_selected_memberships_have_proposals": True,
        "unsupported_amendment_count": 1,
        "all_sidecar_unsupported_amendments_visible": True,
        "outputs": {
            "selected_records": augment.file_item(
                records_path, record_count=2
            ),
            "selected_memberships": augment.file_item(
                memberships_path, record_count=2
            ),
            "membership_review_proposals": augment.file_item(
                proposals_path, record_count=2
            ),
            "unsupported_amendments": augment.file_item(
                unresolved_path, record_count=1
            ),
        },
        "candidate_creation_performed": False,
        "candidate_exclusion_performed": False,
        "semantic_exclusion_performed": False,
        "ratified_decision_performed": False,
        "automatic_scoring_performed": False,
        "sync_or_promotion_performed": False,
        "production_write_performed": False,
        "membership_disposition_authority": False,
        "ratification_authority": False,
        "scoring_authority": False,
        "downstream_consumption_allowed": False,
        "decision_import_allowed": False,
        "scoring_input_allowed": False,
        "acceptance_passed": False,
        "scoring_weight": 0,
    }
    write_json(prefill_manifest_path, prefill_manifest)

    structured_dir = root / "structured"
    measures_path = structured_dir / "measures.ndjson"
    structured_unresolved_path = (
        structured_dir / "unresolved_amendments.ndjson"
    )
    missing_path = structured_dir / "missing_sections.ndjson"
    empty_path = structured_dir / "empty_sections.ndjson"
    raw_hash = "a" * 64
    policy = node("policyArea", None, node("name", "Consumer credit"))
    subjects = node(
        "legislativeSubjects",
        None,
        node("item", None, node("name", "Consumer credit")),
    )
    committees = node(
        "committees",
        None,
        node(
            "item",
            None,
            node("name", "Financial Services Committee"),
            node("name", "Referred"),
        ),
    )
    related = node(
        "relatedBills",
        None,
        node(
            "item",
            None,
            node("title", "Credit Card Competition Act"),
            node("congress", "119"),
            node("number", "2"),
            node("type", "S"),
        ),
    )
    amendments = node(
        "amendments",
        None,
        node(
            "amendment",
            None,
            node(
                "purpose",
                (
                    "Payment scam prevention under the GENIUS Act "
                    "and Reform Act."
                ),
            ),
        ),
    )
    laws = node(
        "laws",
        None,
        node("item", None, node("name", "Consumer credit law")),
    )
    reports = node(
        "committeeReports",
        None,
        node(
            "committeeReport",
            None,
            node("citation", "Committee report credit unions"),
        ),
    )
    cbo = node(
        "cboCostEstimates",
        None,
        node(
            "item",
            None,
            node("description", "Cost estimate credit union"),
        ),
    )
    sections = {
        "policy_area": section(
            "policy_area", "bill/policyArea", policy, raw_hash=raw_hash
        ),
        "legislative_subjects": section(
            "legislative_subjects",
            "bill/subjects/legislativeSubjects",
            subjects,
            raw_hash=raw_hash,
        ),
        "committees": section(
            "committees",
            "bill/committees",
            committees,
            raw_hash=raw_hash,
        ),
        "related_bills": section(
            "related_bills",
            "bill/relatedBills",
            related,
            raw_hash=raw_hash,
        ),
        "amendments": section(
            "amendments",
            "bill/amendments",
            amendments,
            raw_hash=raw_hash,
        ),
        "laws": section(
            "laws", "bill/laws", laws, raw_hash=raw_hash
        ),
        "committee_reports": section(
            "committee_reports",
            "bill/committeeReports",
            reports,
            raw_hash=raw_hash,
        ),
        "cbo_cost_estimates": section(
            "cbo_cost_estimates",
            "bill/cboCostEstimates",
            cbo,
            raw_hash=raw_hash,
        ),
    }
    measure = {
        "schema": (
            "govinfo-billstatus-structured-relevance-measure-v1"
        ),
        "canonical_key": "119:hr:1",
        "sections": sections,
        "provenance": {
            "authority": "U.S. Government Publishing Office / Congress.gov",
            "source_url": (
                "https://www.govinfo.gov/bulkdata/BILLSTATUS/119/hr/"
                "BILLSTATUS-119hr1.xml"
            ),
            "raw_source": {"sha256": raw_hash},
        },
    }
    write_rows(measures_path, [measure])
    write_rows(
        structured_unresolved_path,
        [{"canonical_key": "119:hamdt:9"}],
    )
    write_rows(missing_path, [])
    write_rows(empty_path, [])
    candidates_path = structured_dir / "advanced_candidates.ndjson"
    exclusion_path = structured_dir / "exclusion_ledger.ndjson"
    exclusion_provenance_path = (
        structured_dir / "exclusion_provenance.ndjson"
    )
    snapshot_plan_path = structured_dir / "snapshot_plan.json"
    worker_path = structured_dir / "worker_manifest.json"
    write_rows(
        candidates_path,
        [
            {"canonical_key": "119:hr:1"},
            {"canonical_key": "119:hamdt:9"},
        ],
    )
    write_rows(exclusion_path, [])
    write_rows(
        exclusion_provenance_path,
        [{"canonical_key": "119:hr:999", "reason": "preserved"}],
    )
    write_json(snapshot_plan_path, {"schema": "test-plan"})
    write_json(worker_path, {"schema": "test-worker"})
    structured_manifest_path = structured_dir / "manifest.json"
    structured_manifest = {
        "schema": (
            "govinfo-billstatus-structured-relevance-sidecar-v1"
        ),
        "status": "COMPLETE_STAGED_ONLY",
        "review_prefill_only": True,
        "absence_is_neutral": True,
        "network_executed": False,
        "coverage": {
            "candidate_partition_closed": True,
            "all_amendment_candidates_unresolved": True,
            "every_top_level_has_exactly_one_receipt": True,
            "raw_hashes_validated": True,
            "top_level_failed": 0,
            "selected_candidate_count": 2,
            "top_level_structured": 1,
            "nested_amendments_retained_unresolved": 1,
        },
        "outputs": {
            "measures": augment.file_item(
                measures_path,
                record_count=1,
                relative=True,
            ),
            "missing_sections": augment.file_item(
                missing_path,
                record_count=0,
                relative=True,
            ),
            "empty_sections": augment.file_item(
                empty_path,
                record_count=0,
                relative=True,
            ),
            "unresolved_amendments": augment.file_item(
                structured_unresolved_path,
                record_count=1,
                relative=True,
            ),
        },
        "inputs": {
            "advanced_search_candidates": augment.file_item(
                candidates_path, record_count=2
            ),
            "exclusion_ledger": augment.file_item(exclusion_path),
            "exclusion_provenance": [
                augment.file_item(exclusion_provenance_path)
            ],
            "snapshot_plan": augment.file_item(snapshot_plan_path),
            "worker_manifest": augment.file_item(worker_path),
        },
        "candidate_creation_performed": False,
        "semantic_inclusion_performed": False,
        "semantic_exclusion_performed": False,
        "ratification_performed": False,
        "automatic_scoring_performed": False,
        "sync_or_promotion_performed": False,
        "production_write_performed": False,
        "ratification_authority": False,
        "scoring_authority": False,
        "acceptance_passed": False,
    }
    write_json(structured_manifest_path, structured_manifest)
    return prefill_manifest_path, structured_manifest_path, registry_path


class StructuredPrefillAugmentationTest(unittest.TestCase):
    def test_temporary_cleanup_handles_read_only_directory(self) -> None:
        with TemporaryDirectory() as temp:
            parent = Path(temp)
            temporary = parent / ".structured-augmentation.read-only"
            temporary.mkdir()
            (temporary / "evidence.ndjson").write_text(
                "{}\n",
                encoding="utf-8",
            )
            os.chmod(
                temporary,
                stat.S_IREAD | stat.S_IEXEC,
            )
            augment.remove_temporary_tree(
                temporary,
                expected_parent=parent,
            )
            self.assertFalse(temporary.exists())

    def test_semantic_rollover_binding_closes_exact_inputs(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.json"
            current_strict = root / "current.strict.json"
            reused_strict = root / "reused.strict.json"
            write_json(registry, {"categories": []})
            write_json(current_strict, {"schema": "strict-current"})
            write_json(reused_strict, {"schema": "strict-reused"})
            current_strict_item = augment.file_item(current_strict)
            reused_strict_item = augment.file_item(reused_strict)
            current_plan = root / "current.plan.json"
            reused_plan = root / "reused.plan.json"
            plan_base = {
                "record_tasks": [],
                "unsupported_candidates": [],
            }
            write_json(
                current_plan,
                {
                    **plan_base,
                    "snapshot_contract": {
                        "strict_manifest": current_strict_item
                    },
                },
            )
            write_json(
                reused_plan,
                {
                    **plan_base,
                    "snapshot_contract": {
                        "strict_manifest": reused_strict_item
                    },
                },
            )
            query_rows: list[dict] = []
            candidate_identity = {
                "selected_strict_candidate_count": 1,
                "cumulative_record_count": 2,
                "canonical_keys_sha256": "1" * 64,
                "aggregate_rows_sha256": "2" * 64,
                "advanced_search_line_receipts_sha256": "3" * 64,
                "candidate_partition": {
                    "all": 2,
                    "top_level_enrichable": 1,
                    "nested_only_amendments": 1,
                },
                "record_tasks_sha256": augment.collector_json_sha256(
                    []
                ),
                "unsupported_candidates_sha256": (
                    augment.collector_json_sha256([])
                ),
            }
            identity = {
                "schema": "billstatus-plan-semantic-identity-v1",
                "primary_discovery": augment.PRIMARY,
                "registry_sha256": augment.sha256_file(registry),
                "completed_query_count": 0,
                "selected_query_ids": [],
                "query_logical_receipts": query_rows,
                "query_logical_sha256": augment.collector_json_sha256(
                    query_rows
                ),
                "candidate_identity": candidate_identity,
                "candidate_logical_sha256": (
                    augment.collector_json_sha256(candidate_identity)
                ),
                "cumulative_identity": None,
            }
            identity["semantic_sha256"] = (
                augment.collector_json_sha256(identity)
            )
            binding = {
                "schema": "billstatus-semantic-rollover-binding-v1",
                "status": "PASS",
                "mode": "SEMANTIC_EQUIVALENCE",
                "current_plan": augment.file_item(current_plan),
                "reused_plan": augment.file_item(reused_plan),
                "current_strict_manifest": current_strict_item,
                "reused_strict_manifest": reused_strict_item,
                "semantic_identity": identity,
                "query_logical_hashes_equal": True,
                "candidate_logical_hashes_equal": True,
                "physical_receipts_equal": False,
                "network_reuse_only": True,
                "authoritative_for_discovery": False,
                "authoritative_for_semantic_exclusion": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
                "network_executed": False,
                "sync_or_promotion_performed": False,
            }
            checkpoint = root / "checkpoint.json"
            write_json(
                checkpoint,
                {
                    "coverage": {
                        "billstatus_enrichment_completeness_evidence": {
                            "semantic_rollover_binding": binding
                        }
                    }
                },
            )
            observed, _ = augment.validate_rollover_binding(
                checkpoint_path=checkpoint,
                prefill={
                    "bindings": {
                        "selected_partial_manifest_sha256": (
                            current_strict_item["sha256"]
                        )
                    },
                    "completed_query_count": 0,
                    "selected_record_count": 1,
                },
                structured={
                    "inputs": {
                        "snapshot_plan": augment.file_item(
                            reused_plan
                        )
                    },
                    "coverage": {
                        "selected_candidate_count": 2,
                        "top_level_structured": 1,
                        "nested_amendments_retained_unresolved": 1,
                    },
                },
                registry_path=registry,
            )
            self.assertEqual(
                observed["semantic_identity"]["semantic_sha256"],
                identity["semantic_sha256"],
            )
            checkpoint_value = augment.read_object(checkpoint)
            checkpoint_value["coverage"][
                "billstatus_enrichment_completeness_evidence"
            ]["semantic_rollover_binding"][
                "candidate_logical_hashes_equal"
            ] = False
            write_json(checkpoint, checkpoint_value)
            with self.assertRaisesRegex(
                augment.ContractError, "zero-network PASS"
            ):
                augment.validate_rollover_binding(
                    checkpoint_path=checkpoint,
                    prefill={
                        "bindings": {
                            "selected_partial_manifest_sha256": (
                                current_strict_item["sha256"]
                            )
                        },
                        "completed_query_count": 0,
                        "selected_record_count": 1,
                    },
                    structured={
                        "inputs": {
                            "snapshot_plan": augment.file_item(
                                reused_plan
                            )
                        },
                        "coverage": {
                            "selected_candidate_count": 2,
                            "top_level_structured": 1,
                            "nested_amendments_retained_unresolved": 1,
                        },
                    },
                    registry_path=registry,
                )

    def test_builds_lossless_content_addressed_evidence_overlay(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            prefill, structured, registry = fixture(root)
            pointer = root / "current.json"
            out_dir, manifest, created = augment.build_augmentation(
                prefill_manifest_path=prefill,
                structured_manifest_path=structured,
                registry_path=registry,
                out_root=root / "out" / "snapshots",
                pointer_path=pointer,
            )
            self.assertTrue(created)
            self.assertEqual(
                manifest["status"], "PASS_STAGED_EVIDENCE_ONLY"
            )
            self.assertEqual(
                manifest["coverage"]["membership_evidence_rows"], 2
            )
            self.assertTrue(
                manifest["coverage"][
                    "every_membership_preserved_exactly_once"
                ]
            )
            self.assertEqual(
                manifest["coverage"]["unresolved_amendments_preserved"],
                1,
            )
            self.assertTrue(
                manifest["coverage"][
                    "unresolved_amendment_bytes_preserved"
                ]
            )
            self.assertEqual(
                manifest["coverage"][
                    "structured_candidates_preserved_by_receipt"
                ],
                2,
            )
            self.assertEqual(
                manifest["coverage"][
                    "exclusion_provenance_receipts_preserved"
                ],
                1,
            )
            self.assertTrue(
                all(
                    manifest["match_counts"]["by_section"][section] > 0
                    for section in augment.SECTION_ORDER
                )
            )
            self.assertFalse(manifest["automatic_scoring_performed"])
            self.assertFalse(manifest["sync_or_promotion_performed"])
            self.assertFalse(manifest["production_write_performed"])
            self.assertFalse(manifest["network_executed"])
            self.assertFalse(
                manifest["candidate_membership_review_overwritten"]
            )
            self.assertFalse(manifest["currentness"]["confirmed"])

            rows = read_rows(
                out_dir / "membership_evidence_additions.ndjson"
            )
            self.assertEqual(len(rows), 2)
            first = rows[0]
            additions = first[
                "official_structured_evidence_additions"
            ]
            self.assertFalse(
                any(
                    row["identifier_text"]
                    == "Financial Services Committee Referred"
                    for row in additions
                ),
                "An identifier must never match across official leaves",
            )
            self.assertEqual(
                {row["section"] for row in additions},
                set(augment.SECTION_ORDER),
            )
            short = next(
                row
                for row in additions
                if row["identifier_text"] == "GENIUS Act"
            )
            self.assertEqual(
                short["specificity_class"], "context_only_short_act"
            )
            self.assertFalse(short["independent_review_signal"])
            self.assertFalse(short["dispositive"])
            generic = next(
                row
                for row in additions
                if row["identifier_text"] == "Reform Act"
            )
            self.assertEqual(
                generic["specificity_class"],
                "context_only_generic_act",
            )
            self.assertFalse(generic["independent_review_signal"])
            related = [
                row
                for row in additions
                if row["section"] == "related_bills"
            ]
            self.assertTrue(
                any(
                    row["match_rule"]
                    == "exact_related_bill_canonical_identity"
                    for row in related
                )
            )
            self.assertTrue(
                all(
                    row["source_xpath"]
                    and row["source_record_sha256"] == "a" * 64
                    and row["element_path"]
                    and row["proposal_only"]
                    and not row["dispositive"]
                    for row in additions
                )
            )
            second = rows[1]
            self.assertTrue(second["unresolved_amendment"])
            self.assertFalse(second["structured_measure_available"])
            self.assertEqual(
                second["official_structured_evidence_additions"], []
            )
            self.assertEqual(
                (
                    out_dir / "unresolved_amendments.ndjson"
                ).read_bytes(),
                (
                    Path(
                        augment.read_object(prefill)["outputs"][
                            "unsupported_amendments"
                        ]["path"]
                    )
                ).read_bytes(),
            )
            pointer_value = augment.read_object(pointer)
            self.assertEqual(
                pointer_value["augmentation_id"],
                manifest["augmentation_id"],
            )
            self.assertFalse(pointer_value["currentness_confirmed"])
            self.assertFalse(
                pointer_value["eligible_as_current_prefill_augmentation"]
            )

            out_again, manifest_again, created_again = (
                augment.build_augmentation(
                    prefill_manifest_path=prefill,
                    structured_manifest_path=structured,
                    registry_path=registry,
                    out_root=root / "out" / "snapshots",
                    pointer_path=pointer,
                )
            )
            self.assertFalse(created_again)
            self.assertEqual(out_again, out_dir)
            self.assertEqual(
                manifest_again["augmentation_id"],
                manifest["augmentation_id"],
            )

    def test_tampered_content_addressed_output_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            prefill, structured, registry = fixture(root)
            out_dir, _, _ = augment.build_augmentation(
                prefill_manifest_path=prefill,
                structured_manifest_path=structured,
                registry_path=registry,
                out_root=root / "out" / "snapshots",
            )
            evidence_path = (
                out_dir / "membership_evidence_additions.ndjson"
            )
            body = evidence_path.read_bytes()
            evidence_path.write_bytes(
                body.replace(b"Consumer credit", b"Consumer credix", 1)
            )
            with self.assertRaisesRegex(
                augment.ContractError,
                "SHA-256 mismatch",
            ):
                augment.build_augmentation(
                    prefill_manifest_path=prefill,
                    structured_manifest_path=structured,
                    registry_path=registry,
                    out_root=root / "out" / "snapshots",
                )

    def test_source_proposal_authority_tamper_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            prefill, structured, registry = fixture(root)
            prefill_manifest = augment.read_object(prefill)
            proposal_path = Path(
                prefill_manifest["outputs"][
                    "membership_review_proposals"
                ]["path"]
            )
            rows = read_rows(proposal_path)
            rows[0]["scoring_authority"] = True
            write_rows(proposal_path, rows)
            prefill_manifest["outputs"][
                "membership_review_proposals"
            ] = augment.file_item(proposal_path, record_count=2)
            write_json(prefill, prefill_manifest)
            with self.assertRaisesRegex(
                augment.ContractError,
                "Source proposal authority drift",
            ):
                augment.build_augmentation(
                    prefill_manifest_path=prefill,
                    structured_manifest_path=structured,
                    registry_path=registry,
                    out_root=root / "out" / "snapshots",
                )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from legislation import build_partial_legislation_review_prefill as prefill


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


def fixture(root: Path) -> tuple[Path, Path, Path]:
    registry_path = root / "registry.json"
    registry = {
        "categories": [
            {
                "id": "supervision",
                "label": "Supervision",
                "scoring_category": True,
                "identifiers": [
                    {"text": "examination credit union"},
                    {"text": "Act"},
                ],
                "title_aliases": ["FIRM Act"],
                "known_bills": [],
            }
        ],
        "coverage_probes": [
            {
                "id": "probe_firm",
                "label": "FIRM Act probe",
                "scoring_category": False,
                "record_kind": "coverage_probe",
                "parent_category_id": "supervision",
                "title_alias": "FIRM Act",
                "ambiguous_short_act_alias": True,
                "membership_non_dispositive": True,
            }
        ],
    }
    write_json(registry_path, registry)
    registry_hash = prefill.sha256_file(registry_path)
    run = root / "run"
    query_file = run / "queries" / "supervision.json"
    records = [
        {
            "record_kind": "bill",
            "congress": 119,
            "type": "hr",
            "number": 1,
            "canonical_key": "119:hr:1",
            "canonical_url": (
                "https://www.congress.gov/bill/119th-congress/"
                "house-bill/1"
            ),
            "display_number": "H.R.1",
            "title": "Unrelated display title",
            "purpose": None,
            "source_query_id": "supervision",
            "source_page": 1,
            "source_rank": 1,
            "result_url": "https://www.congress.gov/result/1",
        },
        {
            "record_kind": "amendment",
            "congress": 119,
            "type": "hamdt",
            "number": 2,
            "canonical_key": "119:hamdt:2",
            "canonical_url": (
                "https://www.congress.gov/amendment/119th-congress/"
                "house-amendment/2"
            ),
            "display_number": "H.Amdt.2",
            "title": "Amendment",
            "purpose": None,
            "source_query_id": "supervision",
            "source_page": 1,
            "source_rank": 2,
            "result_url": "https://www.congress.gov/result/2",
        },
    ]
    query = {
        "schema": "congress-advanced-search-query-v1",
        "primary_instrument": prefill.PRIMARY,
        "query_id": "supervision",
        "label": "Supervision",
        "scoring_category": True,
        "record_kind": "category",
        "parent_category_id": None,
        "title_alias": None,
        "ambiguous_short_act_alias": False,
        "membership_non_dispositive": False,
        "registry_sha256": registry_hash,
        "result_total": 2,
        "unique_record_count": 2,
        "records_sha256": prefill.stable_json_sha256(records),
        "validation": {
            "exact_result_total": True,
            "unique_result_total": True,
            "every_page_exact": True,
        },
        "records": records,
    }
    write_json(query_file, query)
    manifest_path = run / "manifest.partial.fixture.json"
    manifest = {
        "schema": "acu-congress-advanced-pages-manifest-v1",
        "status": "failed",
        "completed_at": None,
        "primary_instrument": prefill.PRIMARY,
        "quorum_used": False,
        "registry_sha256": registry_hash,
        "selected_query_ids": ["supervision", "probe_firm"],
        "queries": [
            {
                "query_id": "supervision",
                "query_file": "queries/supervision.json",
                "label": "Supervision",
                "scoring_category": True,
                "record_kind": "category",
                "parent_category_id": None,
                "title_alias": None,
                "ambiguous_short_act_alias": False,
                "membership_non_dispositive": False,
                "result_total": 2,
                "unique_record_count": 2,
                "records_sha256": prefill.stable_json_sha256(records),
                "validation": query["validation"],
            }
        ],
    }
    write_json(manifest_path, manifest)

    sidecar_dir = root / "sidecar"
    metadata_path = sidecar_dir / "measure_metadata.ndjson"
    unsupported_path = sidecar_dir / "verification.ndjson"
    gaps_path = sidecar_dir / "gaps.ndjson"
    write_ndjson(
        metadata_path,
        [
            {
                "canonical_key": "119:hr:1",
                "official_title": "Credit Union Examination Reform Act",
                "official_title_type": "Official Title as Introduced",
                "summary": (
                    "Requires examination credit union procedures."
                ),
                "official_source_url": (
                    "https://www.govinfo.gov/bulkdata/BILLSTATUS/119/hr/"
                    "BILLSTATUS-119hr1.xml"
                ),
                "official_source_sha256": "a" * 64,
                "parsed_evidence_sha256": "b" * 64,
            }
        ],
    )
    unsupported_rows = [
        {
            "canonical_key": "119:hamdt:2",
            "disposition": (
                "retained_unresolved_no_standalone_top_level_package"
            ),
            "candidate_retained": True,
            "absence_is_neutral": True,
            "membership_disposition_authority": False,
            "ratification_authority": False,
            "scoring_authority": False,
        },
        {
            "canonical_key": "119:samdt:9",
            "disposition": (
                "retained_unresolved_no_standalone_top_level_package"
            ),
            "candidate_retained": True,
            "absence_is_neutral": True,
            "membership_disposition_authority": False,
            "ratification_authority": False,
            "scoring_authority": False,
        },
    ]
    write_ndjson(unsupported_path, unsupported_rows)
    write_ndjson(gaps_path, [{"gap": "visible"}])
    outputs = {
        "measure_metadata": prefill.file_item(
            metadata_path, record_count=1
        ),
        "verification_ledger": prefill.file_item(
            unsupported_path, record_count=2
        ),
        "source_gap_ledger": prefill.file_item(gaps_path, record_count=1),
    }
    sidecar_manifest = sidecar_dir / "manifest.json"
    write_json(
        sidecar_manifest,
        {
            "schema": "congress-sponsorship-enrichment-manifest-v1",
            "adapter_schema": "govinfo-billstatus-review-sidecar-v1",
            "status": "complete",
            "official_source_only": True,
            "review_prefill_only": True,
            "snapshot_only": True,
            "partial_discovery_snapshot": True,
            "absence_is_neutral": True,
            "production_write_performed": False,
            "sync_or_promotion_performed": False,
            "candidate_creation_performed": False,
            "semantic_exclusion_performed": False,
            "ratified_decision_performed": False,
            "automatic_scoring_performed": False,
            "membership_disposition_authority": False,
            "ratification_authority": False,
            "scoring_authority": False,
            "outputs": outputs,
        },
    )
    return manifest_path, registry_path, sidecar_manifest


class PartialReviewPrefillTests(unittest.TestCase):
    def test_builds_non_authoritative_complete_membership_prefill(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry, sidecar = fixture(root)
            out, report, created = prefill.build_prefill(
                manifest_path=manifest,
                registry_path=registry,
                sidecar_manifest_path=sidecar,
                out_root=root / "out",
            )
            self.assertTrue(created)
            self.assertEqual(report["status"], "PASS_NON_RATIFIED_PARTIAL")
            self.assertEqual(report["selected_membership_count"], 2)
            self.assertEqual(report["proposal_count"], 2)
            self.assertTrue(
                report["all_selected_memberships_have_proposals"]
            )
            self.assertFalse(report["acceptance_passed"])
            self.assertFalse(report["scoring_authority"])
            proposals = list(
                prefill.iter_ndjson(
                    out / "membership_review_proposals.ndjson"
                )
            )
            bill = next(
                row for row in proposals if row["canonical_key"] == "119:hr:1"
            )
            self.assertEqual(
                bill["review_bucket"], "official_title_or_summary_nexus"
            )
            self.assertEqual(bill["disposition"], "unresolved")
            self.assertIsNone(bill["proposed_disposition"])
            self.assertEqual(bill["scoring_weight"], 0)
            self.assertTrue(bill["proposal_only"])
            self.assertFalse(bill["usable_as_membership_review_ledger"])
            self.assertFalse(bill["decision_import_allowed"])
            self.assertFalse(bill["scoring_input_allowed"])
            self.assertIn(
                "Act", bill["rejected_non_specific_identifier_phrases"]
            )
            self.assertIn(
                "credit union", bill["official_summary_domain_anchors"]
            )
            amendment = next(
                row
                for row in proposals
                if row["canonical_key"] == "119:hamdt:2"
            )
            self.assertTrue(amendment["unsupported_amendment"])

    def test_all_unsupported_amendments_include_historical_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry, sidecar = fixture(root)
            out, report, _ = prefill.build_prefill(
                manifest_path=manifest,
                registry_path=registry,
                sidecar_manifest_path=sidecar,
                out_root=root / "out",
            )
            self.assertEqual(report["unsupported_amendment_count"], 2)
            self.assertEqual(
                report["unsupported_amendments_historical_only_count"], 1
            )
            rows = list(
                prefill.iter_ndjson(out / "unsupported_amendments.ndjson")
            )
            historical = next(
                row
                for row in rows
                if row["canonical_key"] == "119:samdt:9"
            )
            self.assertTrue(
                historical["historical_only_relative_to_current_partial"]
            )
            self.assertTrue(historical["candidate_retained"])
            self.assertFalse(historical["exclusion_performed"])

    def test_query_record_hash_tamper_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry, sidecar = fixture(root)
            query_path = manifest.parent / "queries" / "supervision.json"
            query = prefill.read_object(query_path)
            query["records"][0]["title"] = "tampered"
            write_json(query_path, query)
            with self.assertRaisesRegex(
                prefill.ContractError, "receipt drift"
            ):
                prefill.build_prefill(
                    manifest_path=manifest,
                    registry_path=registry,
                    sidecar_manifest_path=sidecar,
                    out_root=root / "out",
                )

    def test_complete_manifest_cannot_authorize_partial_prefill(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry, sidecar = fixture(root)
            payload = prefill.read_object(manifest)
            payload["status"] = "complete"
            payload["completed_at"] = "2026-07-26T00:00:00Z"
            write_json(manifest, payload)
            with self.assertRaisesRegex(
                prefill.ContractError, "Complete/stale"
            ):
                prefill.build_prefill(
                    manifest_path=manifest,
                    registry_path=registry,
                    sidecar_manifest_path=sidecar,
                    out_root=root / "out",
                )

    def test_immutable_artifact_is_idempotently_reused(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry, sidecar = fixture(root)
            first, _, created = prefill.build_prefill(
                manifest_path=manifest,
                registry_path=registry,
                sidecar_manifest_path=sidecar,
                out_root=root / "out",
            )
            second, _, reused_created = prefill.build_prefill(
                manifest_path=manifest,
                registry_path=registry,
                sidecar_manifest_path=sidecar,
                out_root=root / "out",
            )
            self.assertTrue(created)
            self.assertFalse(reused_created)
            self.assertEqual(first, second)

    def test_summary_act_reference_cannot_become_title_nexus(self) -> None:
        registry = {
            "categories": [
                {
                    "id": "tax",
                    "label": "Tax",
                    "scoring_category": True,
                    "identifiers": [
                        {"text": "credit union tax exemption"}
                    ],
                    "title_aliases": ["One Big Beautiful Bill Act"],
                    "known_bills": [],
                }
            ],
            "coverage_probes": [],
        }
        records = [
            {
                "canonical_key": "119:hr:10",
                "display_number": "H.R.10",
                "title": "Unrelated Health Bill",
                "canonical_url": "https://www.congress.gov/bill/119/10",
            }
        ]
        memberships = [
            {
                "canonical_key": "119:hr:10",
                "query_id": "tax",
                "record_kind": "category",
                "scoring_category": True,
                "membership_non_dispositive": False,
            }
        ]
        proposals = prefill.build_proposals(
            records,
            memberships,
            registry,
            {
                "119:hr:10": {
                    "official_title": "To revise health programs.",
                    "summary": (
                        "This bill repeals provisions of the One Big "
                        "Beautiful Bill Act."
                    ),
                }
            },
            {},
        )
        row = proposals[0]
        self.assertEqual(row["matched_specific_title_aliases"], [])
        self.assertEqual(
            row["matched_specific_title_alias_references_non_dispositive"],
            ["One Big Beautiful Bill Act"],
        )
        self.assertFalse(row["official_title_nexus"])
        self.assertFalse(row["official_summary_nexus"])
        self.assertEqual(row["review_bucket"], "named_act_reference_only")

    def test_exact_primary_specific_title_alias_remains_visible(self) -> None:
        registry = {
            "categories": [
                {
                    "id": "tax",
                    "label": "Tax",
                    "scoring_category": True,
                    "identifiers": [
                        {"text": "credit union tax exemption"}
                    ],
                    "title_aliases": ["One Big Beautiful Bill Act"],
                    "known_bills": [],
                }
            ],
            "coverage_probes": [],
        }
        records = [
            {
                "canonical_key": "119:hr:1",
                "display_number": "H.R.1",
                "title": "One Big Beautiful Bill Act of 2025",
                "canonical_url": "https://www.congress.gov/bill/119/1",
            }
        ]
        memberships = [
            {
                "canonical_key": "119:hr:1",
                "query_id": "tax",
                "record_kind": "category",
                "scoring_category": True,
                "membership_non_dispositive": False,
            }
        ]
        row = prefill.build_proposals(
            records,
            memberships,
            registry,
            {
                "119:hr:1": {
                    "official_title": "To provide for reconciliation.",
                    "summary": None,
                }
            },
            {},
        )[0]
        self.assertEqual(
            row["matched_specific_title_aliases"],
            ["One Big Beautiful Bill Act"],
        )
        self.assertEqual(
            row["discovery_primary_specific_title_aliases"],
            ["One Big Beautiful Bill Act"],
        )
        self.assertEqual(
            row["review_bucket"], "official_title_or_summary_nexus"
        )

    def test_ambiguous_collision_does_not_suppress_official_nexus(self) -> None:
        registry = {
            "categories": [
                {
                    "id": "supervision",
                    "label": "Supervision",
                    "scoring_category": True,
                    "identifiers": [
                        {
                            "text": (
                                "Federal Financial Institutions "
                                "Examination Council"
                            )
                        }
                    ],
                    "title_aliases": ["FIRM Act"],
                    "known_bills": [],
                }
            ],
            "coverage_probes": [
                {
                    "id": "probe_firm",
                    "label": "FIRM Act probe",
                    "scoring_category": False,
                    "record_kind": "coverage_probe",
                    "parent_category_id": "supervision",
                    "title_alias": "FIRM Act",
                    "ambiguous_short_act_alias": True,
                    "membership_non_dispositive": True,
                }
            ],
        }
        records = [
            {
                "canonical_key": "119:hr:20",
                "display_number": "H.R.20",
                "title": "FIRM Act",
                "canonical_url": "https://www.congress.gov/bill/119/20",
            }
        ]
        memberships = [
            {
                "canonical_key": "119:hr:20",
                "query_id": "supervision",
                "record_kind": "category",
                "scoring_category": True,
                "membership_non_dispositive": False,
            },
            {
                "canonical_key": "119:hr:20",
                "query_id": "probe_firm",
                "parent_category_id": "supervision",
                "record_kind": "coverage_probe",
                "scoring_category": False,
                "membership_non_dispositive": True,
                "ambiguous_short_act_alias": True,
                "title_alias": "FIRM Act",
            },
        ]
        proposals = prefill.build_proposals(
            records,
            memberships,
            registry,
            {
                "119:hr:20": {
                    "official_title": (
                        "To amend the Federal Financial Institutions "
                        "Examination Council Act."
                    ),
                    "summary": None,
                }
            },
            {},
        )
        category = next(
            row for row in proposals if row["raw_query_id"] == "supervision"
        )
        recall = next(
            row for row in proposals if row["raw_query_id"] == "probe_firm"
        )
        self.assertEqual(
            category["review_bucket"],
            "ambiguous_short_act_collision_with_independent_official_nexus",
        )
        self.assertTrue(category["official_title_nexus"])
        self.assertEqual(
            recall["review_bucket"],
            "title_probe_parent_category_confirmation_collision",
        )
        self.assertFalse(recall["recall_only_miss_visibility_preserved"])
        self.assertTrue(
            recall[
                "title_probe_parent_category_confirmation_"
                "visibility_preserved"
            ]
        )
        self.assertTrue(
            recall["selected_parent_category_membership_present"]
        )
        self.assertIn(
            "official_title_identifier_phrase",
            recall["recall_trigger_signals"],
        )

    def test_all_five_named_outline_probe_edges_are_confirmations_not_misses(
        self,
    ) -> None:
        identities = [
            ("119:hr:2702", "H.R.2702", "FIRM Act", "probe_firm"),
            (
                "119:hr:3380",
                "H.R.3380",
                "TAILOR Act of 2025",
                "probe_tailor",
            ),
            (
                "119:hr:7056",
                "H.R.7056",
                "Community Bank Regulatory Tailoring Act",
                "probe_tailor",
            ),
            (
                "119:s:427",
                "S.427",
                "TAILOR Act of 2025",
                "probe_tailor",
            ),
            ("119:s:875", "S.875", "FIRM Act", "probe_firm"),
        ]
        registry = {
            "categories": [
                {
                    "id": "examination_supervision",
                    "label": "Examination & Supervision",
                    "scoring_category": True,
                    "identifiers": [
                        {"text": "examination credit union"},
                        {"text": "reputational risk supervision"},
                    ],
                    "title_aliases": [
                        "FIRM Act",
                        "TAILOR Act",
                        "Community Bank Regulatory Tailoring Act",
                    ],
                    "known_bills": [
                        {
                            "congress": 119,
                            "compact_cite": key.replace("119:", "").replace(
                                ":", ""
                            ),
                        }
                        for key, _number, _title, _probe in identities
                    ],
                }
            ],
            "coverage_probes": [
                {
                    "id": "probe_firm",
                    "label": "FIRM Act probe",
                    "scoring_category": False,
                    "record_kind": "coverage_probe",
                    "parent_category_id": "examination_supervision",
                    "title_alias": "FIRM Act",
                    "ambiguous_short_act_alias": True,
                    "membership_non_dispositive": True,
                },
                {
                    "id": "probe_tailor",
                    "label": "TAILOR Act probe",
                    "scoring_category": False,
                    "record_kind": "coverage_probe",
                    "parent_category_id": "examination_supervision",
                    "title_alias": "TAILOR Act",
                    "ambiguous_short_act_alias": True,
                    "membership_non_dispositive": True,
                },
            ],
        }
        records = [
            {
                "canonical_key": key,
                "display_number": number,
                "title": title,
                "canonical_url": f"https://www.congress.gov/{key}",
            }
            for key, number, title, _probe in identities
        ]
        memberships = []
        for rank, (key, _number, _title, probe) in enumerate(
            identities, 1
        ):
            memberships.extend(
                [
                    {
                        "canonical_key": key,
                        "query_id": "examination_supervision",
                        "record_kind": "category",
                        "scoring_category": True,
                        "membership_non_dispositive": False,
                        "source_page": 8,
                        "source_rank": 1700 + rank,
                        "result_url": f"https://example.test/category/{key}",
                    },
                    {
                        "canonical_key": key,
                        "query_id": probe,
                        "parent_category_id": "examination_supervision",
                        "record_kind": "coverage_probe",
                        "scoring_category": False,
                        "membership_non_dispositive": True,
                        "ambiguous_short_act_alias": True,
                        "title_alias": (
                            "FIRM Act"
                            if probe == "probe_firm"
                            else "TAILOR Act"
                        ),
                        "source_page": 1,
                        "source_rank": rank,
                        "result_url": f"https://example.test/probe/{key}",
                    },
                ]
            )
        metadata = {
            key: {
                "official_title": (
                    "To regulate Federal financial institutions."
                ),
                "summary": None,
            }
            for key, _number, _title, _probe in identities
        }
        proposals = prefill.build_proposals(
            records,
            memberships,
            registry,
            metadata,
            {},
        )
        confirmations = [
            row
            for row in proposals
            if row["review_bucket"]
            == "title_probe_parent_category_confirmation_collision"
        ]
        self.assertEqual(len(proposals), len(memberships))
        self.assertEqual(len(confirmations), 5)
        self.assertEqual(
            {row["canonical_key"] for row in confirmations},
            {key for key, _number, _title, _probe in identities},
        )
        for key, _number, _title, probe in identities:
            rows = [
                row
                for row in confirmations
                if row["canonical_key"] == key
            ]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["raw_query_id"], probe)
            self.assertTrue(
                row["selected_parent_category_membership_present"]
            )
            self.assertTrue(
                row["title_probe_parent_category_confirmation"]
            )
            self.assertTrue(
                row[
                    "title_probe_parent_category_confirmation_"
                    "visibility_preserved"
                ]
            )
            self.assertFalse(
                row["recall_only_miss_visibility_preserved"]
            )
            self.assertTrue(
                row[
                    "probe_membership_is_non_dispositive_"
                    "confirmation_only"
                ]
            )
            self.assertEqual(row["disposition"], "unresolved")
            self.assertEqual(row["scoring_weight"], 0)
            self.assertFalse(row["ratification_authority"])
        self.assertFalse(
            any(
                row["review_bucket"]
                == "recall_only_possible_category_miss"
                and row["canonical_key"]
                in {key for key, _number, _title, _probe in identities}
                for row in proposals
            )
        )

    def test_named_outline_alone_preserves_recall_visibility(self) -> None:
        registry = {
            "categories": [
                {
                    "id": "supervision",
                    "label": "Supervision",
                    "scoring_category": True,
                    "identifiers": [{"text": "examination credit union"}],
                    "title_aliases": ["TAILOR Act"],
                    "known_bills": [
                        {"congress": 119, "compact_cite": "hr7056"}
                    ],
                }
            ],
            "coverage_probes": [
                {
                    "id": "probe_tailor",
                    "label": "TAILOR Act probe",
                    "scoring_category": False,
                    "record_kind": "coverage_probe",
                    "parent_category_id": "supervision",
                    "title_alias": "TAILOR Act",
                    "ambiguous_short_act_alias": True,
                    "membership_non_dispositive": True,
                }
            ],
        }
        row = prefill.build_proposals(
            [
                {
                    "canonical_key": "119:hr:7056",
                    "display_number": "H.R.7056",
                    "title": "Generic Threshold Bill",
                    "canonical_url": (
                        "https://www.congress.gov/bill/119/7056"
                    ),
                }
            ],
            [
                {
                    "canonical_key": "119:hr:7056",
                    "query_id": "probe_tailor",
                    "parent_category_id": "supervision",
                    "record_kind": "coverage_probe",
                    "scoring_category": False,
                    "membership_non_dispositive": True,
                    "ambiguous_short_act_alias": True,
                    "title_alias": "TAILOR Act",
                }
            ],
            registry,
            {
                "119:hr:7056": {
                    "official_title": "To index statutory thresholds.",
                    "summary": None,
                }
            },
            {},
        )[0]
        self.assertEqual(
            row["review_bucket"], "recall_only_possible_category_miss"
        )
        self.assertEqual(
            row["recall_trigger_signals"],
            ["explicit_named_outline_bill"],
        )
        self.assertEqual(row["disposition"], "unresolved")
        self.assertFalse(row["ratification_authority"])

    def test_short_alias_text_collision_preserves_official_nexus(self) -> None:
        registry = {
            "categories": [
                {
                    "id": "technology",
                    "label": "Technology",
                    "scoring_category": True,
                    "identifiers": [{"text": "stablecoin"}],
                    "title_aliases": ["GENIUS Act"],
                    "known_bills": [],
                }
            ],
            "coverage_probes": [],
        }
        row = prefill.build_proposals(
            [
                {
                    "canonical_key": "119:s:394",
                    "display_number": "S.394",
                    "title": "GENIUS Act",
                    "canonical_url": (
                        "https://www.congress.gov/bill/119/394"
                    ),
                }
            ],
            [
                {
                    "canonical_key": "119:s:394",
                    "query_id": "technology",
                    "record_kind": "category",
                    "scoring_category": True,
                    "membership_non_dispositive": False,
                }
            ],
            registry,
            {
                "119:s:394": {
                    "official_title": (
                        "A bill to regulate payment stablecoins."
                    ),
                    "summary": None,
                }
            },
            {},
        )[0]
        self.assertFalse(row["ambiguous_short_act_alias_probe"])
        self.assertEqual(
            row["matched_short_act_title_aliases"], ["GENIUS Act"]
        )
        self.assertEqual(
            row["review_bucket"],
            "ambiguous_short_act_collision_with_independent_official_nexus",
        )

    def test_existing_proposal_authority_tamper_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry, sidecar = fixture(root)
            out, _, _ = prefill.build_prefill(
                manifest_path=manifest,
                registry_path=registry,
                sidecar_manifest_path=sidecar,
                out_root=root / "out",
            )
            proposal_path = out / "membership_review_proposals.ndjson"
            rows = list(prefill.iter_ndjson(proposal_path))
            rows[0]["scoring_authority"] = True
            write_ndjson(proposal_path, rows)
            prefill_manifest_path = out / "manifest.json"
            prefill_manifest = prefill.read_object(prefill_manifest_path)
            prefill_manifest["outputs"][
                "membership_review_proposals"
            ] = prefill.file_item(
                proposal_path, record_count=len(rows)
            )
            write_json(prefill_manifest_path, prefill_manifest)
            with self.assertRaisesRegex(
                prefill.ContractError,
                "Non-authoritative proposal contract drift",
            ):
                prefill.build_prefill(
                    manifest_path=manifest,
                    registry_path=registry,
                    sidecar_manifest_path=sidecar,
                    out_root=root / "out",
                )


if __name__ == "__main__":
    unittest.main()

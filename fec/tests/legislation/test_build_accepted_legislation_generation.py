from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

import legislation.build_accepted_legislation_generation as accepted


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def fixtures(tmp_path: Path) -> dict[str, Path]:
    run = tmp_path / "run" / "manifest.json"
    raw_memberships = tmp_path / "run" / "memberships.ndjson"
    raw_membership_rows = [
        {
            "canonical_key": "118:hr:10",
            "query_id": "capital",
            "record_kind": "category",
            "scoring_category": True,
        },
        {
            "canonical_key": "118:hr:10",
            "query_id": "tax",
            "record_kind": "category",
            "scoring_category": True,
        },
        {
            "canonical_key": "118:hr:99",
            "query_id": "recall",
            "record_kind": "coverage_probe",
            "scoring_category": False,
        },
    ]
    write_ndjson(raw_memberships, raw_membership_rows)
    run_value = {
        "status": "complete",
        "primary_instrument": "Congress.gov Advanced Search - Legislation",
        "quorum_used": False,
        "registry_sha256": "a" * 64,
        "selected_query_ids": ["capital", "tax", "recall"],
        "queries": [
            {
                "query_id": "capital",
                "query_file": "queries/capital.json",
                "result_total": 1,
                "unique_record_count": 1,
                "records_sha256": "1" * 64,
            },
            {
                "query_id": "tax",
                "query_file": "queries/tax.json",
                "result_total": 1,
                "unique_record_count": 1,
                "records_sha256": "2" * 64,
            },
            {
                "query_id": "recall",
                "query_file": "queries/recall.json",
                "result_total": 1,
                "unique_record_count": 1,
                "records_sha256": "3" * 64,
            },
        ],
        "union": {
            "records_sha256": "b" * 64,
            "memberships_file": raw_memberships.name,
            "membership_count": len(raw_membership_rows),
            "memberships_sha256": (
                accepted.sha256_rows_like_javascript(raw_membership_rows)
            ),
        },
    }
    write_json(run, run_value)
    review_ledger = tmp_path / "review" / "ledger.ndjson"
    review_rows = [
        {
            "canonical_key": "118:hr:10",
            "display_number": "H.R.10",
            "title": "Specific Credit Union Act",
            "query_id": "capital",
            "category_label": "Capital",
            "scoring_category": True,
            "official_title": "Specific Credit Union Act",
            "official_summary": "Adjusts capital requirements.",
            "policy_area": "Finance and Financial Sector",
            "measure_initial_action": "Introduced",
            "measure_initial_action_date": "01/10/2023",
            "measure_initial_action_date_iso": "2023-01-10",
            "measure_latest_action": "Latest Action: Passed House.",
            "measure_latest_action_date": "05/01/2023",
            "measure_tracker_status": "Passed House",
            "measure_cosponsor_count": 2,
            "measure_committees": "House Financial Services",
            "measure_amends": None,
            "congress_gov_url": (
                "https://www.congress.gov/bill/118th-congress/"
                "house-bill/10"
            ),
            "disposition": "include",
            "decision_source": "human_review",
        },
        {
            "canonical_key": "118:hr:10",
            "display_number": "H.R.10",
            "title": "Specific Credit Union Act",
            "query_id": "tax",
            "category_label": "Tax",
            "scoring_category": True,
            "disposition": "exclude_false_positive",
            "decision_source": "human_review",
        },
    ]
    write_ndjson(review_ledger, review_rows)
    review_manifest = tmp_path / "review" / "manifest.json"
    write_json(
        review_manifest,
        {
            "schema": accepted.REVIEW_MANIFEST_SCHEMA,
            "discovery_records_are_never_deleted": True,
            "automated_semantic_exclusion": False,
            "official_text_signals_require_review_before_inclusion": True,
            "resolved_decisions_require_reproducible_official_evidence": True,
            "promotion_gate_passed": True,
            "unresolved_count": 0,
            "membership_count": 2,
            "registry_sha256": "a" * 64,
            "discovery_manifest_sha256": accepted.sha256_file(run),
            "ledger_file": "ledger.ndjson",
        },
    )
    historical_acceptance_ledger = (
        tmp_path / "historical_acceptance" / "ledger.ndjson"
    )
    write_ndjson(historical_acceptance_ledger, [])
    historical_acceptance_manifest = (
        tmp_path / "historical_acceptance" / "manifest.json"
    )
    acceptance_receipt = {
        "path": str(historical_acceptance_ledger.resolve()),
        "bytes": historical_acceptance_ledger.stat().st_size,
        "sha256": accepted.sha256_file(historical_acceptance_ledger),
        "record_count": 0,
    }
    write_json(
        historical_acceptance_manifest,
        {
            "schema": accepted.HISTORICAL_ACCEPTANCE_AUDIT_SCHEMA,
            "partition": {
                "historical_aggregate_proven_and_retained": 0
            },
            "historical_only_evidence_summary": {
                "memberships_by_query": {},
                "review_dispositions": {},
            },
            "historical_membership_acceptance_gate": {
                "schema": accepted.HISTORICAL_ACCEPTANCE_SCHEMA,
                "passed": True,
                "lossless_partition_closed": True,
                "candidate_count": 0,
                "membership_count": 0,
                "unresolved_non_scoring_count": 0,
                "official_exclusion_non_scoring_count": 0,
                "officially_ratified_scoring_count": 0,
                "unratified_include_non_scoring_count": 0,
                "scoring_requires_official_relevance_ratification": True,
                "historical_membership_can_score_without_ratification": False,
                "historical_manifest_sha256": "c" * 64,
                "current_manifest_sha256": accepted.sha256_file(run),
                "historical_registry_sha256": "d" * 64,
                "current_registry_sha256": "a" * 64,
                "review_ledger_sha256": accepted.sha256_file(review_ledger),
            },
            "outputs": {
                "historical_membership_acceptance": acceptance_receipt
            },
        },
    )
    edges = [
        {
            "canonical_key": "118:hr:10",
            "congress": 118,
            "type": "hr",
            "number": 10,
            "display_number": "H.R.10",
            "title": "Specific Credit Union Act",
            "role": "Sponsor",
            "member": "Sponsor, Sam",
            "bioguide_id": "S000001",
            "original_cosponsor": None,
            "relationship_status": "current",
            "source_url": "https://www.congress.gov/bill/118th-congress/house-bill/10",
        },
        {
            "canonical_key": "118:hr:10",
            "congress": 118,
            "type": "hr",
            "number": 10,
            "display_number": "H.R.10",
            "role": "Cosponsor",
            "member": "Member, One",
            "bioguide_id": "M000001",
            "original_cosponsor": False,
            "relationship_status": "current",
            "source_url": "https://www.congress.gov/bill/118th-congress/house-bill/10/cosponsors",
        },
        {
            "canonical_key": "118:hr:10",
            "congress": 118,
            "type": "hr",
            "number": 10,
            "display_number": "H.R.10",
            "role": "Cosponsor",
            "member": "Member, One",
            "bioguide_id": "M000001",
            "original_cosponsor": True,
            "relationship_status": "current",
            "source_url": "https://www.congress.gov/bill/118th-congress/house-bill/10/cosponsors",
        },
        {
            "canonical_key": "118:hr:10",
            "congress": 118,
            "type": "hr",
            "number": 10,
            "display_number": "H.R.10",
            "role": "Cosponsor",
            "member": "Former, Fran",
            "bioguide_id": "F000001",
            "original_cosponsor": False,
            "relationship_status": "withdrawn",
            "withdrawn_date": "02/01/2023",
            "source_url": "https://www.congress.gov/bill/118th-congress/house-bill/10/cosponsors",
        },
    ]
    edge_ledger = tmp_path / "relationships" / "edges.ndjson"
    write_ndjson(edge_ledger, edges)
    relationships_manifest = tmp_path / "relationships" / "manifest.json"
    write_json(
        relationships_manifest,
        {
            "status": "complete",
            "unresolved_measure_count": 0,
            "discovery_records_sha256": "b" * 64,
            "edge_count": len(edges),
            "edges_sha256": accepted.sha256_rows_like_javascript(edges),
            "edges_file": "edges.ndjson",
        },
    )
    acceptance_values = {
        key: True for key in accepted.REQUIRED_ACCEPTANCE_TRUE
    }
    acceptance_values.update(
        {
            "passed": True,
            "membership_review_unresolved_count": 0,
            "relationship_unresolved_measures": 0,
            "missing_sponsor_records": [],
            "historical_archive_missing": [],
            "missing_required_registry_query_ids": [],
            "manifest_registry_sha256": "a" * 64,
            "current_registry_sha256": "a" * 64,
        }
    )
    reconciliation = tmp_path / "reconciliation.json"
    write_json(reconciliation, {"acceptance": acceptance_values})
    return {
        "run_manifest_path": run,
        "reconciliation_report_path": reconciliation,
        "review_manifest_path": review_manifest,
        "historical_membership_acceptance_manifest_path": (
            historical_acceptance_manifest
        ),
        "relationships_manifest_path": relationships_manifest,
        "out_dir": tmp_path / "staging" / "generation",
    }


def install_historical_acceptance(
    inputs: dict[str, Path],
    row: dict,
) -> None:
    manifest_path = inputs[
        "historical_membership_acceptance_manifest_path"
    ]
    manifest = accepted.read_json(manifest_path)
    ledger_path = Path(
        manifest["outputs"]["historical_membership_acceptance"]["path"]
    )
    write_ndjson(ledger_path, [row])
    manifest["outputs"]["historical_membership_acceptance"] = {
        "path": str(ledger_path.resolve()),
        "bytes": ledger_path.stat().st_size,
        "sha256": accepted.sha256_file(ledger_path),
        "record_count": 1,
    }
    gate = manifest["historical_membership_acceptance_gate"]
    gate.update(
        {
            "candidate_count": 1,
            "membership_count": 1,
            "unresolved_non_scoring_count": int(
                row["review_disposition"] == "unresolved"
            ),
            "official_exclusion_non_scoring_count": int(
                row["review_disposition"] == "exclude_false_positive"
            ),
            "officially_ratified_scoring_count": int(
                row["current_scoring_evidence_eligible"] is True
            ),
            "unratified_include_non_scoring_count": int(
                row["acceptance_state"]
                == "historical_visible_include_unratified_non_scoring"
            ),
        }
    )
    manifest["partition"][
        "historical_aggregate_proven_and_retained"
    ] = 1
    manifest["historical_only_evidence_summary"][
        "memberships_by_query"
    ] = {row["query_id"]: 1}
    manifest["historical_only_evidence_summary"][
        "review_dispositions"
    ] = {row["review_disposition"]: 1}
    write_json(manifest_path, manifest)


def historical_capital_row(inputs: dict[str, Path]) -> dict:
    run_path = inputs["run_manifest_path"]
    run = accepted.read_json(run_path)
    current = accepted.completed_query_versions(run_path, run)["capital"]
    manifest = accepted.read_json(
        inputs["historical_membership_acceptance_manifest_path"]
    )
    gate = manifest["historical_membership_acceptance_gate"]
    return {
        "schema": accepted.HISTORICAL_ACCEPTANCE_SCHEMA,
        "canonical_key": "118:hr:10",
        "query_id": "capital",
        "historical_membership_only": True,
        "candidate_retained": True,
        "review_disposition": "include",
        "acceptance_state": (
            "historical_visible_include_unratified_non_scoring"
        ),
        "official_relevance_ratified": False,
        "official_relevance_ratification_required_for_scoring": True,
        "current_scoring_evidence_eligible": False,
        "membership_query_provenance": {
            "query_id": "capital",
            "historical_membership_only": True,
            "historical_query_version": {
                "scope": "historical_completed_manifest",
                "manifest_sha256": gate["historical_manifest_sha256"],
                "registry_sha256": gate[
                    "historical_registry_sha256"
                ],
                "query_id": "capital",
                "query_records_sha256": "e" * 64,
                "query_result_total": 1,
                "query_unique_record_count": 1,
                "query_completed": True,
            },
            "current_query_version": current,
            "current_membership_present": False,
            "query_version_changed": True,
        },
        "historical_membership_receipt": {
            "line": 1,
            "line_sha256": "f" * 64,
        },
        "review_receipt": {
            "disposition": "include",
            "decision_source": "human_review",
        },
        "exclusion_receipt": None,
    }


class AcceptedLegislationGenerationTests(unittest.TestCase):
    def test_make_row_keeps_congress_decay_separate_from_amendment_weight(
        self,
    ) -> None:
        membership = {
            "canonical_key": "119:hamdt:10",
            "query_id": "payments",
            "category_label": "Payments",
            "scoring_category": True,
            "disposition": "include",
        }
        edge = {
            "canonical_key": "119:hamdt:10",
            "congress": 119,
            "type": "hamdt",
            "number": 10,
            "role": "Sponsor",
            "member": "Member, One",
            "bioguide_id": "M000001",
            "original_cosponsor": None,
            "relationship_status": "current",
        }
        row = accepted.make_row(
            membership,
            edge,
            "sponsor",
            input_hashes={},
            membership_query_provenance={},
            historical_acceptance=None,
        )
        self.assertEqual(row["decay_weight"], 1.0)
        self.assertEqual(row["measure_weight"], 0.5)
        self.assertEqual(row["effective_weight"], 0.5)
        self.assertEqual(
            row["decayed_role_weight_scenarios"][
                "archive_legacy_3_2_1"
            ],
            1.5,
        )

    def test_builds_deterministic_role_correct_staged_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            first = accepted.build_generation(**inputs)
            ledger = (
                inputs["out_dir"]
                / "accepted_legislation_relationships.ndjson"
            )
            first_bytes = ledger.read_bytes()
            rows = accepted.read_ndjson(ledger)

            self.assertTrue(first["generation_complete"])
            self.assertFalse(first["promotion_ready"])
            self.assertIsNone(first["selected_role_weight_policy"])
            self.assertEqual(
                first["membership_partition"],
                {
                    "raw_membership_count": 3,
                    "reviewed_membership_count": 2,
                    "non_dispositive_coverage_probe_count": 1,
                    "raw_memberships_missing_partition_count": 0,
                    "review_memberships_absent_from_raw_count": 0,
                    "lossless_partition_closed": True,
                },
            )
            self.assertEqual(
                first["canonical_ledger_sha256"],
                hashlib.sha256(first_bytes).hexdigest(),
            )
            # Three canonical relationships x two categories.
            self.assertEqual(len(rows), 6)
            included = [
                row for row in rows if row["membership_status"] == "included"
            ]
            self.assertEqual(
                {row["role"] for row in included},
                {"sponsor", "original_cosponsor", "cosponsor"},
            )
            original = next(
                row
                for row in included
                if row["member_bioguide_id"] == "M000001"
            )
            self.assertEqual(original["role"], "original_cosponsor")
            self.assertEqual(original["decay_weight"], 0.66)
            self.assertEqual(original["tracker_status"], "Passed House")
            self.assertEqual(
                original["initial_action_date_iso"], "2023-01-10"
            )
            self.assertEqual(original["reported_cosponsor_count"], 2)
            self.assertEqual(
                original["policy_area"], "Finance and Financial Sector"
            )
            self.assertEqual(
                original["provenance"]["membership"][
                    "congress_gov_url"
                ],
                (
                    "https://www.congress.gov/bill/118th-congress/"
                    "house-bill/10"
                ),
            )
            self.assertFalse(original["historical_membership_only"])
            self.assertFalse(
                original["provenance"]["membership"][
                    "membership_query_provenance"
                ]["historical_membership_only"]
            )
            withdrawn = next(
                row
                for row in included
                if row["relationship_status"] == "withdrawn"
            )
            self.assertFalse(withdrawn["scoring_eligible"])
            self.assertEqual(
                withdrawn["zero_weight_reason"], "relationship_withdrawn"
            )
            self.assertEqual(
                set(withdrawn["decayed_role_weight_scenarios"].values()),
                {0.0},
            )
            excluded = [
                row for row in rows if row["membership_status"] == "excluded"
            ]
            self.assertTrue(excluded)
            self.assertTrue(
                all(not row["scoring_eligible"] for row in excluded)
            )

            second = accepted.build_generation(**inputs)
            self.assertEqual(second, first)
            self.assertEqual(ledger.read_bytes(), first_bytes)

    def test_fails_closed_on_unresolved_upstream_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            report = accepted.read_json(
                inputs["reconciliation_report_path"]
            )
            report["acceptance"]["membership_review_complete"] = False
            write_json(inputs["reconciliation_report_path"], report)

            with self.assertRaises(accepted.GenerationGateError):
                accepted.build_generation(**inputs)
            self.assertFalse(inputs["out_dir"].exists())

    def test_rejects_proposal_only_row_even_if_disposition_is_resolved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            manifest_path = inputs["review_manifest_path"]
            manifest = accepted.read_json(manifest_path)
            ledger_path = manifest_path.parent / manifest["ledger_file"]
            rows = accepted.read_ndjson(ledger_path)
            rows[0].update(
                {
                    "proposal_only": True,
                    "ratification_state": "NOT_RATIFIED",
                    "scoring_input_allowed": False,
                    "scoring_authority": False,
                }
            )
            write_ndjson(ledger_path, rows)

            with self.assertRaisesRegex(
                accepted.GenerationGateError,
                "proposal-only or non-authoritative row",
            ):
                accepted.build_generation(**inputs)
            self.assertFalse(inputs["out_dir"].exists())

    def test_rejects_review_ledger_from_prefill_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            inputs = fixtures(temp)
            original_manifest_path = inputs["review_manifest_path"]
            original_manifest = accepted.read_json(original_manifest_path)
            original_ledger = (
                original_manifest_path.parent
                / original_manifest["ledger_file"]
            )
            sidecar_dir = temp / "partial_review_prefill" / "snapshot"
            sidecar_manifest = sidecar_dir / "manifest.json"
            sidecar_ledger = sidecar_dir / "ledger.ndjson"
            write_ndjson(
                sidecar_ledger, accepted.read_ndjson(original_ledger)
            )
            original_manifest["ledger_file"] = sidecar_ledger.name
            write_json(sidecar_manifest, original_manifest)
            inputs["review_manifest_path"] = sidecar_manifest

            with self.assertRaisesRegex(
                accepted.GenerationGateError,
                "sidecars cannot be accepted generation inputs",
            ):
                accepted.build_generation(**inputs)
            self.assertFalse(inputs["out_dir"].exists())

    def test_refuses_repository_root_as_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            inputs["out_dir"] = accepted.ROOT
            with self.assertRaisesRegex(
                accepted.GenerationGateError, "repository root"
            ):
                accepted.build_generation(**inputs)

    def test_refuses_non_audit_workspace_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            inputs["out_dir"] = accepted.ROOT / "unsafe-accepted-generation-output"
            with self.assertRaisesRegex(
                accepted.GenerationGateError, "audits/legfix5"
            ):
                accepted.build_generation(**inputs)

    def test_fails_closed_without_valid_bioguide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            relationships_manifest = accepted.read_json(
                inputs["relationships_manifest_path"]
            )
            edge_ledger = (
                inputs["relationships_manifest_path"].parent
                / relationships_manifest["edges_file"]
            )
            edges = accepted.read_ndjson(edge_ledger)
            edges[0]["bioguide_id"] = ""
            write_ndjson(edge_ledger, edges)
            relationships_manifest["edges_sha256"] = (
                accepted.sha256_rows_like_javascript(edges)
            )
            write_json(
                inputs["relationships_manifest_path"],
                relationships_manifest,
            )
            with self.assertRaisesRegex(
                accepted.GenerationGateError, "valid Bioguide"
            ):
                accepted.build_generation(**inputs)

    def test_fails_closed_when_cosponsor_original_status_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            relationships_manifest = accepted.read_json(
                inputs["relationships_manifest_path"]
            )
            edge_ledger = (
                inputs["relationships_manifest_path"].parent
                / relationships_manifest["edges_file"]
            )
            edges = accepted.read_ndjson(edge_ledger)
            cosponsor = next(
                row for row in edges if row["role"] == "Cosponsor"
            )
            cosponsor["original_cosponsor"] = None
            write_ndjson(edge_ledger, edges)
            relationships_manifest["edges_sha256"] = (
                accepted.sha256_rows_like_javascript(edges)
            )
            write_json(
                inputs["relationships_manifest_path"],
                relationships_manifest,
            )
            with self.assertRaisesRegex(
                accepted.GenerationGateError, "explicit boolean"
            ):
                accepted.build_generation(**inputs)

    def test_fails_closed_without_exactly_one_current_sponsor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            relationships_manifest = accepted.read_json(
                inputs["relationships_manifest_path"]
            )
            edge_ledger = (
                inputs["relationships_manifest_path"].parent
                / relationships_manifest["edges_file"]
            )
            edges = [
                row
                for row in accepted.read_ndjson(edge_ledger)
                if row["role"] != "Sponsor"
            ]
            write_ndjson(edge_ledger, edges)
            relationships_manifest["edge_count"] = len(edges)
            relationships_manifest["edges_sha256"] = (
                accepted.sha256_rows_like_javascript(edges)
            )
            write_json(
                inputs["relationships_manifest_path"],
                relationships_manifest,
            )
            with self.assertRaisesRegex(
                accepted.GenerationGateError, "exactly one current Sponsor"
            ):
                accepted.build_generation(**inputs)

    def test_historical_include_is_visible_but_zero_weight_without_ratification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            install_historical_acceptance(
                inputs, historical_capital_row(inputs)
            )
            manifest = accepted.build_generation(**inputs)
            rows = accepted.read_ndjson(
                inputs["out_dir"]
                / "accepted_legislation_relationships.ndjson"
            )
            capital = [row for row in rows if row["category"] == "capital"]
            self.assertTrue(capital)
            self.assertTrue(
                all(row["historical_membership_only"] for row in capital)
            )
            self.assertTrue(
                all(not row["scoring_eligible"] for row in capital)
            )
            self.assertEqual(
                {
                    row["zero_weight_reason"]
                    for row in capital
                    if row["relationship_status"] == "current"
                },
                {"historical_membership_not_officially_ratified"},
            )
            self.assertEqual(
                manifest["historical_membership_acceptance"],
                {
                    "membership_count": 1,
                    "officially_ratified_scoring_count": 0,
                    "non_scoring_count": 1,
                    "lossless_acceptance_gate_passed": True,
                },
            )

    def test_rejects_forged_historical_scoring_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            row = historical_capital_row(inputs)
            row["current_scoring_evidence_eligible"] = True
            row["acceptance_state"] = (
                "historical_visible_officially_ratified_scoring"
            )
            install_historical_acceptance(inputs, row)
            with self.assertRaisesRegex(
                accepted.GenerationGateError,
                "scoring authority is inconsistent",
            ):
                accepted.build_generation(**inputs)

    def test_rejects_lossy_historical_acceptance_partition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            manifest_path = inputs[
                "historical_membership_acceptance_manifest_path"
            ]
            manifest = accepted.read_json(manifest_path)
            manifest["partition"][
                "historical_aggregate_proven_and_retained"
            ] = 1
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(
                accepted.GenerationGateError,
                "source divergence partition",
            ):
                accepted.build_generation(**inputs)

    def test_rejects_scoring_raw_membership_that_escaped_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            run_path = inputs["run_manifest_path"]
            run = accepted.read_json(run_path)
            raw_path = run_path.parent / run["union"]["memberships_file"]
            raw = accepted.read_ndjson(raw_path)
            raw[-1]["record_kind"] = "category"
            raw[-1]["scoring_category"] = True
            write_ndjson(raw_path, raw)
            run["union"]["memberships_sha256"] = (
                accepted.sha256_rows_like_javascript(raw)
            )
            write_json(run_path, run)
            review_manifest_path = inputs["review_manifest_path"]
            review_manifest = accepted.read_json(review_manifest_path)
            review_manifest["discovery_manifest_sha256"] = (
                accepted.sha256_file(run_path)
            )
            write_json(review_manifest_path, review_manifest)
            acceptance_manifest_path = inputs[
                "historical_membership_acceptance_manifest_path"
            ]
            acceptance_manifest = accepted.read_json(
                acceptance_manifest_path
            )
            acceptance_manifest[
                "historical_membership_acceptance_gate"
            ]["current_manifest_sha256"] = accepted.sha256_file(run_path)
            write_json(acceptance_manifest_path, acceptance_manifest)
            with self.assertRaisesRegex(
                accepted.GenerationGateError,
                "escaped review",
            ):
                accepted.build_generation(**inputs)


if __name__ == "__main__":
    unittest.main()

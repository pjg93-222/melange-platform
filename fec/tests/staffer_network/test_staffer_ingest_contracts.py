import hashlib
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from lobbying import collect_lda_filings as CLF
from staffer_network.historic_members import HistoricMemberError, historic_member_receipt, prepare_voteview_spine
from staffer_network.lda_contract import (
    LdaReconciliationError,
    audit_sweep_year,
    audit_sweeps,
    lda_reconciliation_receipt,
    sweep_tree_inventory,
)
from staffer_network import lda_composite_repair as LCR
from staffer_network import lda_source_native_corpus as LSC
from staffer_network import lda_identity_candidates as LIC
from staffer_network.legacy_seed import (
    LegacySeedError,
    build_stripped_seed,
    build_stripped_seed_generation,
    strip_document,
)
from staffer_network.politico_contract import politico_directory_receipt
from staffer_network.publication import (
    GenerationError, _validate_record_semantics, schema_path,
)
from staffer_network.schema_validation import validate_instance
from staffer_network.source_manifest import seal_source_manifest, sha256_file


def authority(status="AUTHORIZED", enabled=True, redistribution="restricted-local-only"):
    return seal_source_manifest({
        "schema_id": "staffer-network-source-manifest-v1", "source_system_id": "quorum-legacy-derived",
        "enabled": enabled, "authority_scope": "synthetic fixture", "terms_uri": "https://example.test/terms",
        "license": "CC0",
        "terms_checked_at": "2026-08-29T20:00:00Z", "authorization_status": status,
        "approved_access_mode": "local file", "redistribution_class": redistribution,
        "query_or_inventory": "one synthetic record", "crawl_watermark": "fixture-watermark",
        "retention_policy": "temporary fixture", "source_schema_version": "fixture-v1",
        "freshness_rule": "immutable fixture",
        "source_as_of": "2026-08-29T00:00:00Z", "collected_at": "2026-08-29T20:00:00Z",
        "collector_version": "fixture-v1", "parser_version": "fixture-v1", "expected_count": 1,
        "observed_count": 1, "accepted_count": 1 if enabled else 0, "rejected_count": 0 if enabled else 1,
        "duplicate_count": 0, "amendment_policy": "replace by version",
        "rejection_reasons": {} if enabled else {"QUARANTINED": 1},
        "acceptance_bar": {"metric": "accepted / expected", "threshold": 1.0 if enabled else None, "comparison": ">=" if enabled else None, "observed": 1.0 if enabled else None, "pass": enabled},
        "files": [{"path": "staff.json", "sha256": "a" * 64, "bytes": 1}],
    })


class StafferIngestContractTests(unittest.TestCase):
    def test_lda_retry_envelope_survives_five_503s_and_caps_backoff(self):
        url = CLF.sweep_page_url(2022, 10)

        class Pacer:
            def __init__(self):
                self.calls = 0

            def wait(self):
                self.calls += 1

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return url

            def read(self):
                return b"recovered"

        outcomes = [
            urllib.error.HTTPError(url, 503, "busy", {}, None) for _ in range(5)
        ] + [Response()]

        def opener(*_args, **_kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        sleeps = []
        pacer = Pacer()
        body = CLF.fetch_sweep_page(
            2022, 10, {}, pacer, opener=opener, sleep_fn=sleeps.append,
            max_tries=6, max_backoff_seconds=8,
        )
        self.assertEqual(body, b"recovered")
        self.assertEqual(sleeps, [1, 2, 4, 8, 8])
        self.assertEqual(pacer.calls, 6)

    def test_enabled_manifest_flag_is_contained_to_staffer_ingest(self):
        """The source-native authority flag must not arm existing analysis code."""

        forbidden = (
            'manifest["enabled"]',
            "manifest['enabled']",
            'manifest.get("enabled")',
            "manifest.get('enabled')",
        )
        hits = []
        for path in FEC.rglob("*.py"):
            if FEC / "staffer_network" in path.parents or FEC / "tests" in path.parents:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(token in text for token in forbidden):
                hits.append(path.relative_to(FEC).as_posix())
        self.assertEqual(hits, [])

    def test_legacy_strip_removes_contact_fields_and_conserves(self):
        document = {"M001": [{"id": "q1", "n": "Alex Example", "t": "Chief", "e": "x@example.test", "p": "555", "star": True}]}
        rows, receipt = strip_document(document, expected_rows=1, expected_offices=1)
        self.assertEqual(receipt["accepted_rows"], 1)
        self.assertNotIn("e", rows[0])
        self.assertNotIn("p", rows[0])

    def test_legacy_file_is_not_opened_before_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.json"
            with self.assertRaisesRegex(LegacySeedError, "authority-gated"):
                build_stripped_seed(
                    missing,
                    authority(status="QUARANTINED", enabled=False),
                    expected_source_sha256="a" * 64,
                    expected_rows=1,
                    expected_offices=1,
                )

    def test_legacy_seed_requires_restricted_scope_and_exact_input_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "staff.json"
            document = {"M001": [{
                "id": "q1", "n": "Alex Example", "t": "Chief",
                "e": "x@example.test", "p": "555",
            }]}
            path.write_text(json.dumps(document), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(LegacySeedError, "restricted-local-only"):
                build_stripped_seed(
                    path,
                    authority(redistribution="public-safe-derived-only"),
                    expected_source_sha256=digest,
                    expected_rows=1,
                    expected_offices=1,
                )
            with self.assertRaisesRegex(LegacySeedError, "input hash mismatch"):
                build_stripped_seed(
                    path,
                    authority(),
                    expected_source_sha256="0" * 64,
                    expected_rows=1,
                    expected_offices=1,
                )
            rows, receipt = build_stripped_seed(
                path,
                authority(),
                expected_source_sha256=digest,
                expected_rows=1,
                expected_offices=1,
            )
            self.assertEqual(receipt["accepted_rows"], 1)
            self.assertNotIn("e", rows[0])
            self.assertNotIn("p", rows[0])

    def test_committed_legacy_seed_receipt_has_a_canonical_producer(self):
        receipt_path = (
            FEC.parent / "data" / "staffer_network" / "receipts" /
            "legacy_seed_20260830.json"
        )
        expected = json.loads(receipt_path.read_text(encoding="utf-8"))
        manifest = json.loads((
            FEC.parent / "data" / "staffer_network" / "policy" /
            "legacy_seed_authority_20260830.json"
        ).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            actual = build_stripped_seed_generation(
                FEC.parent / "data" / "members" / "staff_directory.json",
                manifest,
                Path(temp) / "seed",
                expected_source_sha256=expected["source_sha256"],
                generated_at=expected["generated_at"],
                source_path_label=expected["source_path"],
                synced_roots=[FEC.parent],
            )
        self.assertEqual(actual, expected)
        schema = json.loads((
            FEC / "staffer_network" / "schemas" / "legacy_seed_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(expected, schema)
        _validate_record_semantics(expected, expected["schema_id"])

    def test_voteview_preserves_blank_bioguide_disposition(self):
        content = "congress,chamber,icpsr,state_abbrev,bioname,bioguide_id\n119,House,1,MN,EXAMPLE Alex,E000001\n1,President,2,USA,WASHINGTON George,\n"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "members.csv"
            path.write_text(content, encoding="utf-8")
            rows, receipt = prepare_voteview_spine(path, expected_blank_bioguides=1)
            self.assertEqual(receipt["observed_rows"], 2)
            self.assertEqual(rows[1]["bioguide_disposition"], "SOURCE_BLANK")
            with self.assertRaises(HistoricMemberError):
                prepare_voteview_spine(path, expected_blank_bioguides=0)

    def test_committed_voteview_receipt_has_a_canonical_producer(self):
        expected = json.loads((FEC.parent / "data" / "staffer_network" / "receipts" / "historic_member_spine_20260829.json").read_text(encoding="utf-8"))
        actual = historic_member_receipt(
            FEC.parent / "votes" / "HSall_members.csv",
            generated_at=expected["generated_at"],
            source_path_label=expected["source_path"],
            expected_blank_bioguides=68,
        )
        self.assertEqual(actual, expected)

    def test_committed_politico_receipt_is_schema_and_semantically_valid(self):
        receipt = json.loads((FEC.parent / "data" / "staffer_network" / "receipts" / "politico_directory_crawl_20260829.json").read_text(encoding="utf-8"))
        schema = json.loads((FEC / "staffer_network" / "schemas" / "politico_crawl_receipt.schema.json").read_text(encoding="utf-8"))
        validate_instance(receipt, schema)
        _validate_record_semantics(receipt, receipt["schema_id"])

    def test_politico_receipt_has_a_canonical_contact_free_producer(self):
        rows = [
            {"partition": "Democratic", "partition_ordinal": 1, "profile_uuid": "d0", "name": "D Zero", "roles": ["Chief of Staff"], "issue_areas": ["Budget"]},
            {"partition": "Democratic", "partition_ordinal": 2, "profile_uuid": "d1", "name": "D One", "roles": [], "issue_areas": []},
            {"partition": "Republican", "partition_ordinal": 1, "profile_uuid": "r0", "name": "R Zero", "roles": ["Counsel"], "issue_areas": []},
            {"partition": "Unknown", "partition_ordinal": 1, "profile_uuid": "u0", "name": "U Zero", "roles": [], "issue_areas": []},
            {"partition": "Independent", "partition_ordinal": 1, "profile_uuid": "i0", "name": "I Zero", "roles": [], "issue_areas": []},
        ]
        inventory = {
            "schema_id": "politico-directory-partition-inventory-v1",
            "measured_at": "2026-08-30T02:00:00Z",
            "partitions": [
                {"party": "Democratic", "total": 2},
                {"party": "Republican", "total": 1},
                {"party": "Unknown", "total": 1},
                {"party": "Independent", "total": 1},
            ],
            "total": 5,
            "task_pages": 4,
        }
        requests = [
            {"requested_at": f"2026-08-30T02:00:0{index}Z", "party": party, "offset": 0, "limit": 100, "count": count, "duration_ms": 5000, "status": "OK"}
            for index, (party, count) in enumerate((("Democratic", 2), ("Republican", 1), ("Unknown", 1), ("Independent", 1)))
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "directory_rows.ndjson").write_text(
                "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
                encoding="utf-8",
            )
            (root / "request_log.ndjson").write_text(
                "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in requests),
                encoding="utf-8",
            )
            (root / "partition_inventory.json").write_text(
                json.dumps(inventory, separators=(",", ":")) + "\n", encoding="utf-8"
            )
            files = [
                {"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(), "bytes": (root / name).stat().st_size}
                for name in ("directory_rows.ndjson", "request_log.ndjson", "partition_inventory.json")
            ]
            manifest = seal_source_manifest({
                "schema_id": "staffer-network-source-manifest-v1", "source_system_id": "politico-pro-staffer-directory",
                "enabled": False, "authority_scope": "synthetic Politico fixture", "terms_uri": "urn:test:politico-permission",
                "license": "synthetic fixture", "terms_checked_at": "2026-08-30T02:00:00Z", "authorization_status": "AUTHORIZED",
                "approved_access_mode": "fixture; four concurrent; 60/minute", "redistribution_class": "restricted-local-only",
                "query_or_inventory": "five synthetic rows", "crawl_watermark": "fixture", "retention_policy": "temporary fixture",
                "source_schema_version": "fixture-v1", "freshness_rule": "immutable fixture", "source_as_of": "2026-08-30T02:00:03Z",
                "collected_at": "2026-08-30T02:00:08Z", "collector_version": "fixture-v1", "parser_version": "fixture-v1",
                "expected_count": 5, "observed_count": 5, "accepted_count": 5, "rejected_count": 0, "duplicate_count": 0,
                "amendment_policy": "replace by version", "rejection_reasons": {},
                "acceptance_bar": {"metric": "accepted / expected", "threshold": 1.0, "comparison": ">=", "observed": 1.0, "pass": True},
                "files": files,
            })
            receipt = politico_directory_receipt(
                root, manifest, generated_at="2026-08-30T02:00:09Z"
            )
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["max_in_flight_requests"], 4)
            self.assertEqual(receipt["role_assertions"], 2)
            self.assertEqual(receipt["rows_with_issue_areas"], 1)
            self.assertEqual(receipt["prohibited_contact_keys"], [])

    def test_politico_receipt_semantics_have_planted_failures(self):
        expected = json.loads((FEC.parent / "data" / "staffer_network" / "receipts" / "politico_directory_crawl_20260829.json").read_text(encoding="utf-8"))
        mutations = {
            "partition mismatch": lambda value: value["partition_observed"].update({"Democratic": 0}),
            "rate excess": lambda value: value.update({"max_requests_in_any_rolling_minute": 61}),
            "concurrency excess": lambda value: value.update({"max_in_flight_requests": 5}),
            "unsafe field": lambda value: value["retained_fields"].append("email"),
            "duplicate UUID": lambda value: value.update({"unique_profile_uuids": 18617, "duplicate_uuid_occurrences": 1}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                receipt = json.loads(json.dumps(expected))
                mutate(receipt)
                with self.assertRaises(GenerationError):
                    _validate_record_semantics(receipt, receipt["schema_id"])

    def test_lda_manifest_reconciliation_has_negative_control(self):
        page = {"count": 1, "results": [{"filing_uuid": "u1", "filing_year": 2026}]}
        body = json.dumps(page).encode("utf-8")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "2026"
            directory.mkdir()
            (directory / "page_00001.json").write_bytes(body)
            manifest = {"complete": True, "filing_year": 2026, "page_count": 1, "md5": {"page_00001.json": hashlib.md5(body).hexdigest()}}
            (directory / "sweep_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(audit_sweep_year(directory)["pass"])
            (directory / "page_00001.json").write_bytes(body + b" ")
            receipt = audit_sweep_year(directory)
            self.assertFalse(receipt["pass"])
            self.assertEqual(receipt["hash_mismatch_pages"], ["page_00001.json"])

    def test_lda_duplicate_and_count_drift_fail_inventory(self):
        pages = {
            "page_00001.json": {"count": 2, "results": [{"filing_uuid": "u1", "filing_year": 2026}]},
            "page_00002.json": {"count": 3, "results": [{"filing_uuid": "u1", "filing_year": 2026}]},
        }
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "2026"
            directory.mkdir()
            digests = {}
            for name, page in pages.items():
                body = json.dumps(page).encode("utf-8")
                (directory / name).write_bytes(body)
                digests[name] = hashlib.md5(body).hexdigest()
            manifest = {"complete": True, "filing_year": 2026, "page_count": 2, "md5": digests}
            (directory / "sweep_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            receipt = audit_sweep_year(directory)
            self.assertFalse(receipt["pass"])
            self.assertEqual(receipt["duplicate_filing_uuid_occurrences"], 1)
            self.assertEqual(receipt["identical_duplicate_occurrences"], 1)
            self.assertTrue(receipt["source_count_drift"])
            self.assertEqual(receipt["unique_shortfall_vs_final_count"], 2)

    def test_lda_tree_inventory_changes_on_any_legacy_byte_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "2026"
            directory.mkdir()
            (directory / "sweep_manifest.json").write_text("{}", encoding="utf-8")
            page = directory / "page_00001.json"
            page.write_text("one", encoding="utf-8")
            before = sweep_tree_inventory(root, expected_years=[2026])
            page.write_text("two", encoding="utf-8")
            after = sweep_tree_inventory(root, expected_years=[2026])
            self.assertEqual(before["files"], 2)
            self.assertNotEqual(before["inventory_sha256"], after["inventory_sha256"])

    def test_lda_immutable_generation_refuses_reuse_and_rate_excess(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = CLF.default_paths(temp)
            generation = Path(temp) / "generation"
            directory, immutable = CLF._sweep_directory(
                paths, 2026, generation_root=generation
            )
            self.assertTrue(immutable)
            Path(directory).mkdir(parents=True)
            (Path(directory) / "sentinel").write_text("sealed", encoding="utf-8")
            with self.assertRaisesRegex(CLF.IngestError, "already exists and is nonempty"):
                CLF._sweep_directory(paths, 2026, generation_root=generation)
            with self.assertRaisesRegex(CLF.IngestError, "official anonymous limit"):
                CLF._run_sweep(
                    paths,
                    2025,
                    {
                        "LDA_RPM": "16",
                        "LDA_TERMS_CHECKED_AT": "2026-08-30T06:24:00Z",
                    },
                    generation_root=generation,
                )

    def test_lda_manifest_metadata_and_cross_year_uuid_are_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for year in (2025, 2026):
                directory = root / str(year)
                directory.mkdir()
                page = {"count": 1, "results": [{"filing_uuid": "shared", "filing_year": year}]}
                body = json.dumps(page).encode("utf-8")
                (directory / "page_00001.json").write_bytes(body)
                manifest = {"complete": True, "filing_year": year, "page_count": 1, "md5": {"page_00001.json": hashlib.md5(body).hexdigest()}}
                (directory / "sweep_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            receipt = audit_sweeps(root, expected_years=[2025, 2026])
            self.assertFalse(receipt["pass"])
            self.assertEqual(receipt["unique_filing_uuids"], 1)
            self.assertEqual(receipt["cross_year_duplicate_uuid_occurrences"], 1)

            bad_manifest = json.loads((root / "2026" / "sweep_manifest.json").read_text(encoding="utf-8"))
            bad_manifest["page_count"] = 2
            bad_manifest["filing_year"] = 2025
            (root / "2026" / "sweep_manifest.json").write_text(json.dumps(bad_manifest), encoding="utf-8")
            bad = audit_sweep_year(root / "2026")
            self.assertFalse(bad["manifest_page_count_matches"])
            self.assertFalse(bad["manifest_year_matches"])
            with self.assertRaisesRegex(LdaReconciliationError, "year inventory mismatch"):
                audit_sweeps(root, expected_years=[2024, 2025, 2026])

            shaped = lda_reconciliation_receipt(
                receipt, generated_at="2026-08-29T20:00:00Z", source_root_label="fixture://sweeps"
            )
            schema = json.loads((FEC / "staffer_network" / "schemas" / "lda_reconciliation_receipt.schema.json").read_text(encoding="utf-8"))
            validate_instance(shaped, schema)

    def test_lda_composite_url_and_timestamp_order_are_binding(self):
        url = LCR.bounded_page_url(
            2022, "2023-01-20", "2023-01-21", 3
        )
        self.assertIn("filing_year=2022", url)
        self.assertIn("filing_dt_posted_after=2023-01-20", url)
        self.assertIn("filing_dt_posted_before=2023-01-21", url)
        self.assertIn("ordering=dt_posted%2Cregistrant__name", url)
        page = {"results": [
            {"filing_year": 2022, "filing_uuid": "00000000-0000-0000-0000-000000000001", "dt_posted": "2023-01-20T12:00:00-05:00"},
            {"filing_year": 2022, "filing_uuid": "00000000-0000-0000-0000-000000000002", "dt_posted": "2023-01-20T12:00:00-05:00"},
        ]}
        key, hashes = LCR.validate_bounded_page(
            page, filing_year=2022, start_date="2023-01-20",
            end_date_exclusive="2023-01-21",
        )
        self.assertEqual(len(hashes), 2)
        # UUID order within a timestamp is deliberately not binding.
        LCR.validate_bounded_page(
            {"results": list(reversed(page["results"]))},
            filing_year=2022, start_date="2023-01-20",
            end_date_exclusive="2023-01-21", prior_order_key=None,
        )
        descending = json.loads(json.dumps(page))
        descending["results"][1]["dt_posted"] = "2023-01-20T11:59:59-05:00"
        with self.assertRaisesRegex(LCR.CompositeRepairError, "nondecreasing"):
            LCR.validate_bounded_page(
                descending,
                filing_year=2022, start_date="2023-01-20",
                end_date_exclusive="2023-01-21", prior_order_key=None,
            )
        self.assertIsNotNone(key)

    def test_lda_offline_ordering_proof_localizes_tied_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for year in LCR.LEGACY_YEARS:
                directory = root / str(year)
                directory.mkdir()
                filing = {
                    "filing_year": year,
                    "filing_uuid": f"00000000-0000-0000-0000-{year:012d}",
                    "dt_posted": f"{year}-01-01T00:00:00+00:00",
                }
                results = [filing]
                if year == 2022:
                    results.append(dict(filing))
                (directory / "page_00001.json").write_text(
                    json.dumps({"results": results}), encoding="utf-8"
                )
            audit = LCR.audit_legacy_ordering(root)
            self.assertTrue(all(row["ascending_monotonic"] for row in audit["years"]))
            self.assertEqual(audit["duplicate_occurrences"], 1)
            self.assertEqual(audit["duplicates_same_exact_timestamp"], 1)
            # The production proof expects exactly the known 88 repeats.
            self.assertFalse(audit["pass"])
            page = root / "2023" / "page_00001.json"
            payload = json.loads(page.read_text(encoding="utf-8"))
            payload["results"].insert(0, {
                "filing_year": 2023,
                "filing_uuid": "10000000-0000-0000-0000-000000000000",
                "dt_posted": "2024-01-01T00:00:00+00:00",
            })
            page.write_text(json.dumps(payload), encoding="utf-8")
            bad = LCR.audit_legacy_ordering(root)
            self.assertFalse(next(row for row in bad["years"] if row["year"] == 2023)["ascending_monotonic"])

    def test_lda_twin_comparison_is_uuid_and_payload_exact(self):
        base = {
            "manifest": {
                "advertised_count": 2,
                "uuid_inventory_sha256": "a",
                "payload_inventory_sha256": "b",
                "page_layout_sha256": "c",
                "base_repeated_uuid_occurrences": 0,
                "page_boundary_timestamps": [],
                "tie_cohorts": [],
            },
            "uuid_hashes": {"u1": "h1", "u2": "h2"},
        }
        same = json.loads(json.dumps(base))
        self.assertTrue(LCR.compare_twins(base, same)["pass"])
        changed = json.loads(json.dumps(base))
        changed["uuid_hashes"]["u2"] = "other"
        receipt = LCR.compare_twins(base, changed)
        self.assertFalse(receipt["pass"])
        self.assertEqual(receipt["payload_mismatches"], ["u2"])

    def test_lda_twin_comparison_ignores_only_live_registrant_timestamp(self):
        left_filing = {
            "filing_uuid": "u1",
            "registrant": {"id": 7, "name": "Firm", "dt_updated": "old"},
            "income": "1000.00",
        }
        right_filing = json.loads(json.dumps(left_filing))
        right_filing["registrant"]["dt_updated"] = "new"
        manifest = {
            "advertised_count": 1,
            "uuid_inventory_sha256": "same",
            "payload_inventory_sha256": "raw",
            "page_layout_sha256": "layout",
            "base_repeated_uuid_occurrences": 0,
            "page_boundary_timestamps": [],
            "tie_cohorts": [],
        }
        left = {
            "manifest": manifest,
            "uuid_hashes": {"u1": LCR._canonical_hash(left_filing)},
            "comparison_hashes": {"u1": LCR._twin_comparison_hash(left_filing)},
        }
        right = {
            "manifest": manifest,
            "uuid_hashes": {"u1": LCR._canonical_hash(right_filing)},
            "comparison_hashes": {"u1": LCR._twin_comparison_hash(right_filing)},
        }
        receipt = LCR.compare_twins(left, right)
        self.assertTrue(receipt["pass"])
        self.assertEqual(receipt["payload_mismatches"], [])
        self.assertEqual(receipt["raw_payload_mismatches"], ["u1"])
        self.assertEqual(
            receipt["ignored_live_registrant_timestamp_mismatches"], ["u1"]
        )

        right_filing["income"] = "2000.00"
        right["uuid_hashes"]["u1"] = LCR._canonical_hash(right_filing)
        right["comparison_hashes"]["u1"] = LCR._twin_comparison_hash(right_filing)
        self.assertFalse(LCR.compare_twins(left, right)["pass"])

    def test_lda_tie_repair_recovers_straddled_cohort(self):
        class Pacer:
            def wait(self):
                return None

        class Response:
            def __init__(self, url, payload):
                self.url, self.payload = url, payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def row(number, timestamp):
            return {
                "filing_year": 2026,
                "filing_uuid": f"00000000-0000-0000-0000-{number:012d}",
                "dt_posted": timestamp,
            }

        first = row(1, "2026-01-01T11:59:59+00:00")
        repeated = row(2, "2026-01-01T12:00:00+00:00")
        missed = row(3, "2026-01-01T12:00:00+00:00")
        last = row(4, "2026-01-01T12:00:01+00:00")

        def opener(request, timeout=None):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(request.full_url).query)
            start = query["filing_dt_posted_after"][0]
            if "T" in start:
                payload = {
                    "count": 2, "results": [repeated, missed], "next": None
                }
            elif query["page"][0] == "1":
                payload = {
                    "count": 4, "results": [first, repeated], "next": "page2"
                }
            else:
                payload = {
                    "count": 4, "results": [repeated, last], "next": None
                }
            return Response(request.full_url, payload)

        tie_key = LCR._timestamp_key(
            repeated["dt_posted"], label="fixture tie"
        )
        with tempfile.TemporaryDirectory() as temp:
            receipt = LCR.fetch_window(
                temp,
                window_id="fixture", twin="A", filing_year=2026,
                start_date="2026-01-01", end_date_exclusive="2026-01-02",
                headers={}, pacer=Pacer(), opener=opener,
                required_legacy_by_timestamp={tie_key: {
                    repeated["filing_uuid"], missed["filing_uuid"],
                }},
                page_size=2, max_in_flight_requests=2,
            )
            manifest = receipt["manifest"]
            self.assertEqual(manifest["base_repeated_uuid_occurrences"], 1)
            self.assertEqual(manifest["page_boundary_timestamps"], [tie_key])
            self.assertEqual(manifest["unique_filing_uuids"], 4)
            payloads = LCR._load_window_payloads(Path(temp), "fixture", "A")
            self.assertEqual(set(payloads), {
                first["filing_uuid"], repeated["filing_uuid"],
                missed["filing_uuid"], last["filing_uuid"],
            })

    def test_lda_tie_repair_has_planted_failures(self):
        class Pacer:
            def wait(self):
                return None

        class Response:
            def __init__(self, url, payload):
                self.url, self.payload = url, payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def row(number, timestamp="2026-01-01T12:00:00+00:00"):
            return {
                "filing_year": 2026,
                "filing_uuid": f"00000000-0000-0000-0000-{number:012d}",
                "dt_posted": timestamp,
            }

        cases = {
            "more than one page": {
                "count": 26,
                "results": [row(index + 1) for index in range(26)],
                "next": "page2",
            },
            "inclusive-before endpoint leakage": {
                "count": 1,
                "results": [row(1, "2026-01-01T12:00:01+00:00")],
                "next": None,
            },
            "duplicate UUID": {
                "count": 2,
                "results": [row(1), row(1)],
                "next": None,
            },
            "sub-second timestamp": {
                "count": 1,
                "results": [row(1, "2026-01-01T12:00:00.500000+00:00")],
                "next": None,
            },
        }
        for label, payload in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                def opener(request, timeout=None, payload=payload):
                    return Response(request.full_url, payload)
                with self.assertRaises(LCR.CompositeRepairError):
                    LCR._fetch_exact_second_cohort(
                        Path(temp), window_id="fixture", twin="A",
                        filing_year=2026,
                        timestamp_key="2026-01-01T12:00:00.000000Z",
                        headers={}, pacer=Pacer(), opener=opener,
                    )

        # Missing a required legacy member fails before repaired count promotion.
        repeated = row(2)
        missed = row(3)
        before = row(1, "2026-01-01T11:59:59+00:00")
        after = row(4, "2026-01-01T12:00:01+00:00")

        def incomplete_opener(request, timeout=None):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(request.full_url).query)
            start = query["filing_dt_posted_after"][0]
            if "T" in start:
                payload = {"count": 1, "results": [repeated], "next": None}
            elif query["page"][0] == "1":
                payload = {"count": 4, "results": [before, repeated], "next": "page2"}
            else:
                payload = {"count": 4, "results": [repeated, after], "next": None}
            return Response(request.full_url, payload)

        tie_key = LCR._timestamp_key(repeated["dt_posted"], label="fixture")
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(LCR.CompositeRepairError, "missing UUIDs"):
                LCR.fetch_window(
                    temp, window_id="incomplete", twin="A", filing_year=2026,
                    start_date="2026-01-01", end_date_exclusive="2026-01-02",
                    headers={}, pacer=Pacer(), opener=incomplete_opener,
                    required_legacy_by_timestamp={tie_key: {
                        repeated["filing_uuid"], missed["filing_uuid"],
                    }},
                    page_size=2, max_in_flight_requests=2,
                )

        # A repeated payload away from a same-timestamp boundary is rejected.
        duplicate = row(10, "2026-01-01T11:00:00+00:00")
        middle = row(11, "2026-01-01T11:30:00+00:00")
        later = row(12, "2026-01-01T12:00:00+00:00")

        def nontie_opener(request, timeout=None):
            from urllib.parse import parse_qs, urlparse
            page_number = parse_qs(urlparse(request.full_url).query)["page"][0]
            payload = (
                {"count": 3, "results": [duplicate, middle], "next": "page2"}
                if page_number == "1"
                else {"count": 3, "results": [later, duplicate], "next": None}
            )
            return Response(request.full_url, payload)

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(LCR.CompositeRepairError):
                LCR.fetch_window(
                    temp, window_id="nontie", twin="A", filing_year=2026,
                    start_date="2026-01-01", end_date_exclusive="2026-01-02",
                    headers={}, pacer=Pacer(), opener=nontie_opener,
                    page_size=2, max_in_flight_requests=2,
                )

        # Reach T1 directly: same timestamp boundary, but changed payload.
        changed_repeat = dict(repeated)
        changed_repeat["amendment_type"] = "changed"

        def changed_repeat_opener(request, timeout=None):
            from urllib.parse import parse_qs, urlparse
            page_number = parse_qs(urlparse(request.full_url).query)["page"][0]
            payload = (
                {"count": 4, "results": [before, repeated], "next": "page2"}
                if page_number == "1"
                else {"count": 4, "results": [changed_repeat, after], "next": None}
            )
            return Response(request.full_url, payload)

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(
                LCR.CompositeRepairError,
                "not an identical same-timestamp boundary repeat",
            ):
                LCR.fetch_window(
                    temp, window_id="changed-repeat", twin="A", filing_year=2026,
                    start_date="2026-01-01", end_date_exclusive="2026-01-02",
                    headers={}, pacer=Pacer(), opener=changed_repeat_opener,
                    page_size=2, max_in_flight_requests=2,
                )

    def test_lda_split_coverage_includes_full_year_and_fails_on_gap(self):
        cohorts = {year: {} for year in LCR.LEGACY_YEARS}
        key = "2026-07-20T12:00:00.000000Z"
        cohorts[2026][key] = {"u1", "u2"}
        late_eastern_key = "2026-01-21T03:58:07.000000Z"
        cohorts[2025][late_eastern_key] = {"u3"}
        specs = [{
            "window_id": "full-2026",
            "kind": "FULL_MOVING_YEAR",
            "filing_year": 2026,
            "start_date": "2026-01-01",
            "end_date_exclusive": "2026-08-30",
        }, {
            "window_id": "repair-local-day",
            "kind": "PAGINATION_BOUNDARY_REPAIR",
            "filing_year": 2025,
            "start_date": "2026-01-20",
            "end_date_exclusive": "2026-01-21",
        }]
        slices, receipt = LCR.prove_split_cohort_coverage(specs, cohorts)
        self.assertTrue(receipt["pass"])
        self.assertEqual(slices["full-2026"][key], {"u1", "u2"})
        self.assertEqual(
            slices["repair-local-day"][late_eastern_key], {"u3"}
        )
        specs[0]["end_date_exclusive"] = "2026-07-01"
        with self.assertRaisesRegex(LCR.CompositeRepairError, "not covered"):
            LCR.prove_split_cohort_coverage(specs, cohorts)

    def test_lda_page_shape_handles_zero_divisible_and_failures(self):
        LCR.validate_page_shape(
            {"count": 0, "results": [], "next": None},
            page_number=1, total_pages=1, advertised_count=0,
        )
        rows = [{} for _ in range(25)]
        LCR.validate_page_shape(
            {"count": 50, "results": rows, "next": "page2"},
            page_number=1, total_pages=2, advertised_count=50,
        )
        LCR.validate_page_shape(
            {"count": 50, "results": rows, "next": None},
            page_number=2, total_pages=2, advertised_count=50,
        )
        bad_pages = [
            ({"count": 51, "results": rows, "next": "page2"}, 1, 2, 50),
            ({"count": 50, "results": rows[:-1], "next": "page2"}, 1, 2, 50),
            ({"count": 50, "results": rows, "next": None}, 1, 2, 50),
            ({"count": 50, "results": rows, "next": "extra"}, 2, 2, 50),
        ]
        for page, number, total, count in bad_pages:
            with self.subTest(page=number, count=page["count"]):
                with self.assertRaises(LCR.CompositeRepairError):
                    LCR.validate_page_shape(
                        page, page_number=number, total_pages=total,
                        advertised_count=count,
                    )

    def test_lda_shared_pacer_and_batched_transport_are_bounded_and_equivalent(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        import time
        from urllib.parse import parse_qs, urlparse

        logical_time = [0.0]
        starts = []

        def monotonic():
            return logical_time[0]

        def sleep(delay):
            logical_time[0] += delay

        pacer = LCR.SharedRequestPacer(
            120, sleep_fn=sleep, monotonic_fn=monotonic
        )
        for _ in range(8):
            pacer.wait()
            starts.append(logical_time[0])
        self.assertEqual(starts, [index * 0.5 for index in range(8)])

        contended = LCR.SharedRequestPacer(
            120, sleep_fn=lambda _delay: None, monotonic_fn=lambda: 0.0
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _index: contended.wait(), range(8)))
        self.assertEqual(contended._next_start, 4.0)

        class NoWait:
            def wait(self):
                return None

        class Response:
            def __init__(self, url, payload):
                self.url, self.payload = url, payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        active = [0]
        maximum = [0]
        lock = threading.Lock()
        batched_mode = [False]
        eight_party_barrier = threading.Barrier(8)

        def row(index):
            minute, second = divmod(index, 60)
            return {
                "filing_year": 2026,
                "filing_uuid": f"30000000-0000-0000-0000-{index + 1:012d}",
                "dt_posted": f"2026-01-01T12:{minute:02d}:{second:02d}+00:00",
            }

        def opener(request, timeout=None):
            page_number = int(parse_qs(urlparse(request.full_url).query)["page"][0])
            with lock:
                active[0] += 1
                maximum[0] = max(maximum[0], active[0])
            try:
                if batched_mode[0] and 2 <= page_number <= 9:
                    eight_party_barrier.wait(timeout=2)
                time.sleep(0.005 * (11 - page_number))
                first = (page_number - 1) * 25
                results = [row(index) for index in range(first, first + 25)]
                payload = {
                    "count": 250,
                    "results": results,
                    "next": None if page_number == 10 else f"page{page_number + 1}",
                }
                return Response(request.full_url, payload)
            finally:
                with lock:
                    active[0] -= 1

        with tempfile.TemporaryDirectory() as temp:
            sequential = LCR.fetch_window(
                Path(temp) / "sequential", window_id="window", twin="A",
                filing_year=2026, start_date="2026-01-01",
                end_date_exclusive="2026-01-02", headers={}, pacer=NoWait(),
                opener=opener, max_in_flight_requests=1,
            )
            maximum[0] = 0
            batched_mode[0] = True
            batched = LCR.fetch_window(
                Path(temp) / "batched", window_id="window", twin="A",
                filing_year=2026, start_date="2026-01-01",
                end_date_exclusive="2026-01-02", headers={}, pacer=NoWait(),
                opener=opener, max_in_flight_requests=8,
            )
            self.assertEqual(sequential["uuid_hashes"], batched["uuid_hashes"])
            self.assertEqual(
                sequential["manifest"]["payload_inventory_sha256"],
                batched["manifest"]["payload_inventory_sha256"],
            )
            self.assertEqual(
                sequential["manifest"]["page_layout_sha256"],
                batched["manifest"]["page_layout_sha256"],
            )
            self.assertEqual(maximum[0], 8)
            with self.assertRaises(LCR.CompositeRepairError):
                LCR.fetch_window(
                    Path(temp) / "excess", window_id="window", twin="A",
                    filing_year=2026, start_date="2026-01-01",
                    end_date_exclusive="2026-01-02", headers={}, pacer=NoWait(),
                    opener=opener, max_in_flight_requests=9,
                )

    def test_lda_batched_worker_and_count_drift_fail_without_manifest(self):
        from urllib.parse import parse_qs, urlparse

        class NoWait:
            def wait(self):
                return None

        class Response:
            def __init__(self, url, payload):
                self.url, self.payload = url, payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def row(index):
            minute, second = divmod(index, 60)
            return {
                "filing_year": 2026,
                "filing_uuid": f"40000000-0000-0000-0000-{index + 1:012d}",
                "dt_posted": f"2026-01-01T00:{minute:02d}:{second:02d}+00:00",
            }

        def opener_for(mode):
            def opener(request, timeout=None):
                page_number = int(
                    parse_qs(urlparse(request.full_url).query)["page"][0]
                )
                if mode == "worker" and page_number == 3:
                    raise urllib.error.HTTPError(
                        request.full_url, 400, "planted", {}, None
                    )
                first = (page_number - 1) * 25
                count = 101 if mode == "drift" and page_number == 3 else 100
                return Response(request.full_url, {
                    "count": count,
                    "results": [row(index) for index in range(first, first + 25)],
                    "next": None if page_number == 4 else f"page{page_number + 1}",
                })
            return opener

        for mode in ("worker", "drift"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(Exception):
                    LCR.fetch_window(
                        temp, window_id="window", twin="A", filing_year=2026,
                        start_date="2026-01-01", end_date_exclusive="2026-01-02",
                        headers={}, pacer=NoWait(), opener=opener_for(mode),
                        max_in_flight_requests=4,
                    )
                self.assertFalse(
                    (Path(temp) / "windows" / "window" / "A" /
                     "window_manifest.json").exists()
                )

    def test_lda_resume_replays_prefix_repairs_seam_and_matches_fresh(self):
        from urllib.parse import parse_qs, urlparse

        class NoWait:
            def wait(self):
                return None

        class Response:
            def __init__(self, url, payload):
                self.url, self.payload = url, payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def row(number, timestamp):
            return {
                "filing_year": 2026,
                "filing_uuid": f"50000000-0000-0000-0000-{number:012d}",
                "dt_posted": timestamp,
            }

        before = row(1, "2026-01-01T11:59:59+00:00")
        tie_a = row(2, "2026-01-01T12:00:00+00:00")
        tie_b = row(3, "2026-01-01T12:00:00+00:00")
        after = row(4, "2026-01-01T12:00:01+00:00")
        resume_base_calls = []

        def payload(request, *, fail_page_two=False, track=False):
            query = parse_qs(urlparse(request.full_url).query)
            start = query["filing_dt_posted_after"][0]
            if "T" in start:
                return {"count": 2, "results": [tie_a, tie_b], "next": None}
            page_number = int(query["page"][0])
            if track:
                resume_base_calls.append(page_number)
            if fail_page_two and page_number == 2:
                raise urllib.error.HTTPError(
                    request.full_url, 400, "planted terminal failure", {}, None
                )
            return (
                {"count": 4, "results": [before, tie_a], "next": "page2"}
                if page_number == 1
                else {"count": 4, "results": [tie_b, after], "next": None}
            )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            partial_root = root / "partial"

            def failing(request, timeout=None):
                return Response(
                    request.full_url,
                    payload(request, fail_page_two=True),
                )

            with self.assertRaises(Exception):
                LCR.fetch_window(
                    partial_root, window_id="window", twin="A",
                    filing_year=2026, start_date="2026-01-01",
                    end_date_exclusive="2026-01-02", headers={},
                    pacer=NoWait(), opener=failing, page_size=2,
                    max_in_flight_requests=1,
                )
            partial_directory = partial_root / "windows" / "window" / "A"
            self.assertEqual(
                [path.name for path in partial_directory.iterdir()],
                ["page_00001.json"],
            )
            residue = partial_directory / ".tmp.residue"
            residue.write_text("planted", encoding="utf-8")
            with self.assertRaisesRegex(
                LCR.CompositeRepairError, "unexpected file"
            ):
                LCR.fetch_window(
                    partial_root, window_id="window", twin="A",
                    filing_year=2026, start_date="2026-01-01",
                    end_date_exclusive="2026-01-02", headers={},
                    pacer=NoWait(), opener=failing, page_size=2,
                    max_in_flight_requests=1,
                    resume_existing_base_pages=True,
                )
            residue.unlink()

            def resumed_opener(request, timeout=None):
                return Response(
                    request.full_url, payload(request, track=True)
                )

            resumed = LCR.fetch_window(
                partial_root, window_id="window", twin="A",
                filing_year=2026, start_date="2026-01-01",
                end_date_exclusive="2026-01-02", headers={},
                pacer=NoWait(), opener=resumed_opener, page_size=2,
                max_in_flight_requests=1,
                resume_existing_base_pages=True,
            )
            self.assertEqual(resume_base_calls, [2])
            tie_key = LCR._timestamp_key(tie_a["dt_posted"], label="resume tie")
            self.assertEqual(
                resumed["manifest"]["page_boundary_timestamps"], [tie_key]
            )

            def fresh_opener(request, timeout=None):
                return Response(request.full_url, payload(request))

            fresh = LCR.fetch_window(
                root / "fresh", window_id="window", twin="A",
                filing_year=2026, start_date="2026-01-01",
                end_date_exclusive="2026-01-02", headers={},
                pacer=NoWait(), opener=fresh_opener, page_size=2,
                max_in_flight_requests=1,
            )
            self.assertEqual(resumed["uuid_hashes"], fresh["uuid_hashes"])
            for key in (
                "page_layout_sha256", "uuid_inventory_sha256",
                "payload_inventory_sha256", "advertised_count",
                "unique_filing_uuids", "base_filing_objects",
                "base_unique_filing_uuids", "base_repeated_uuid_occurrences",
                "page_boundary_timestamps", "page_count", "files", "tie_cohorts",
            ):
                self.assertEqual(
                    resumed["manifest"][key], fresh["manifest"][key], key
                )

    def test_lda_failed_resume_status_is_hash_and_contract_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            status_path = root / "generation_status.json"
            legacy = root / "legacy"
            status = {
                "schema_id": "staffer-network-lda-composite-generation-status-v1",
                "generation_id": "generation",
                "status": "FAILED",
                "authentication_mode": "registered",
                "rate_limit_rpm": 120,
                "base_page_size": LCR.PAGE_SIZE,
                "max_in_flight_requests": LCR.MAX_IN_FLIGHT_REQUESTS,
                "transport_mode": "BOUNDED_BATCH_ASCENDING_PROCESSING",
                "ordering": LCR.ORDERING,
                "cutoff_exclusive": "2026-08-30",
                "legacy_root": str(legacy),
                "error": "window twin A page 3 planted failure",
            }
            status_path.write_text(json.dumps(status), encoding="utf-8")
            digest = hashlib.sha256(status_path.read_bytes()).hexdigest()
            loaded = LCR._load_failed_resume_state(
                status_path, expected_sha256=digest,
                generation_id="generation", cutoff_exclusive="2026-08-30",
                legacy_root=legacy,
            )
            self.assertEqual(loaded["error"], status["error"])
            with self.assertRaisesRegex(
                LCR.CompositeRepairError, "status hash"
            ):
                LCR._load_failed_resume_state(
                    status_path, expected_sha256="0" * 64,
                    generation_id="generation", cutoff_exclusive="2026-08-30",
                    legacy_root=legacy,
                )
            status["ordering"] = "wrong"
            status_path.write_text(json.dumps(status), encoding="utf-8")
            changed_digest = hashlib.sha256(status_path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(
                LCR.CompositeRepairError, "ordering mismatch"
            ):
                LCR._load_failed_resume_state(
                    status_path, expected_sha256=changed_digest,
                    generation_id="generation", cutoff_exclusive="2026-08-30",
                    legacy_root=legacy,
                )

    def test_lda_resume_contiguous_prefix_and_completed_replay_fail_closed(self):
        class NoWait:
            def wait(self):
                return None

        class Response:
            def __init__(self, url, payload):
                self.url, self.payload = url, payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def rows(count):
            return [{
                "filing_year": 2026,
                "filing_uuid": f"60000000-0000-0000-0000-{index + 1:012d}",
                "dt_posted": f"2026-01-01T12:00:{index:02d}+00:00",
            } for index in range(count)]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            partial = root / "partial" / "windows" / "window" / "A"
            partial.mkdir(parents=True)
            all_rows = rows(51)
            for number in (1, 2):
                first = (number - 1) * 25
                (partial / f"page_{number:05d}.json").write_text(
                    json.dumps({
                        "count": 51,
                        "results": all_rows[first:first + 25],
                        "next": f"page{number + 1}",
                    }),
                    encoding="utf-8",
                )
            def resume_opener(request, timeout=None):
                return Response(request.full_url, {
                    "count": 51, "results": all_rows[50:], "next": None,
                })

            resumed = LCR.fetch_window(
                root / "partial", window_id="window", twin="A",
                filing_year=2026, start_date="2026-01-01",
                end_date_exclusive="2026-01-02", headers={},
                pacer=NoWait(), opener=resume_opener,
                max_in_flight_requests=8,
                resume_existing_base_pages=True,
            )
            self.assertEqual(resumed["manifest"]["page_count"], 3)
            self.assertEqual(resumed["manifest"]["unique_filing_uuids"], 51)

            one_page = rows(25)

            def opener(request, timeout=None):
                return Response(request.full_url, {
                    "count": 25, "results": one_page, "next": None,
                })

            complete_root = root / "complete"
            LCR.fetch_window(
                complete_root, window_id="window", twin="A",
                filing_year=2026, start_date="2026-01-01",
                end_date_exclusive="2026-01-02", headers={},
                pacer=NoWait(), opener=opener,
            )
            spec = {
                "window_id": "window", "filing_year": 2026,
                "start_date": "2026-01-01",
                "end_date_exclusive": "2026-01-02",
            }
            loaded = LCR._load_completed_window_result(
                complete_root, spec, "A"
            )
            self.assertEqual(len(loaded["uuid_hashes"]), 25)
            page = (
                complete_root / "windows" / "window" / "A" /
                "page_00001.json"
            )
            page.write_bytes(page.read_bytes() + b" ")
            with self.assertRaisesRegex(
                LCR.CompositeRepairError, "file binding failed"
            ):
                LCR._load_completed_window_result(complete_root, spec, "A")

    def test_lda_filter_probe_requires_half_open_partition_identity(self):
        class Pacer:
            def wait(self):
                return None

        class Response:
            def __init__(self, url, payload):
                self.url, self.payload = url, payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        historical_first = [{
            "filing_year": 2022,
            "filing_uuid": "10000000-0000-0000-0000-000000000001",
            "dt_posted": "2023-01-20T09:31:13-05:00",
        }, {
            "filing_year": 2022,
            "filing_uuid": "10000000-0000-0000-0000-000000000002",
            "dt_posted": "2023-01-20T09:31:13-05:00",
        }]
        historical_second = [{
            "filing_year": 2022,
            "filing_uuid": "10000000-0000-0000-0000-000000000003",
            "dt_posted": "2023-01-20T09:31:14-05:00",
        }]
        historical_control = {
            "filing_year": 2022,
            "first_timestamp": "2023-01-20T14:31:13Z",
            "first_uuid_hashes": {
                row["filing_uuid"]: LCR._canonical_hash(row)
                for row in historical_first
            },
            "next_uuid_hashes": {
                row["filing_uuid"]: LCR._canonical_hash(row)
                for row in historical_second
            },
        }

        def opener(request, timeout=None):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(request.full_url).query)
            start = query.get("filing_dt_posted_after", [None])[0]
            end = query.get("filing_dt_posted_before", [None])[0]
            counts = {
                ("2026-08-01", "2026-08-02"): 2,
                ("2026-08-02", "2026-08-03"): 3,
                ("2026-08-01", "2026-08-03"): 5,
                (None, "2026-08-03"): 100,
                ("2026-08-01T16:00:00Z", "2026-08-01T16:00:00.999999Z"): 2,
                ("2026-08-01T16:00:01Z", "2026-08-01T16:00:01.999999Z"): 1,
                ("2026-08-01T16:00:00Z", "2026-08-01T16:00:01.999999Z"): 3,
                ("2026-08-01T00:00:00Z", "2026-08-01T00:00:00.999999Z"): 0,
                ("2026-08-01T12:00:00-04:00", "2026-08-01T12:00:00.999999-04:00"): 2,
                ("2023-01-20T14:31:13Z", "2023-01-20T14:31:13.999999Z"): 2,
                ("2023-01-20T14:31:14Z", "2023-01-20T14:31:14.999999Z"): 1,
                ("2023-01-20T14:31:13Z", "2023-01-20T14:31:14.999999Z"): 3,
            }
            count = counts[(start, end)]
            rows_by_window = {
                ("2023-01-20T14:31:13Z", "2023-01-20T14:31:13.999999Z"): historical_first,
                ("2023-01-20T14:31:14Z", "2023-01-20T14:31:14.999999Z"): historical_second,
                ("2023-01-20T14:31:13Z", "2023-01-20T14:31:14.999999Z"): historical_first + historical_second,
            }
            rows = list(rows_by_window.get((start, end), []))
            timestamps = {
                "2026-08-01": "2026-08-01T12:00:00-04:00",
                "2026-08-02": "2026-08-02T12:00:00-04:00",
                "2026-08-01T16:00:00Z": "2026-08-01T12:00:00-04:00",
                "2026-08-01T16:00:01Z": "2026-08-01T12:00:01-04:00",
                "2026-08-01T12:00:00-04:00": "2026-08-01T12:00:00-04:00",
            }
            if not rows and start in timestamps and count:
                rows = [{
                    "filing_year": 2026,
                    "filing_uuid": "00000000-0000-0000-0000-000000000001",
                    "dt_posted": timestamps[start],
                }]
            return Response(request.full_url, {"count": count, "results": rows, "next": None})

        receipt = LCR.validate_filter_semantics(
            headers={}, pacer=Pacer(), opener=opener,
            historical_control=historical_control,
        )
        self.assertTrue(receipt["pass"])
        self.assertEqual(receipt["first_count"] + receipt["second_count"], receipt["union_count"])
        self.assertTrue(receipt["exact_second_partition_identity"])
        self.assertEqual(receipt["empty_exact_second_count"], 0)
        self.assertTrue(receipt["offset_equivalence_pass"])

    def test_lda_historical_boundary_probe_rejects_widened_endpoint(self):
        class Pacer:
            def wait(self):
                return None

        class Response:
            def __init__(self, url, payload):
                self.url, self.payload = url, payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        first = {
            "filing_year": 2022,
            "filing_uuid": "20000000-0000-0000-0000-000000000001",
            "dt_posted": "2023-01-20T09:31:13-05:00",
        }
        leaked_endpoint = {
            "filing_year": 2022,
            "filing_uuid": "20000000-0000-0000-0000-000000000002",
            "dt_posted": "2023-01-20T09:31:14-05:00",
        }
        control = {
            "filing_year": 2022,
            "first_timestamp": "2023-01-20T14:31:13Z",
            "first_uuid_hashes": {
                first["filing_uuid"]: LCR._canonical_hash(first)
            },
            "next_uuid_hashes": {
                leaked_endpoint["filing_uuid"]: LCR._canonical_hash(
                    leaked_endpoint
                )
            },
        }

        def opener(request, timeout=None):
            # Simulate an ignored microsecond end that leaks the next endpoint.
            return Response(request.full_url, {
                "count": 2,
                "results": [first, leaked_endpoint],
                "next": None,
            })

        with self.assertRaisesRegex(LCR.CompositeRepairError, "outside"):
            LCR.validate_historical_datetime_control(
                headers={}, pacer=Pacer(), opener=opener, control=control
            )

    def test_lda_filter_probe_rejects_date_truncated_datetime_filters(self):
        class Pacer:
            def wait(self):
                return None

        class Response:
            def __init__(self, url, payload):
                self.url, self.payload = url, payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def geturl(self):
                return self.url
            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def opener(request, timeout=None):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(request.full_url).query)
            start = query.get("filing_dt_posted_after", [None])[0]
            end = query.get("filing_dt_posted_before", [None])[0]
            date_counts = {
                ("2026-08-01", "2026-08-02"): 2,
                ("2026-08-02", "2026-08-03"): 3,
                ("2026-08-01", "2026-08-03"): 5,
                (None, "2026-08-03"): 100,
            }
            if (start, end) in date_counts:
                count = date_counts[(start, end)]
            else:
                # Simulate an API that truncates every datetime to its date.
                count = 2
            rows = [] if start is None else [{
                "filing_year": 2026,
                "filing_uuid": "00000000-0000-0000-0000-000000000001",
                "dt_posted": "2026-08-01T12:00:00-04:00",
            }]
            return Response(
                request.full_url,
                {"count": count, "results": rows[:1], "next": None},
            )

        with self.assertRaises(LCR.CompositeRepairError):
            LCR.validate_filter_semantics(
                headers={}, pacer=Pacer(), opener=opener
            )

    def test_lda_secret_scan_detects_split_and_plain_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = "test-secret-token"
            (root / "safe.json").write_text('{"ok":true}', encoding="utf-8")
            self.assertTrue(LCR.scan_for_secret(root, secret)["pass"])
            (root / "bad.log").write_text("prefix " + secret + " suffix", encoding="utf-8")
            receipt = LCR.scan_for_secret(root, secret)
            self.assertFalse(receipt["pass"])
            self.assertEqual(receipt["matches"], ["bad.log"])
            (root / "bad.log").unlink()
            boundary = 1024 * 1024
            prefix = b"x" * (boundary - len(secret) // 2)
            (root / "split.bin").write_bytes(prefix + secret.encode("utf-8"))
            split = LCR.scan_for_secret(root, secret)
            self.assertFalse(split["pass"])
            self.assertEqual(split["matches"], ["split.bin"])

    def test_lda_composite_refuses_synced_generation_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            synced = Path(temp) / "OneDrive-fixture"
            generation = synced / "raw" / "generation"
            with self.assertRaisesRegex(
                LCR.CompositeRepairError, "outside synced storage"
            ):
                LCR.refuse_synced_generation_root(generation, [synced])
            local = Path(temp) / "local" / "generation"
            LCR.refuse_synced_generation_root(local, [synced])

    def test_committed_lda_supersession_receipt_is_registered_and_conservative(self):
        path = (
            FEC.parent / "data" / "staffer_network" / "receipts" /
            "lda_anonymous_supersession_20260830.json"
        )
        receipt = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads((
            FEC / "staffer_network" / "schemas" /
            "lda_anonymous_supersession_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(receipt, schema)
        _validate_record_semantics(receipt, receipt["schema_id"])
        self.assertEqual(
            receipt["legacy_inventory_before_sha256"],
            LCR.APPROVED_LEGACY_INVENTORY_SHA256,
        )

    def test_lda_source_native_extraction_keeps_namespaces_and_gates_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "legacy"
            composite = root / "composite"
            output = root / "output"
            composite.mkdir()
            per_year = []
            for year in LCR.LEGACY_YEARS:
                directory = legacy / str(year)
                directory.mkdir(parents=True)
                activity = []
                if year == 2026:
                    activity = [{
                        "general_issue_code": "FIN",
                        "general_issue_code_display": "Financial Institutions/Investments/Securities",
                        "description": "Synthetic issue",
                        "government_entities": [{"id": 1, "name": "U.S. House of Representatives"}],
                        "lobbyists": [{
                            "lobbyist": {"id": 77, "first_name": "Alex", "middle_name": None, "last_name": "Example", "suffix": None, "prefix": None, "nickname": None},
                            "covered_position": "Legislative Director, U.S. House",
                        }],
                    }]
                filing = {
                    "filing_uuid": f"fixture-{year}",
                    "filing_year": year,
                    "filing_period": "first_quarter",
                    "dt_posted": f"{year}-01-02T12:00:00+00:00",
                    "registrant": {"id": 10, "name": "Fixture Registrant", "description": "Firm", "country": "US", "state": "DC", "contact_name": "excluded"},
                    "client": {"id": 20, "client_id": 1, "name": "Fixture Client", "general_description": "Client", "country": "US", "state": "MN"},
                    "lobbying_activities": activity,
                }
                (directory / "page_00001.json").write_text(
                    json.dumps({"results": [filing]}), encoding="utf-8"
                )
                per_year.append({"year": year, "composite_unique_filing_uuids": 1})
            composite_receipt = {
                "status": "PASS",
                "cutoff_exclusive": "2026-08-30",
                "windows": [],
                "per_year": per_year,
                "downstream_gates": {"identity_resolution": "CLOSED"},
            }
            (composite / "composite_reconciliation_receipt.json").write_text(
                json.dumps(composite_receipt), encoding="utf-8"
            )
            receipt = LSC.build_lda_source_native_corpus(
                legacy, composite, output,
                generated_at="2026-08-30T18:00:00Z",
            )
            self.assertEqual(receipt["filings"], 6)
            self.assertEqual(receipt["lobbyists"], 1)
            self.assertEqual(receipt["registrants"], 1)
            self.assertEqual(receipt["clients"], 1)
            self.assertEqual(receipt["activities"], 1)
            self.assertEqual(receipt["industry_edges"], 3)
            self.assertEqual(receipt["covered_position_assertions"], 1)
            self.assertEqual(receipt["canonical_identity_merges"], 0)
            self.assertFalse(receipt["analysis_enabled"])
            self.assertFalse(receipt["publication_enabled"])
            receipt_schema = json.loads((
                FEC / "staffer_network" / "schemas" /
                "lda_source_native_corpus_receipt.schema.json"
            ).read_text(encoding="utf-8"))
            validate_instance(receipt, receipt_schema)
            _validate_record_semantics(receipt, receipt["schema_id"])
            schemas = FEC / "staffer_network" / "schemas"
            for entry in receipt["files"]:
                artifact = output / entry["path"]
                self.assertEqual(artifact.stat().st_size, entry["bytes"])
                self.assertEqual(sha256_file(artifact), entry["sha256"])
                row_schema = json.loads(
                    schema_path(schemas, entry["schema_id"]).read_text(
                        encoding="utf-8"
                    )
                )
                observed = 0
                with gzip.open(artifact, "rt", encoding="utf-8") as stream:
                    for line in stream:
                        row = json.loads(line)
                        validate_instance(row, row_schema)
                        _validate_record_semantics(row, entry["schema_id"])
                        observed += 1
                self.assertEqual(observed, entry["records"])
            lobbyist_file = output / "lda_lobbyist_assertions.ndjson.gz"
            with gzip.open(lobbyist_file, "rt", encoding="utf-8") as stream:
                lobbyist = json.loads(next(stream))
            self.assertEqual(lobbyist["source_person_id"], "lda:lobbyist:77")
            self.assertIn("Legislative Director, U.S. House", lobbyist["covered_position_values"])
            self.assertNotIn("contact_name", json.dumps(receipt))

    def test_lda_identity_crosswalk_is_candidate_only_and_conserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lda, politico, native, output = (
                root / "lda", root / "politico", root / "native", root / "out"
            )
            for directory in (lda, politico, native):
                directory.mkdir()
            (lda / "lda_source_native_corpus_receipt.json").write_text(
                json.dumps({
                    "schema_id": "staffer-network-lda-source-native-corpus-receipt-v1",
                    "status": "PASS", "lobbyists": 1,
                    "canonical_identity_merges": 0,
                    "name_matches_are_candidates_only": True,
                }), encoding="utf-8",
            )
            lobbyist = {
                "source_person_id": "lda:lobbyist:77",
                "display_name": "Alex Example",
                "name_variants": ["Alex Example"],
                "covered_position_support_count": 1,
                "covered_position_values": ["Legislative Director, U.S. House"],
            }
            with gzip.open(
                lda / "lda_lobbyist_assertions.ndjson.gz", "wt", encoding="utf-8"
            ) as stream:
                stream.write(json.dumps(lobbyist) + "\n")
            (politico / "normalization_receipt.json").write_text(
                json.dumps({"status": "PASS"}), encoding="utf-8"
            )
            (politico / "persons.ndjson").write_text(json.dumps({
                "source_person_id": "politico:person:p1",
                "display_name": "Alex Example",
            }) + "\n", encoding="utf-8")
            (native / "source_native_corpus_receipt.json").write_text(
                json.dumps({"status": "PASS"}), encoding="utf-8"
            )
            (native / "quorum_legacy_source_native_seed.ndjson").write_text(
                json.dumps({
                    "source_entity_id": "quorum:staff:q1",
                    "display_name": "Alex Example",
                }) + "\n", encoding="utf-8"
            )
            with gzip.open(
                native / "littlesis_entities.ndjson.gz", "wt", encoding="utf-8"
            ) as stream:
                stream.write(json.dumps({
                    "source_entity_id": "littlesis:person:l1",
                    "name": "A. Different",
                    "aliases": ["Alex Example"],
                    "types": ["Person"],
                }) + "\n")
                stream.write(json.dumps({
                    "source_entity_id": "littlesis:org:o1",
                    "name": "Alex Example", "aliases": [], "types": ["Org"],
                }) + "\n")
            lobbyist_path = lda / "lda_lobbyist_assertions.ndjson.gz"
            politico_path = politico / "persons.ndjson"
            quorum_path = native / "quorum_legacy_source_native_seed.ndjson"
            littlesis_path = native / "littlesis_entities.ndjson.gz"
            def bound(file_path, **values):
                return {
                    **values, "bytes": file_path.stat().st_size,
                    "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
                }
            (lda / "lda_source_native_corpus_receipt.json").write_text(
                json.dumps({
                    "schema_id": "staffer-network-lda-source-native-corpus-receipt-v1",
                    "status": "PASS", "lobbyists": 1,
                    "canonical_identity_merges": 0,
                    "name_matches_are_candidates_only": True,
                    "files": [bound(lobbyist_path, role="lobbyist_assertions")],
                }), encoding="utf-8",
            )
            (politico / "normalization_receipt.json").write_text(
                json.dumps({
                    "schema_id": "politico-directory-normalization-receipt-v1",
                    "status": "PASS", "canonical_entity_merges": 0,
                    "files": [bound(politico_path, path="persons.ndjson")],
                }), encoding="utf-8",
            )
            (native / "source_native_corpus_receipt.json").write_text(
                json.dumps({
                    "schema_id": "staffer-network-source-native-corpus-receipt-v1",
                    "status": "PASS", "canonical_entity_merges": 0,
                    "cross_source_identity_assertions": 0,
                    "identity_resolution_advanced": False,
                    "record_files": [
                        bound(quorum_path, role="quorum-legacy-source-native-seed"),
                        bound(littlesis_path, role="littlesis-entities"),
                    ],
                }), encoding="utf-8",
            )
            receipt = LIC.build_candidates(
                lda, politico, native, output,
                generated_at="2026-08-30T18:30:00Z",
            )
            self.assertEqual(receipt["candidate_count"], 3)
            self.assertEqual(receipt["review_required_count"], 3)
            self.assertEqual(receipt["confidence_values_emitted"], 0)
            self.assertEqual(receipt["canonical_identity_merges"], 0)
            self.assertEqual(receipt["downstream_identity_gate"], "CLOSED")
            self.assertEqual(
                receipt["method"],
                "NORMALIZED_NAME_EQUALITY_REVIEW_CANDIDATES_ONLY",
            )
            self.assertEqual(receipt["candidates_with_any_name_collision"], 3)
            self.assertEqual(
                receipt["candidates_with_cross_source_name_multiplicity"], 3
            )
            receipt_schema = json.loads((
                FEC / "staffer_network" / "schemas" /
                "lda_identity_candidate_receipt.schema.json"
            ).read_text(encoding="utf-8"))
            candidate_schema = json.loads((
                FEC / "staffer_network" / "schemas" /
                "lda_identity_candidate.schema.json"
            ).read_text(encoding="utf-8"))
            validate_instance(receipt, receipt_schema)
            _validate_record_semantics(receipt, receipt["schema_id"])
            with gzip.open(
                output / "lda_identity_candidates.ndjson.gz", "rt", encoding="utf-8"
            ) as stream:
                rows = [json.loads(line) for line in stream]
            self.assertEqual(len(rows), 3)
            for row in rows:
                validate_instance(row, candidate_schema)
                _validate_record_semantics(row, row["schema_id"])
                self.assertEqual(row["review_status"], "REVIEW_REQUIRED")
                self.assertIsNone(row["confidence"])
                self.assertFalse(row["canonical_merge"])
                self.assertEqual(row["target_cross_source_multiplicity_max"], 3)
                self.assertEqual(row["target_multiplicity_by_source_max"], {
                    "littlesis": 1,
                    "politico-pro-staffer-directory": 1,
                    "quorum-legacy-staffers": 1,
                })
            littlesis = next(
                row for row in rows
                if row["matched_source_system_id"] == "littlesis"
            )
            self.assertEqual(
                littlesis["match_evidence"][0]["target_name_evidence"],
                [{"basis": "ALIAS", "raw_target_name": "Alex Example"}],
            )
            output_twin = root / "out-twin"
            LIC.build_candidates(
                lda, politico, native, output_twin,
                generated_at="2026-08-30T18:30:00Z",
            )
            for filename in (
                "lda_identity_candidates.ndjson.gz",
                "lda_identity_candidate_receipt.json",
            ):
                self.assertEqual(
                    (output / filename).read_bytes(),
                    (output_twin / filename).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()

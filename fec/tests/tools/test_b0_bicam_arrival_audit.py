# -*- coding: utf-8 -*-
"""Contract tests for B0 BICAM acquisition and arrival auditing."""

from __future__ import annotations

import contextlib
import copy
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fec.tools import b0_bicam_arrival_audit as b0


class B0BicamArrivalAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.directory = Path(self.scratch.name)
        self.snapshot = self.directory / "snapshot"
        self.snapshot.mkdir()
        self.manifest_path = self.directory / "sources.json"
        self.manifest = self._build_snapshot(self.snapshot, self.manifest_path)
        self.network_bomb = mock.patch.object(
            b0.urllib.request,
            "build_opener",
            side_effect=AssertionError("network path entered"),
        )
        self.network_bomb.start()
        self.addCleanup(self.network_bomb.stop)

    @staticmethod
    def _write_csv(path: Path, columns: list[str], rows: list[list[object]], compressed: bool = False) -> None:
        if compressed:
            text_stream = io.StringIO(newline="")
            writer = csv.writer(text_stream)
            writer.writerow(columns)
            writer.writerows(rows)
            text = text_stream.getvalue()
            with gzip.GzipFile(str(path), "wb", mtime=0) as stream:
                stream.write(text.encode("utf-8"))
        else:
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerows(rows)

    @classmethod
    def _build_snapshot(
        cls,
        snapshot: Path,
        manifest_path: Path,
        *,
        schema: str = b0.SOURCE_SCHEMA,
        amendment_rows: list[list[object]] | None = None,
        amendments_texts_rows: list[list[object]] | None = None,
    ) -> dict[str, object]:
        definitions = {
            "bills": (["bill_id", "congress", "bill_type", "bill_number"], [["b1", "119", "hr", "1"]], {
                "bill_id": "bill_id", "congress": "congress",
                "bill_type": "bill_type", "bill_number": "bill_number",
            }),
            "bills_texts": (["bill_id"], [["b1"]], {"bill_id": "bill_id"}),
            "bills_sponsored": (["bill_id", "member_id", "relationship"], [["b1", "m1", "cosponsor"]], {
                "bill_id": "bill_id", "member_id": "member_id", "relationship": "relationship",
            }),
            "members": (["member_id", "bioguide_id"], [["m1", "B000001"]], {
                "member_id": "member_id", "bioguide_id": "bioguide_id",
            }),
            "committees": (["committee_id"], [], {}),
            "hearings": (["hearing_id"], [], {}),
            "committee_reports": (["report_id"], [], {}),
        }
        if schema == b0.SOURCE_SCHEMA_V2:
            definitions["amendments"] = (
                ["amendment_id", "amendment_type", "amendment_number", "congress"],
                amendment_rows if amendment_rows is not None else [
                    ["samdt1542-118", "samdt", "1542", "118"],
                ],
                {
                    "amendment_id": "amendment_id",
                    "congress": "congress",
                    "amendment_type": "amendment_type",
                    "amendment_number": "amendment_number",
                },
            )
            definitions["amendments_texts"] = (
                ["amendment_id"],
                amendments_texts_rows if amendments_texts_rows is not None else [
                    ["samdt1542-118"],
                ],
                {"amendment_id": "amendment_id"},
            )
        components = {}
        for index, name in enumerate(b0.SOURCE_COMPONENTS[schema]):
            columns, rows, mapping = definitions[name]
            compressed = name == "hearings"
            if name == "amendments_texts":
                filename = "amendments_texts_ids.csv"
            else:
                filename = f"{name}.csv.gz" if compressed else f"{name}.csv"
            cls._write_csv(snapshot / filename, columns, rows, compressed)
            component = {
                "url": f"https://invalid.example/{filename}",
                "filename": filename,
                "format": "csv.gz" if compressed else "csv",
                "expected_sha256": "",
                "columns": mapping,
            }
            if name == "bills_sponsored":
                component["cosponsor_values"] = ["cosponsor"]
            components[name] = component
        manifest = {
            "schema": schema,
            "snapshot_id": "fixture-snapshot",
            "components": components,
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    @staticmethod
    def _write_ndjson(path: Path, records: list[dict[str, object]]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _quiet_main(self, arguments: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return b0.main(arguments)

    def test_01_no_action_is_no_network_no_write(self) -> None:
        absent = self.directory / "absent-snapshot"
        self.assertEqual(self._quiet_main(["--snapshot-dir", str(absent)]), 0)
        self.assertFalse(absent.exists())
        self.assertEqual(b0.urllib.request.build_opener.call_count, 0)

    def test_02_audit_never_reaches_downloader(self) -> None:
        # An empty legfix5 run and discovery file, so the audit still fails at the missing
        # billstatus input without first walking the live repo's whole legfix5 receipts tree
        # (the --legfix5-* defaults), which took this suite past 300 s.
        legfix = self.directory / "legfix-run"
        (legfix / "receipts").mkdir(parents=True)
        (legfix / "accepted_html").mkdir()
        discovery = self.directory / "legfix-discovery.ndjson"
        discovery.write_text("", encoding="utf-8")
        with mock.patch.object(b0, "atomic_download", side_effect=AssertionError("download called")):
            code = self._quiet_main([
                "--snapshot-dir", str(self.snapshot), "--source-manifest", str(self.manifest_path),
                "--audit", "--billstatus-parsed", str(self.directory / "missing.ndjson"),
                "--legfix5-run", str(legfix), "--legfix5-fulltext", str(discovery),
            ])
        self.assertEqual(code, 4)

    def test_03_fetch_gate_precedes_filesystem_and_url(self) -> None:
        absent = self.directory / "fetch-target"
        with mock.patch.dict(os.environ, {}, clear=True):
            code = self._quiet_main([
                "--snapshot-dir", str(absent), "--source-manifest", str(self.manifest_path),
                "--fetch", "--allow-host", "invalid.example",
                "--billstatus-parsed", str(self.manifest_path),
            ])
        self.assertEqual(code, 2)
        self.assertFalse(absent.exists())
        self.assertEqual(b0.urllib.request.build_opener.call_count, 0)

    def test_04_manifest_requires_exact_components_and_mappings(self) -> None:
        value, _ = b0.validate_source_manifest(self.manifest_path)
        self.assertEqual(tuple(value["components"]), b0.COMPONENTS)
        malformed = copy.deepcopy(self.manifest)
        del malformed["components"]["hearings"]
        path = self.directory / "missing-component.json"
        path.write_text(json.dumps(malformed), encoding="utf-8")
        with self.assertRaises(b0.UsageError):
            b0.validate_source_manifest(path)
        malformed = copy.deepcopy(self.manifest)
        del malformed["components"]["bills"]["columns"]["bill_type"]
        path.write_text(json.dumps(malformed), encoding="utf-8")
        with self.assertRaises(b0.UsageError):
            b0.validate_source_manifest(path)

    def test_05_source_url_and_path_gates_fail_closed(self) -> None:
        mutations = [
            ("url", "http://invalid.example/x.csv", []),
            ("url", "https://user@invalid.example/x.csv", []),
            ("url", "https://invalid.example/x.csv?api_key=x", []),
            ("url", "https://invalid.example/x.csv", ["different.example"]),
            ("filename", "../x.csv", []),
            ("format", "parquet", []),
        ]
        for field, value, hosts in mutations:
            with self.subTest(field=field, value=value):
                malformed = copy.deepcopy(self.manifest)
                malformed["components"]["bills"][field] = value
                path = self.directory / "bad-source.json"
                path.write_text(json.dumps(malformed), encoding="utf-8")
                with self.assertRaises(b0.UsageError):
                    b0.validate_source_manifest(
                        path, fetch=bool(hosts), allowed_hosts=hosts
                    )

    def test_06_csv_and_gzip_receipts_are_exact(self) -> None:
        for name in ("bills", "hearings"):
            specification = self.manifest["components"][name]
            path = self.snapshot / specification["filename"]
            receipt = b0.inspect_component(path, specification["format"], specification["columns"])
            self.assertEqual(receipt["bytes"], path.stat().st_size)
            self.assertEqual(receipt["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(receipt["row_count"], 1 if name == "bills" else 0)

    def test_07_arrival_manifest_schema_and_order_deterministic(self) -> None:
        manifest, source_hash = b0.validate_source_manifest(self.manifest_path)
        first = b0.build_arrival_manifest(self.snapshot, manifest, source_hash)
        second = b0.build_arrival_manifest(self.snapshot, manifest, source_hash)
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], b0.ARRIVAL_SCHEMA)
        self.assertEqual(first["component_order"], list(b0.COMPONENTS))
        self.assertEqual(list(first["components"]), list(b0.COMPONENTS))

    def _roster_files(self, count: int = 533, duplicate: bool = False) -> tuple[Path, Path, Path, list[dict[str, str]]]:
        identifiers = [f"B{index:06d}" for index in range(count)]
        roster = self.directory / f"roster-{count}-{duplicate}.json"
        values = [{"bioguide": identifier} for identifier in identifiers]
        if duplicate:
            values.append({"bioguide": identifiers[0]})
        roster.write_text(json.dumps(values), encoding="utf-8")
        legislators = self.directory / f"legislators-{count}.csv"
        voteview = self.directory / f"voteview-{count}.csv"
        self._write_csv(legislators, ["bioguide_id"], [[value] for value in identifiers])
        self._write_csv(voteview, ["bioguide"], [[value] for value in identifiers])
        members = [{"member_id": f"m{index}", "bioguide_id": value} for index, value in enumerate(identifiers)]
        return roster, legislators, voteview, members

    def test_08_roster_bar_passes_533_and_names_misses_duplicates(self) -> None:
        roster, legislators, voteview, members = self._roster_files()
        result = b0.audit_roster(roster, legislators, voteview, members)
        self.assertTrue(result["pass"])
        self.assertEqual(result["joined"], 533)
        roster, legislators, voteview, members = self._roster_files(533, True)
        members = members[:-1]
        result = b0.audit_roster(roster, legislators, voteview, members)
        self.assertFalse(result["pass"])
        self.assertEqual(result["roster_duplicates"], ["B000000"])
        self.assertEqual(result["missing_from_bicam"], ["B000532"])

    @staticmethod
    def _act_records(count: int = 648) -> list[dict[str, object]]:
        return [{"bill": f"H.R.{index}", "congress": 119} for index in range(1, count + 1)]

    @staticmethod
    def _bill_rows(count: int) -> list[dict[str, str]]:
        return [
            {"bill_id": f"b{index}", "congress": "119", "bill_type": "hr", "bill_number": str(index)}
            for index in range(1, count + 1)
        ]

    def test_09_act_bar_uses_pre_119_bills_not_legacy_composite(self) -> None:
        pre_119_records = [
            {"bill": f"H.R.{index}", "congress": 118}
            for index in range(1, 549)
        ]
        congress_119_records = [
            {"bill": f"H.R.{index}", "congress": 119}
            for index in range(1, 101)
        ]
        pre_119_bills = [
            {
                "bill_id": f"b118-{index}",
                "congress": "118",
                "bill_type": "hr",
                "bill_number": str(index),
            }
            for index in range(1, 549)
        ]
        result, _ = b0.audit_act_universe(
            pre_119_records + congress_119_records, pre_119_bills
        )
        self.assertTrue(result["pass"])
        self.assertEqual((result["joined"], len(result["misses"])), (548, 100))
        self.assertTrue(all(miss["reason"] == "NO_BICAM_MATCH" for miss in result["misses"]))
        self.assertEqual(result["pre_119_bill_stratum"]["hit_rate"], 1.0)
        self.assertFalse(result["armed_strata"]["119"]["armed"])
        self.assertFalse(result["legacy_composite"]["pass"])

    def test_10_typed_bill_normalization_preserves_distinctions(self) -> None:
        keys = {
            b0.normalize_bicam_bill_type("hr", 1),
            b0.normalize_bicam_bill_type("hres", 1),
            b0.normalize_bicam_bill_type("hjres", 1),
            b0.normalize_bicam_bill_type("hconres", 1),
        }
        self.assertEqual(len(keys), 4)
        self.assertEqual(b0.normalize_repo_act({"bill": "H.Res.1", "congress": 119}), (119, "H", "RES", 1))

    def test_11_text_presence_enforces_denominator_and_threshold(self) -> None:
        discovery = [
            {"canonical_key": f"118:hr:{index + 1}", "bill_id": f"b{index}"}
            for index in range(18557)
        ]
        bicam = {f"b{index}" for index in range(18557)}
        legfix = {f"118:hr:{index + 1}" for index in range(17630)}
        result = b0.audit_text_presence(discovery, bicam, legfix)
        self.assertTrue(result["pass"])
        self.assertEqual(result["denominator"], 18557)
        self.assertGreaterEqual(
            result["strata"]["pre_119_bills"]["agreement_ratio"], 0.95
        )
        legfix = {f"118:hr:{index + 1}" for index in range(17629)}
        self.assertFalse(b0.audit_text_presence(discovery, bicam, legfix)["pass"])

    def test_12_disagreement_selection_is_deterministic_and_capped_at_50(self) -> None:
        discovery = [{"canonical_key": f"k{index}", "bill_id": f"b{index}"} for index in range(18557)]
        first = b0.audit_text_presence(discovery, {f"b{index}" for index in range(100)}, set())
        second = b0.audit_text_presence(discovery, {f"b{index}" for index in range(100)}, set())
        self.assertEqual(first["selected_disagreements"], second["selected_disagreements"])
        self.assertEqual(len(first["selected_disagreements"]), 50)
        self.assertTrue(all(row["classification"] == "BICAM_ONLY" for row in first["selected_disagreements"]))

    @staticmethod
    def _cosponsor_maps(count: int = 100) -> tuple[list[str], dict[str, set[str]], dict[str, set[str]]]:
        keys = [f"119|H|R|{index}" for index in range(1, count + 1)]
        repository = {key: {f"B{index:06d}"} for index, key in enumerate(keys)}
        bicam = copy.deepcopy(repository)
        return keys, repository, bicam

    def test_13_cosponsor_sample_and_sha_are_exactly_deterministic(self) -> None:
        keys, repository, bicam = self._cosponsor_maps(120)
        first = b0.audit_cosponsor_agreement(keys, repository, bicam)
        second = b0.audit_cosponsor_agreement(reversed(keys), repository, bicam)
        self.assertTrue(first["pass"])
        self.assertEqual(len(first["sample"]), 100)
        self.assertEqual(first["sample"], second["sample"])
        self.assertEqual(first["sample_sha256"], second["sample_sha256"])

    def test_14_cosponsor_aggregate_two_percent_bar(self) -> None:
        keys, repository, bicam = self._cosponsor_maps()
        repository = {key: {f"x{n}" for n in range(10)} for key in keys}
        bicam = copy.deepcopy(repository)
        bicam[keys[0]] = set()
        bicam[keys[1]] = set()
        self.assertTrue(b0.audit_cosponsor_agreement(keys, repository, bicam)["pass"])
        bicam[keys[2]] = set()
        result = b0.audit_cosponsor_agreement(keys, repository, bicam)
        self.assertFalse(result["pass"])
        self.assertEqual(len(result["per_act_differences"]), 3)

    def test_15_bad_legfix_receipts_or_bodies_do_not_count(self) -> None:
        run = self.directory / "legfix"
        body_dir = run / "accepted_html" / "shared"
        body_dir.mkdir(parents=True)
        receipts_dir = run / "receipts"
        receipts_dir.mkdir()
        path = body_dir / "page.html.gz"
        with gzip.GzipFile(str(path), "wb", mtime=0) as stream:
            stream.write(b"body")
        records = [
            {"canonical_key": "valid", "status": "accepted", "accepted_html": "accepted_html/shared/page.html.gz"},
            {"canonical_key": "missing", "status": "accepted", "accepted_html": "accepted_html/no/page.html.gz"},
            {"canonical_key": "rejected", "status": "rejected", "accepted_html": "accepted_html/shared/page.html.gz"},
        ]
        self._write_ndjson(receipts_dir / "receipts.ndjson", records)
        presence, errors, _ = b0.load_legfix5_presence(run)
        self.assertEqual(presence, {"valid"})
        self.assertEqual({row["canonical_key"] for row in errors}, {"missing", "rejected"})

    def _build_integration_inputs(
        self,
        *,
        schema: str = b0.SOURCE_SCHEMA,
        amendment_rows: list[list[object]] | None = None,
        amendments_texts_rows: list[list[object]] | None = None,
    ) -> dict[str, Path]:
        integration = self.directory / "integration"
        integration.mkdir()
        snapshot = integration / "snapshot"
        snapshot.mkdir()
        manifest_path = integration / "sources.json"
        self._build_snapshot(
            snapshot,
            manifest_path,
            schema=schema,
            amendment_rows=amendment_rows,
            amendments_texts_rows=amendments_texts_rows,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bills = [
            {
                "bill_id": f"b{index}",
                "congress": "118",
                "bill_type": "hr",
                "bill_number": str(index),
            }
            for index in range(1, 649)
        ]
        self._write_csv(snapshot / "bills.csv", ["bill_id", "congress", "bill_type", "bill_number"], [list(row.values()) for row in bills])
        self._write_csv(snapshot / "bills_texts.csv", ["bill_id"], [[f"b{index}"] for index in range(1, 649)])
        identifiers = [f"B{index:06d}" for index in range(533)]
        self._write_csv(snapshot / "members.csv", ["member_id", "bioguide_id"], [[f"m{index}", identifier] for index, identifier in enumerate(identifiers)])
        self._write_csv(snapshot / "bills_sponsored.csv", ["bill_id", "member_id", "relationship"], [[f"b{index}", "m0", "cosponsor"] for index in range(1, 649)])
        roster = integration / "congestion.json"
        roster.write_text(json.dumps([{"bioguide": value} for value in identifiers]), encoding="utf-8")
        legislators = integration / "legislators.csv"
        voteview = integration / "voteview.csv"
        self._write_csv(legislators, ["bioguide_id"], [[value] for value in identifiers])
        self._write_csv(voteview, ["bioguide"], [[value] for value in identifiers])
        tags = integration / "tags.ndjson"
        self._write_ndjson(tags, [
            {"bill": f"H.R.{index}", "congress": 118}
            for index in range(1, 649)
        ])
        discovery = integration / "discovery.ndjson"
        discovery_records = [
            {
                "canonical_key": f"118:hr:{index + 1}",
                "bill_id": f"b{(index % 648) + 1}",
            }
            for index in range(18000)
        ] + [
            {
                "canonical_key": f"119:hr:{index + 1}",
                "bill_id": f"b{((18000 + index) % 648) + 1}",
            }
            for index in range(557)
        ]
        self._write_ndjson(discovery, discovery_records)
        legfix = integration / "legfix"
        body_dir = legfix / "accepted_html" / "shared"
        body_dir.mkdir(parents=True)
        receipts_dir = legfix / "receipts"
        receipts_dir.mkdir()
        path = body_dir / "page.html.gz"
        with gzip.GzipFile(str(path), "wb", mtime=0) as stream:
            stream.write(b"body")
        receipts = [
            {"canonical_key": record["canonical_key"], "status": "accepted", "accepted_html": "accepted_html/shared/page.html.gz"}
            for record in discovery_records
        ]
        self._write_ndjson(receipts_dir / "receipts.ndjson", receipts)
        billstatus = integration / "billstatus.ndjson"
        self._write_ndjson(billstatus, [
            {"bill": f"H.R.{index}", "congress": 118, "cosponsors": ["B000000"]}
            for index in range(1, 649)
        ])
        return {
            "root": integration, "snapshot": snapshot, "manifest": manifest_path,
            "roster": roster, "legislators": legislators, "voteview": voteview,
            "tags": tags, "discovery": discovery, "legfix": legfix,
            "billstatus": billstatus,
        }

    def test_16_integration_exact_cardinalities_hashes_and_no_source_urls(self) -> None:
        paths = self._build_integration_inputs()
        out_dir = paths["root"] / "outputs"
        code = self._quiet_main([
            "--snapshot-dir", str(paths["snapshot"]), "--source-manifest", str(paths["manifest"]),
            "--audit", "--repo-root", str(paths["root"]), "--roster", str(paths["roster"]),
            "--legislators", str(paths["legislators"]), "--voteview", str(paths["voteview"]),
            "--tags", str(paths["tags"]), "--legfix5-run", str(paths["legfix"]),
            "--legfix5-fulltext", str(paths["discovery"]),
            "--billstatus-parsed", str(paths["billstatus"]), "--out-dir", str(out_dir),
        ])
        self.assertEqual(code, 0)
        report = json.loads((out_dir / b0.AUDIT_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["roster_join"]["roster_distinct"], 533)
        self.assertEqual(report["act_universe_join"]["unique_bill_congress_rows"], 648)
        self.assertEqual(report["text_presence"]["denominator"], 18557)
        serialized = json.dumps(report).casefold()
        self.assertNotIn("https://", serialized)
        self.assertNotIn("invalid.example", serialized)
        self.assertTrue(all("sha256" in json.dumps(receipt) for receipt in report["inputs"].values()))

    def test_17_replace_gate_and_atomic_failure_preserve_prior(self) -> None:
        out_dir = self.directory / "audit"
        out_dir.mkdir()
        arrival_path, audit_path = b0.output_paths(out_dir)
        arrival_path.write_bytes(b"old-arrival\n")
        audit_path.write_bytes(b"old-audit\n")
        with self.assertRaises(b0.UsageError):
            b0.write_audit_outputs(out_dir, {"x": 1}, {"y": 2}, False)
        with mock.patch.object(b0.os, "replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                b0.write_audit_outputs(out_dir, {"x": 1}, {"y": 2}, True)
        self.assertEqual(arrival_path.read_bytes(), b"old-arrival\n")
        self.assertEqual(audit_path.read_bytes(), b"old-audit\n")
        self.assertEqual([path for path in out_dir.iterdir() if path.name.startswith(".")], [])

    def test_18_source_text_locks_fetch_gates_and_tests_never_fetch(self) -> None:
        source = Path(b0.__file__).read_text(encoding="utf-8")
        self.assertIn('if not args.fetch and not args.audit:', source)
        self.assertIn('if args.fetch:', source)
        self.assertIn('os.environ.get("BICAM_FETCH_OK") != "1"', source)
        self.assertIn("urllib.request.build_opener", source)
        fetch_needle = '"--' + 'fetch"'
        self.assertNotIn(fetch_needle, Path(__file__).read_text(encoding="utf-8").split("def test_18", 1)[1])
        self.assertEqual(b0.urllib.request.build_opener.call_count, 0)

    def test_19_no_barred_output_or_source_targets(self) -> None:
        source = Path(b0.__file__).read_text(encoding="utf-8").casefold()
        barred = (
            ".xlsx", "collector_supervisor.py", "apply_", "external mentat",
            "--look", "per_topic_flip",
        )
        self.assertTrue(all(term not in source for term in barred))
        self.assertEqual(b0.output_paths(Path("audit")), (
            Path("audit") / b0.ARRIVAL_FILENAME,
            Path("audit") / b0.AUDIT_FILENAME,
        ))

    def test_20_csv_field_limit_accepts_oversized_fields(self) -> None:
        oversized = "B" * ((1 << 17) + 1)
        path = self.directory / "oversized.csv"
        self._write_csv(path, ["bioguide_id"], [[oversized]])
        self.assertEqual(b0.read_csv_identifiers(path), {oversized})

    def test_21_scalar_json_records_are_skipped_counted_and_reported(self) -> None:
        path = self.directory / "mixed.json"
        path.write_text(
            json.dumps([*self._act_records(), "scalar"]),
            encoding="utf-8",
        )
        stats: dict[str, int] = {}
        records = list(b0.iter_json_records(path, stats))
        self.assertEqual(len(records), 648)
        self.assertEqual(stats["skipped_nonrecord"], 1)
        report, _ = b0.audit_act_universe(
            records, self._bill_rows(640), stats["skipped_nonrecord"]
        )
        self.assertEqual(report["skipped_nonrecord"], 1)
        self.assertFalse(report["pass"])

    def test_22_legfix_defaults_and_expected_child_guard_are_explicit(self) -> None:
        root = Path(b0.ROOT)
        parent = (
            root / "audits" / "legfix5" / "congress_advanced_pages_20260725"
        )
        args = b0.build_parser().parse_args(["--snapshot-dir", "snapshot"])
        self.assertEqual(
            args.legfix5_run,
            parent / "text_relevance_httr_full_20260727z",
        )
        self.assertEqual(
            args.legfix5_fulltext,
            parent / "congress_advanced_records.ndjson",
        )

        incomplete = self.directory / "incomplete-legfix"
        incomplete.mkdir()
        with self.assertRaises(b0.IntegrityError) as caught:
            b0.load_legfix5_presence(incomplete)
        message = str(caught.exception)
        self.assertIn("receipts/", message)
        self.assertIn("accepted_html/", message)

    def test_23_truncated_receipt_is_ledgered_by_path_and_not_fatal(self) -> None:
        run = self.directory / "truncated-legfix"
        receipt_dir = run / "receipts" / "119" / "hr" / "42"
        receipt_dir.mkdir(parents=True)
        (run / "accepted_html").mkdir()
        (receipt_dir / "receipt.json").write_text(
            '{"canonical_key":"119:hr:42"', encoding="utf-8"
        )
        presence, errors, count = b0.load_legfix5_presence(run)
        self.assertEqual(presence, set())
        self.assertEqual(count, 0)
        self.assertEqual(errors, [{
            "canonical_key": "119:hr:42",
            "reason": "UNREADABLE_OR_UNPARSEABLE_RECEIPT",
        }])
        discovery = [
            {"canonical_key": f"118:hr:{index + 1}", "bill_id": f"b{index}"}
            for index in range(18557)
        ]
        bicam = {f"b{index}" for index in range(18557)}
        legfix = {f"118:hr:{index + 1}" for index in range(18557)}
        self.assertTrue(
            b0.audit_text_presence(discovery, bicam, legfix)["pass"]
        )
        self.assertFalse(
            b0.audit_text_presence(discovery, bicam, legfix, errors)["pass"]
        )

    def test_24_act_strata_separate_non_bills_from_malformed_rows(self) -> None:
        records = [
            {"bill": "H.R.1", "congress": 119},
            {"bill": "LTR-example", "congress": 118},
            {"bill": "H.Amdt.5", "congress": 118},
            {"bill": "not an act", "congress": "bad"},
        ]
        report, _ = b0.audit_act_universe(records, self._bill_rows(1))
        self.assertEqual(
            report["strata"],
            {"118": {"n": 2, "hit": 0}, "119": {"n": 1, "hit": 1}},
        )
        reasons = [miss["reason"] for miss in report["misses"]]
        self.assertEqual(reasons.count("NON_BILL"), 2)
        self.assertEqual(reasons.count("MALFORMED"), 1)

    def test_25_parse_congress_rejects_zero_and_negative_values(self) -> None:
        for value in (0, -1, "0", "-17", "0.0", "-2.0"):
            with self.subTest(value=value):
                self.assertIsNone(b0._parse_congress(value))

    def test_26_canonical_duplicate_detection_catches_spelling_variants(self) -> None:
        records = [
            {"bill": "H.R.1", "congress": 119},
            {"bill": "HR 1", "congress": "119.0"},
        ]
        report, _ = b0.audit_act_universe(records, self._bill_rows(1))
        self.assertEqual(report["duplicate_bill_congress_rows"], [])
        self.assertEqual(report["duplicate_canonical_keys"], ["119|H|R|1"])
        self.assertFalse(report["pass"])

    def test_27_equal_count_cosponsor_identity_mismatch_fails(self) -> None:
        key = "119|H|R|1"
        result = b0.audit_cosponsor_agreement(
            [key], {key: {"B000001"}}, {key: {"B000002"}}, sample_size=1
        )
        self.assertFalse(result["pass"])
        self.assertEqual(result["identity_mismatch_ratio"], 2.0)
        self.assertEqual(result["symmetric_difference_total"], 2)
        self.assertEqual(
            result["per_act_symmetric_differences"],
            [{"canonical_act_key": key, "symmetric_difference_count": 2}],
        )

    def test_28_skipped_nonrecords_veto_text_and_cosponsor_passes(self) -> None:
        discovery = [
            {"canonical_key": f"118:hr:{index + 1}", "bill_id": f"b{index}"}
            for index in range(18557)
        ]
        bicam_text = {f"b{index}" for index in range(18557)}
        legfix = {f"118:hr:{index + 1}" for index in range(18557)}
        self.assertTrue(
            b0.audit_text_presence(discovery, bicam_text, legfix)["pass"]
        )
        self.assertFalse(
            b0.audit_text_presence(
                discovery, bicam_text, legfix, skipped_nonrecord=1
            )["pass"]
        )
        self.assertFalse(
            b0.audit_text_presence(
                discovery, bicam_text, legfix, legfix5_skipped_nonrecord=1
            )["pass"]
        )

        keys, repository, bicam_cosponsors = self._cosponsor_maps()
        self.assertTrue(
            b0.audit_cosponsor_agreement(
                keys, repository, bicam_cosponsors
            )["pass"]
        )
        self.assertFalse(
            b0.audit_cosponsor_agreement(
                keys,
                repository,
                bicam_cosponsors,
                skipped_nonrecord=1,
            )["pass"]
        )

    def test_29_armed_119_threshold_exact_and_just_below(self) -> None:
        pre_119_records = [
            {"bill": f"H.R.{index}", "congress": 118}
            for index in range(1, 549)
        ]
        congress_119_records = [
            {"bill": f"H.R.{index}", "congress": 119}
            for index in range(1, 101)
        ]
        pre_119_bills = [
            {
                "bill_id": f"b118-{index}",
                "congress": "118",
                "bill_type": "hr",
                "bill_number": str(index),
            }
            for index in range(1, 549)
        ]
        def audit_with_119_hits(hit_count: int) -> dict[str, object]:
            congress_119_bills = [
                {
                    "bill_id": f"b119-{index}",
                    "congress": "119",
                    "bill_type": "hr",
                    "bill_number": str(index),
                }
                for index in range(1, hit_count + 1)
            ] + [
                {
                    "bill_id": f"b119-extra-{index}",
                    "congress": "119",
                    "bill_type": "hr",
                    "bill_number": str(index),
                }
                for index in range(10000, 10000 + 5000 - hit_count)
            ]
            self.assertEqual(len(congress_119_bills), 5000)
            result, _ = b0.audit_act_universe(
                pre_119_records + congress_119_records,
                pre_119_bills + congress_119_bills,
            )
            return result

        exact = audit_with_119_hits(95)
        below = audit_with_119_hits(94)
        self.assertTrue(exact["armed_strata"]["119"]["armed"])
        self.assertEqual(
            exact["armed_strata"]["119"]["condition"],
            "bills.csv contains >= 5000 congress-119 rows",
        )
        self.assertEqual(
            exact["armed_strata"]["119"]["congress_119_snapshot_rows"],
            5000,
        )
        self.assertEqual(exact["armed_strata"]["119"]["hit_rate"], 0.95)
        self.assertTrue(exact["pass"])
        self.assertEqual(below["armed_strata"]["119"]["hit_rate"], 0.94)
        self.assertFalse(below["pass"])

    def test_30_text_bar_is_pre_119_and_receipt_errors_still_veto(self) -> None:
        pre_119 = [
            {"canonical_key": f"118:hr:{index}", "bill_id": f"pre-{index}"}
            for index in range(1, 18001)
        ]
        congress_119 = [
            {"canonical_key": f"119:hr:{index}", "bill_id": f"current-{index}"}
            for index in range(1, 548)
        ]
        amendments = [
            {"canonical_key": f"118:hamdt:{index}", "bill_id": f"amend-{index}"}
            for index in range(1, 11)
        ]
        discovery = pre_119 + congress_119 + amendments
        bicam = {
            record["bill_id"] for record in pre_119 + congress_119
        }
        legfix = {
            record["canonical_key"] for record in pre_119[:17100] + amendments
        }
        result = b0.audit_text_presence(discovery, bicam, legfix)
        self.assertTrue(result["pass"])
        self.assertEqual(
            result["strata"]["pre_119_bills"]["agreement_ratio"], 0.95
        )
        self.assertLess(result["legacy_overall"]["agreement_ratio"], 0.95)
        self.assertEqual(result["strata"]["119"], {
            "label": "report_only",
            "size": 547,
            "LEGFIX5_ONLY": 0,
        })
        self.assertEqual(result["strata"]["amendments"], {
            "label": "report_only",
            "size": 10,
            "LEGFIX5_ONLY": 10,
        })
        errors = [{"canonical_key": "118:hr:1", "reason": "fixture error"}]
        vetoed = b0.audit_text_presence(discovery, bicam, legfix, errors)
        self.assertEqual(vetoed["legfix5_errors"], errors)
        self.assertFalse(vetoed["pass"])

    def test_31_malformed_miss_vetoes_otherwise_passing_act_bar(self) -> None:
        records = [
            {"bill": f"H.R.{index}", "congress": 118}
            for index in range(1, 648)
        ] + [{"bill": "not an act", "congress": 118}]
        bills = [
            {
                "bill_id": f"b118-{index}",
                "congress": "118",
                "bill_type": "hr",
                "bill_number": str(index),
            }
            for index in range(1, 648)
        ]
        result, _ = b0.audit_act_universe(records, bills)
        self.assertGreaterEqual(
            result["pre_119_bill_stratum"]["hit_rate"], 0.987
        )
        self.assertEqual(result["pre_119_bill_stratum"]["denominator"], 648)
        self.assertEqual(result["pre_119_bill_stratum"]["hit"], 647)
        self.assertEqual(result["malformed_misses"], 1)
        self.assertTrue(result["legacy_composite"]["pass"])
        self.assertFalse(result["pass"])

    def test_32_report_identifies_d011_and_amendments_stay_placeholder(self) -> None:
        paths = self._build_integration_inputs()
        out_dir = paths["root"] / "d011-outputs"
        code = self._quiet_main([
            "--snapshot-dir", str(paths["snapshot"]),
            "--source-manifest", str(paths["manifest"]),
            "--audit", "--repo-root", str(paths["root"]),
            "--roster", str(paths["roster"]),
            "--legislators", str(paths["legislators"]),
            "--voteview", str(paths["voteview"]),
            "--tags", str(paths["tags"]),
            "--legfix5-run", str(paths["legfix"]),
            "--legfix5-fulltext", str(paths["discovery"]),
            "--billstatus-parsed", str(paths["billstatus"]),
            "--out-dir", str(out_dir),
        ])
        self.assertEqual(code, 0)
        report = json.loads(
            (out_dir / b0.AUDIT_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(report["bars_schema"], "d011-stratum-v1")
        self.assertEqual(
            report["bars"]["text_presence"]["bar"],
            ">=0.95 pre-119th bill discovery-key agreement/18000",
        )
        self.assertNotIn("/18557", report["bars"]["text_presence"]["bar"])
        self.assertEqual(
            report["act_universe_join"]["armed_strata"]["amendments"],
            {
                "armed": False,
                "condition": "amendments component not declared in manifest",
                "hit_rate": None,
            },
        )

    def test_33_empty_mandatory_strata_fail_closed(self) -> None:
        congress_119_records = [
            {"bill": f"H.R.{index}", "congress": 119}
            for index in range(1, 649)
        ]
        congress_119_bills = self._bill_rows(648)
        empty_pre_119, _ = b0.audit_act_universe(
            congress_119_records, congress_119_bills
        )
        self.assertEqual(empty_pre_119["pre_119_bill_stratum"]["denominator"], 0)
        self.assertEqual(empty_pre_119["pre_119_bill_stratum"]["hit_rate"], 0.0)
        self.assertFalse(empty_pre_119["pass"])

        pre_119_records = [
            {"bill": f"H.R.{index}", "congress": 118}
            for index in range(1, 649)
        ]
        pre_119_bills = [
            {
                "bill_id": f"b118-{index}",
                "congress": "118",
                "bill_type": "hr",
                "bill_number": str(index),
            }
            for index in range(1, 649)
        ]
        snapshot_only_119_bills = [
            {
                "bill_id": f"b119-snapshot-{index}",
                "congress": "119",
                "bill_type": "hr",
                "bill_number": str(index),
            }
            for index in range(10000, 15000)
        ]
        empty_armed_119, _ = b0.audit_act_universe(
            pre_119_records, pre_119_bills + snapshot_only_119_bills
        )
        self.assertTrue(empty_armed_119["armed_strata"]["119"]["armed"])
        self.assertNotIn("119", empty_armed_119["bill_strata"])
        self.assertEqual(
            empty_armed_119["armed_strata"]["119"]["congress_119_snapshot_rows"],
            5000,
        )
        self.assertEqual(empty_armed_119["armed_strata"]["119"]["hit_rate"], 0.0)
        self.assertFalse(empty_armed_119["pass"])

        discovery = [
            {"canonical_key": f"119:hr:{index + 1}", "bill_id": f"b{index}"}
            for index in range(18557)
        ]
        bicam = {f"b{index}" for index in range(18557)}
        legfix = {f"119:hr:{index + 1}" for index in range(18557)}
        empty_text = b0.audit_text_presence(discovery, bicam, legfix)
        self.assertEqual(empty_text["strata"]["pre_119_bills"]["size"], 0)
        self.assertEqual(
            empty_text["strata"]["pre_119_bills"]["agreement_ratio"], 0.0
        )
        self.assertFalse(empty_text["pass"])

    def test_34_pre_119_act_threshold_exact_and_just_below(self) -> None:
        records = [
            {"bill": f"H.R.{index}", "congress": 118}
            for index in range(1, 1001)
        ]

        def bill_rows(hit_count: int) -> list[dict[str, str]]:
            return [
                {
                    "bill_id": f"b118-{index}",
                    "congress": "118",
                    "bill_type": "hr",
                    "bill_number": str(index),
                }
                for index in range(1, hit_count + 1)
            ]

        with mock.patch.object(b0, "EXPECTED_ACTS", 1000):
            exact, _ = b0.audit_act_universe(records, bill_rows(987))
            below, _ = b0.audit_act_universe(records, bill_rows(986))
        self.assertEqual(exact["pre_119_bill_stratum"]["hit_rate"], 0.987)
        self.assertTrue(exact["pass"])
        self.assertEqual(below["pre_119_bill_stratum"]["hit_rate"], 0.986)
        self.assertFalse(below["pass"])

    def test_35_v2_manifest_accepts_exact_appended_amendments_components(self) -> None:
        manifest_path = self.directory / "sources-v2.json"
        self._build_snapshot(
            self.snapshot, manifest_path, schema=b0.SOURCE_SCHEMA_V2
        )
        manifest, source_hash = b0.validate_source_manifest(manifest_path)
        self.assertEqual(tuple(manifest["components"]), b0.V2_COMPONENTS)
        self.assertEqual(
            tuple(manifest["components"]["amendments"]["columns"]),
            ("amendment_id", "congress", "amendment_type", "amendment_number"),
        )
        self.assertEqual(
            tuple(manifest["components"]["amendments_texts"]["columns"]),
            ("amendment_id",),
        )
        arrival = b0.build_arrival_manifest(self.snapshot, manifest, source_hash)
        self.assertEqual(arrival["component_order"], list(b0.V2_COMPONENTS))
        self.assertEqual(list(arrival["components"]), list(b0.V2_COMPONENTS))
        self.assertEqual(arrival["components"]["amendments"]["row_count"], 1)
        self.assertEqual(arrival["components"]["amendments_texts"]["row_count"], 1)

    def test_36_v1_v2_schema_component_mismatches_fail_closed(self) -> None:
        manifest_path = self.directory / "sources-v2.json"
        valid_v2 = self._build_snapshot(
            self.snapshot, manifest_path, schema=b0.SOURCE_SCHEMA_V2
        )
        cases: list[tuple[str, dict[str, object]]] = []

        v2_missing_amendments = copy.deepcopy(valid_v2)
        del v2_missing_amendments["components"]["amendments"]
        cases.append(("v2_missing_amendments", v2_missing_amendments))

        v2_missing_texts = copy.deepcopy(valid_v2)
        del v2_missing_texts["components"]["amendments_texts"]
        cases.append(("v2_missing_texts", v2_missing_texts))

        v2_reversed = copy.deepcopy(valid_v2)
        items = list(v2_reversed["components"].items())
        v2_reversed["components"] = dict(items[:-2] + list(reversed(items[-2:])))
        cases.append(("v2_reversed_suffix", v2_reversed))

        v2_schema_with_v1_components = copy.deepcopy(self.manifest)
        v2_schema_with_v1_components["schema"] = b0.SOURCE_SCHEMA_V2
        cases.append(("v2_schema_with_v1_components", v2_schema_with_v1_components))

        v1_with_amendments = copy.deepcopy(self.manifest)
        v1_with_amendments["components"]["amendments"] = copy.deepcopy(
            valid_v2["components"]["amendments"]
        )
        cases.append(("v1_with_amendments", v1_with_amendments))

        v1_with_amendments_texts = copy.deepcopy(self.manifest)
        v1_with_amendments_texts["components"]["amendments_texts"] = copy.deepcopy(
            valid_v2["components"]["amendments_texts"]
        )
        cases.append(("v1_with_amendments_texts", v1_with_amendments_texts))

        v1_with_both = copy.deepcopy(valid_v2)
        v1_with_both["schema"] = b0.SOURCE_SCHEMA
        cases.append(("v1_with_both", v1_with_both))

        for name, malformed in cases:
            with self.subTest(name=name):
                path = self.directory / f"{name}.json"
                path.write_text(json.dumps(malformed), encoding="utf-8")
                with self.assertRaises(b0.UsageError):
                    b0.validate_source_manifest(path)

    def test_37_v2_amendment_components_reuse_all_component_validation_rules(self) -> None:
        manifest_path = self.directory / "sources-v2.json"
        valid_v2 = self._build_snapshot(
            self.snapshot, manifest_path, schema=b0.SOURCE_SCHEMA_V2
        )
        mutations = []

        invalid_url = copy.deepcopy(valid_v2)
        invalid_url["components"]["amendments"]["url"] = "http://invalid.example/amendments.csv"
        mutations.append(("invalid_url", invalid_url))

        duplicate_filename = copy.deepcopy(valid_v2)
        duplicate_filename["components"]["amendments"]["filename"] = "bills.csv"
        mutations.append(("duplicate_filename", duplicate_filename))

        traversal_filename = copy.deepcopy(valid_v2)
        traversal_filename["components"]["amendments"]["filename"] = "../amendments.csv"
        mutations.append(("traversal_filename", traversal_filename))

        invalid_format = copy.deepcopy(valid_v2)
        invalid_format["components"]["amendments_texts"]["format"] = "parquet"
        mutations.append(("invalid_format", invalid_format))

        mismatched_extension = copy.deepcopy(valid_v2)
        mismatched_extension["components"]["amendments_texts"]["format"] = "csv.gz"
        mutations.append(("mismatched_extension", mismatched_extension))

        missing_amendment_mapping = copy.deepcopy(valid_v2)
        del missing_amendment_mapping["components"]["amendments"]["columns"]["congress"]
        mutations.append(("missing_amendment_mapping", missing_amendment_mapping))

        missing_text_mapping = copy.deepcopy(valid_v2)
        del missing_text_mapping["components"]["amendments_texts"]["columns"]["amendment_id"]
        mutations.append(("missing_text_mapping", missing_text_mapping))

        duplicate_physical_mapping = copy.deepcopy(valid_v2)
        duplicate_physical_mapping["components"]["amendments"]["columns"][
            "amendment_type"
        ] = "amendment_id"
        mutations.append(("duplicate_physical_mapping", duplicate_physical_mapping))

        for name, malformed in mutations:
            with self.subTest(name=name):
                path = self.directory / f"{name}.json"
                path.write_text(json.dumps(malformed), encoding="utf-8")
                with self.assertRaises(b0.UsageError):
                    b0.validate_source_manifest(path)

        disallowed_host = copy.deepcopy(valid_v2)
        disallowed_host["components"]["amendments"]["url"] = (
            "https://other.example/amendments.csv"
        )
        path = self.directory / "disallowed-amendments-host.json"
        path.write_text(json.dumps(disallowed_host), encoding="utf-8")
        with self.assertRaises(b0.UsageError):
            b0.validate_source_manifest(
                path, fetch=True, allowed_hosts=["invalid.example"]
            )

    def test_38_v2_tiny_staged_components_arm_and_compute_act_and_text_metrics(self) -> None:
        manifest_path = self.directory / "sources-v2.json"
        manifest = self._build_snapshot(
            self.snapshot,
            manifest_path,
            schema=b0.SOURCE_SCHEMA_V2,
            amendment_rows=[
                ["samdt2231-119", "samdt", "2231", "119"],
                ["hamdt104-118", "hamdt", "104", "118.0"],
            ],
            amendments_texts_rows=[
                ["samdt2231-119"],
                ["hamdt104-118"],
            ],
        )
        validated, _ = b0.validate_source_manifest(manifest_path)
        rows = {
            name: b0.load_component_rows(self.snapshot, validated["components"][name])
            for name in b0.V2_COMPONENTS
        }
        self.assertEqual(rows["amendments"][0]["amendment_type"], "samdt")
        self.assertEqual(rows["amendments"][1]["amendment_type"], "hamdt")
        self.assertEqual(b0._parse_congress(rows["amendments"][1]["congress"]), 118)
        records = [
            {"bill": "H.R.1", "congress": 118},
            {"bill": "S.Amdt.2231", "congress": "119"},
            {"bill": "H.Amdt.104", "congress": "118.0"},
        ]
        bills = [{
            "bill_id": "hr1-118", "congress": "118",
            "bill_type": "hr", "bill_number": "1",
        }]
        with mock.patch.object(b0, "EXPECTED_ACTS", 3):
            unarmed, unarmed_joinable = b0.audit_act_universe(records, bills)
            armed, armed_joinable = b0.audit_act_universe(
                records,
                bills,
                amendment_rows=rows["amendments"],
                amendments_texts_row_count=len(rows["amendments_texts"]),
                amendments_declared=True,
            )
        self.assertTrue(armed["armed_strata"]["amendments"]["armed"])
        self.assertEqual(armed["armed_strata"]["amendments"]["denominator"], 2)
        self.assertEqual(armed["armed_strata"]["amendments"]["hit"], 2)
        self.assertEqual(armed["armed_strata"]["amendments"]["hit_rate"], 1.0)
        self.assertEqual(armed["bill_strata"], unarmed["bill_strata"])
        self.assertEqual(armed["joined"], unarmed["joined"])
        self.assertEqual(armed["legacy_composite"], unarmed["legacy_composite"])
        self.assertEqual(armed_joinable, unarmed_joinable)
        self.assertTrue(armed["pass"])

        discovery = [
            {"canonical_key": "118:hr:1", "bill_id": "hr1-118"},
            {"canonical_key": "119:samdt:2231"},
            {"canonical_key": "118:hamdt:104"},
        ]
        legfix = {record["canonical_key"] for record in discovery}
        with mock.patch.object(b0, "EXPECTED_DISCOVERY", 3):
            text = b0.audit_text_presence(
                discovery,
                {"hr1-118"},
                legfix,
                bicam_amendment_ids={
                    row["amendment_id"] for row in rows["amendments_texts"]
                },
                amendments_armed=True,
                amendments_declared=True,
            )
        self.assertTrue(text["pass"])
        self.assertEqual(text["strata"]["amendments"]["label"], "armed")
        self.assertEqual(text["strata"]["amendments"]["size"], 2)
        self.assertEqual(text["strata"]["amendments"]["agreement"], 2)
        self.assertEqual(text["strata"]["amendments"]["agreement_ratio"], 1.0)
        self.assertEqual(manifest["schema"], b0.SOURCE_SCHEMA_V2)

    def test_39_v2_missing_unreadable_or_empty_amendment_components_fail_closed(self) -> None:
        manifest_path = self.directory / "sources-v2.json"
        self._build_snapshot(
            self.snapshot, manifest_path, schema=b0.SOURCE_SCHEMA_V2
        )
        manifest, source_hash = b0.validate_source_manifest(manifest_path)

        amendments_path = self.snapshot / "amendments.csv"
        amendments_path.unlink()
        missing_out = self.directory / "missing-amendments-output"
        self.assertEqual(self._quiet_main([
            "--snapshot-dir", str(self.snapshot),
            "--source-manifest", str(manifest_path),
            "--audit", "--billstatus-parsed", str(manifest_path),
            "--out-dir", str(missing_out),
        ]), 4)
        self.assertFalse(missing_out.exists())
        with self.assertRaisesRegex(
            b0.IntegrityError, "missing BICAM component: amendments.csv"
        ):
            b0.build_arrival_manifest(self.snapshot, manifest, source_hash)

        self._build_snapshot(
            self.snapshot, manifest_path, schema=b0.SOURCE_SCHEMA_V2
        )
        manifest, source_hash = b0.validate_source_manifest(manifest_path)
        amendments_texts_path = self.snapshot / "amendments_texts_ids.csv"
        amendments_texts_path.write_bytes(b"\xff")
        with self.assertRaisesRegex(
            b0.IntegrityError,
            "component cannot be read: amendments_texts_ids.csv",
        ):
            b0.build_arrival_manifest(self.snapshot, manifest, source_hash)

        bill_record = [{"bill": "H.R.1", "congress": 118}]
        bill_rows = [{
            "bill_id": "hr1-118", "congress": "118",
            "bill_type": "hr", "bill_number": "1",
        }]
        empty_cases = [
            ([], 1, 0, 1),
            ([{"amendment_id": "samdt1-118"}], 0, 1, 0),
        ]
        for amendment_rows, texts_count, amendments_count, expected_texts in empty_cases:
            with self.subTest(
                amendments_count=amendments_count,
                amendments_texts_count=expected_texts,
            ):
                with mock.patch.object(b0, "EXPECTED_ACTS", 1):
                    report, _ = b0.audit_act_universe(
                        bill_record,
                        bill_rows,
                        amendment_rows=amendment_rows,
                        amendments_texts_row_count=texts_count,
                        amendments_declared=True,
                    )
                arm = report["armed_strata"]["amendments"]
                self.assertFalse(arm["armed"])
                self.assertEqual(
                    arm["condition"], "amendments component declared but empty"
                )
                self.assertIsNone(arm["hit_rate"])
                self.assertEqual(
                    arm["amendments_snapshot_rows"], amendments_count
                )
                self.assertEqual(
                    arm["amendments_texts_snapshot_rows"], expected_texts
                )
                self.assertFalse(report["pass"])

        paths = self._build_integration_inputs(
            schema=b0.SOURCE_SCHEMA_V2,
            amendment_rows=[],
        )
        out_dir = paths["root"] / "empty-amendments-output"
        code = self._quiet_main([
            "--snapshot-dir", str(paths["snapshot"]),
            "--source-manifest", str(paths["manifest"]),
            "--audit", "--repo-root", str(paths["root"]),
            "--roster", str(paths["roster"]),
            "--legislators", str(paths["legislators"]),
            "--voteview", str(paths["voteview"]),
            "--tags", str(paths["tags"]),
            "--legfix5-run", str(paths["legfix"]),
            "--legfix5-fulltext", str(paths["discovery"]),
            "--billstatus-parsed", str(paths["billstatus"]),
            "--out-dir", str(out_dir),
        ])
        self.assertEqual(code, 3)
        full_report = json.loads(
            (out_dir / b0.AUDIT_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(full_report["status"], "FAIL")
        self.assertEqual(
            full_report["act_universe_join"]["armed_strata"]["amendments"]["condition"],
            "amendments component declared but empty",
        )

    def test_40_armed_amendment_act_empty_denominator_fails_closed(self) -> None:
        records = [{"bill": "H.R.1", "congress": 118}]
        bills = [{
            "bill_id": "hr1-118", "congress": "118",
            "bill_type": "hr", "bill_number": "1",
        }]
        with mock.patch.object(b0, "EXPECTED_ACTS", 1):
            report, _ = b0.audit_act_universe(
                records,
                bills,
                amendment_rows=[{"amendment_id": "samdt1-118"}],
                amendments_texts_row_count=1,
                amendments_declared=True,
            )
        arm = report["armed_strata"]["amendments"]
        self.assertTrue(arm["armed"])
        self.assertEqual(arm["denominator"], 0)
        self.assertEqual(arm["hit_rate"], 0.0)
        self.assertFalse(report["pass"])

    def test_41_armed_amendment_act_threshold_exact_and_just_below(self) -> None:
        records = [{"bill": "H.R.1001", "congress": 118}] + [
            {"bill": f"S.Amdt.{index}", "congress": 118}
            for index in range(1, 101)
        ]
        bills = [{
            "bill_id": "hr1001-118", "congress": "118",
            "bill_type": "hr", "bill_number": "1001",
        }]

        def audit(hit_count: int) -> dict[str, object]:
            amendment_rows = [
                {"amendment_id": f"samdt{index}-118"}
                for index in range(1, hit_count + 1)
            ]
            with mock.patch.object(b0, "EXPECTED_ACTS", 101):
                report, _ = b0.audit_act_universe(
                    records,
                    bills,
                    amendment_rows=amendment_rows,
                    amendments_texts_row_count=1,
                    amendments_declared=True,
                )
            return report

        exact = audit(95)
        below = audit(94)
        self.assertEqual(exact["armed_strata"]["amendments"]["denominator"], 100)
        self.assertEqual(exact["armed_strata"]["amendments"]["hit_rate"], 0.95)
        self.assertTrue(exact["pass"])
        self.assertEqual(below["armed_strata"]["amendments"]["hit_rate"], 0.94)
        self.assertFalse(below["pass"])

    def test_42_armed_amendment_normalization_and_malformed_veto(self) -> None:
        bills = [{
            "bill_id": "hr1-118", "congress": "118",
            "bill_type": "hr", "bill_number": "1",
        }]
        digitless_records = [
            {"bill": "H.R.1", "congress": 118},
            {"bill": "S.Amdt.", "congress": 118},
        ]
        with mock.patch.object(b0, "EXPECTED_ACTS", 2):
            unarmed, _ = b0.audit_act_universe(digitless_records, bills)
            armed, _ = b0.audit_act_universe(
                digitless_records,
                bills,
                amendment_rows=[{"amendment_id": "samdt2231-119"}],
                amendments_texts_row_count=1,
                amendments_declared=True,
            )
        self.assertEqual(unarmed["misses"], armed["misses"])
        self.assertEqual(unarmed["bill_strata"], armed["bill_strata"])
        self.assertEqual(unarmed["malformed_misses"], 1)
        self.assertEqual(armed["armed_strata"]["amendments"]["denominator"], 0)

        malformed_congress_records = [
            {"bill": "H.R.1", "congress": 118},
            {"bill": "S.Amdt.2231", "congress": "not-a-congress"},
        ]
        with mock.patch.object(b0, "EXPECTED_ACTS", 2):
            unarmed_congress, _ = b0.audit_act_universe(
                malformed_congress_records, bills
            )
            armed_congress, _ = b0.audit_act_universe(
                malformed_congress_records,
                bills,
                amendment_rows=[{"amendment_id": "samdt2231-119"}],
                amendments_texts_row_count=1,
                amendments_declared=True,
            )
        self.assertEqual(unarmed_congress["misses"][-1]["reason"], "NON_BILL")
        self.assertEqual(armed_congress["misses"][-1]["reason"], "MALFORMED")
        self.assertEqual(
            armed_congress["armed_strata"]["amendments"]["denominator"], 1
        )
        self.assertEqual(armed_congress["malformed_misses"], 1)
        self.assertFalse(armed_congress["pass"])

    def test_43_armed_amendment_text_uses_ids_and_bill_matrix_semantics(self) -> None:
        discovery = [
            {"canonical_key": "118:hr:1", "bill_id": "hr1-118"},
            {"canonical_key": "118:samdt:1"},
            {"canonical_key": "118:samdt:2"},
            {"canonical_key": "118:hamdt:3"},
            {"canonical_key": "118:hamdt:4"},
        ]
        legfix = {"118:hr:1", "118:samdt:1", "118:hamdt:3"}
        with mock.patch.object(b0, "EXPECTED_DISCOVERY", 5):
            report = b0.audit_text_presence(
                discovery,
                {"hr1-118"},
                legfix,
                bicam_amendment_ids={"samdt1-118", "samdt2-118"},
                amendments_armed=True,
                amendments_declared=True,
            )
        amendment = report["strata"]["amendments"]
        self.assertEqual(amendment["label"], "armed")
        self.assertEqual(amendment["size"], 4)
        self.assertEqual(amendment["agreement"], 2)
        self.assertEqual(amendment["agreement_ratio"], 0.5)
        self.assertEqual(amendment["matrix"], {
            "BICAM_AND_LEGFIX5": 1,
            "BICAM_ONLY": 1,
            "LEGFIX5_ONLY": 1,
            "NEITHER": 1,
        })
        self.assertFalse(report["pass"])

        leading_zero = b0.audit_text_presence(
            [{"canonical_key": "118:samdt:01542"}],
            set(),
            set(),
            bicam_amendment_ids={"samdt1542-118"},
            amendments_armed=True,
            amendments_declared=True,
        )
        self.assertEqual(leading_zero["matrix"]["BICAM_ONLY"], 1)
        non_digit = b0.audit_text_presence(
            [{"canonical_key": "118:samdt:abc"}],
            set(),
            set(),
            bicam_amendment_ids={"samdtabc-118"},
            amendments_armed=True,
            amendments_declared=True,
        )
        self.assertEqual(non_digit["matrix"]["NEITHER"], 1)

    def test_44_armed_amendment_text_empty_denominator_fails_closed(self) -> None:
        discovery = [{"canonical_key": "118:hr:1", "bill_id": "hr1-118"}]
        with mock.patch.object(b0, "EXPECTED_DISCOVERY", 1):
            report = b0.audit_text_presence(
                discovery,
                {"hr1-118"},
                {"118:hr:1"},
                bicam_amendment_ids={"samdt1-118"},
                amendments_armed=True,
                amendments_declared=True,
            )
        amendment = report["strata"]["amendments"]
        self.assertEqual(amendment["label"], "armed")
        self.assertEqual(amendment["size"], 0)
        self.assertEqual(amendment["agreement_ratio"], 0.0)
        self.assertFalse(report["pass"])

    def test_45_armed_amendment_text_threshold_exact_and_just_below(self) -> None:
        discovery = [{"canonical_key": "118:hr:1001", "bill_id": "hr1001-118"}] + [
            {"canonical_key": f"118:samdt:{index}"}
            for index in range(1, 101)
        ]
        amendment_ids = {f"samdt{index}-118" for index in range(1, 101)}

        def audit(agreement_count: int) -> dict[str, object]:
            legfix = {"118:hr:1001"} | {
                f"118:samdt:{index}"
                for index in range(1, agreement_count + 1)
            }
            with mock.patch.object(b0, "EXPECTED_DISCOVERY", 101):
                return b0.audit_text_presence(
                    discovery,
                    {"hr1001-118"},
                    legfix,
                    bicam_amendment_ids=amendment_ids,
                    amendments_armed=True,
                    amendments_declared=True,
                )

        exact = audit(95)
        below = audit(94)
        self.assertEqual(exact["strata"]["amendments"]["size"], 100)
        self.assertEqual(exact["strata"]["amendments"]["agreement_ratio"], 0.95)
        self.assertTrue(exact["pass"])
        self.assertEqual(below["strata"]["amendments"]["agreement_ratio"], 0.94)
        self.assertFalse(below["pass"])

    def test_46_v1_report_is_byte_identical_except_amendment_condition(self) -> None:
        records = [
            {"bill": "H.R.1", "congress": 118},
            {"bill": "H.Amdt.104", "congress": "118.0"},
            {"bill": "S.Amdt.", "congress": 118},
        ]
        bills = [{
            "bill_id": "hr1-118", "congress": "118",
            "bill_type": "hr", "bill_number": "1",
        }]
        default_act, default_joinable = b0.audit_act_universe(records, bills)
        explicit_v1_act, explicit_v1_joinable = b0.audit_act_universe(
            records,
            bills,
            amendment_rows=[],
            amendments_texts_row_count=0,
            amendments_declared=False,
        )
        self.assertEqual(
            b0.canonical_json_bytes(default_act),
            b0.canonical_json_bytes(explicit_v1_act),
        )
        self.assertEqual(default_joinable, explicit_v1_joinable)
        self.assertEqual(
            explicit_v1_act["armed_strata"]["amendments"],
            {
                "armed": False,
                "condition": "amendments component not declared in manifest",
                "hit_rate": None,
            },
        )
        self.assertEqual(
            [miss["reason"] for miss in explicit_v1_act["misses"]],
            ["NON_BILL", "MALFORMED"],
        )

        discovery = [
            {"canonical_key": "118:hr:1", "bill_id": "hr1-118"},
            {"canonical_key": "118:hamdt:104"},
        ]
        default_text = b0.audit_text_presence(
            discovery, {"hr1-118"}, {"118:hr:1", "118:hamdt:104"}
        )
        explicit_v1_text = b0.audit_text_presence(
            discovery,
            {"hr1-118"},
            {"118:hr:1", "118:hamdt:104"},
            bicam_amendment_ids=set(),
            amendments_armed=False,
            amendments_declared=False,
        )
        self.assertEqual(
            b0.canonical_json_bytes(default_text),
            b0.canonical_json_bytes(explicit_v1_text),
        )
        self.assertEqual(explicit_v1_text["strata"]["amendments"], {
            "label": "report_only",
            "size": 1,
            "LEGFIX5_ONLY": 1,
        })

        paths = self._build_integration_inputs()
        arguments = [
            "--snapshot-dir", str(paths["snapshot"]),
            "--source-manifest", str(paths["manifest"]),
            "--audit", "--repo-root", str(paths["root"]),
            "--roster", str(paths["roster"]),
            "--legislators", str(paths["legislators"]),
            "--voteview", str(paths["voteview"]),
            "--tags", str(paths["tags"]),
            "--legfix5-run", str(paths["legfix"]),
            "--legfix5-fulltext", str(paths["discovery"]),
            "--billstatus-parsed", str(paths["billstatus"]),
        ]
        args = b0.build_parser().parse_args(arguments)
        source_manifest, source_hash = b0.validate_source_manifest(paths["manifest"])
        arrival = b0.build_arrival_manifest(
            paths["snapshot"], source_manifest, source_hash
        )
        actual = b0.run_audits(args, source_manifest, source_hash, arrival)
        normalized_bytes = b0.canonical_json_bytes(actual).decode("utf-8").replace(
            "amendments component not declared in manifest",
            "future contract - amendments component not downloaded, schema unseen",
        ).encode("utf-8")
        # Golden sha256 generated by the coordinator on 2026-08-17 from the TRUE pre-change tool at commit 22c55d0ac over this fixture builder with deterministic gzip writes (mtime=0); proven identical across independent fixture instances, and the normalized current output was separately proven byte-equal. A mismatch means the v1 integration surface moved: investigate, and re-pin only for a deliberately ratified v1 change.
        # RE-PINNED 2026-10-07 (not re-derived from a pre-change tool): the act universe grew 644 -> 648 (telecom association anchors), so EXPECTED_ACTS and the fixture universe moved with it.
        expected_sha256 = "fd13e507c0b099aa1bffa2cbccb547e6be47f8102beda55bc3eb7931c2518c10"
        actual_sha256 = hashlib.sha256(normalized_bytes).hexdigest()
        diagnostic_path = None
        if actual_sha256 != expected_sha256:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="b0-v1-normalized-",
                suffix=".json",
                delete=False,
            ) as stream:
                stream.write(normalized_bytes)
                diagnostic_path = Path(stream.name)
        self.assertEqual(
            actual_sha256,
            expected_sha256,
            f"normalized v1 report written to {diagnostic_path}",
        )


class B0ArrivalWorldRegressionTests(unittest.TestCase):
    """Regressions from the 2026-08-17 first real arrival run."""

    def test_parse_congress_tolerates_float_strings(self) -> None:
        self.assertEqual(b0._parse_congress("118"), 118)
        self.assertEqual(b0._parse_congress("118.0"), 118)
        self.assertEqual(b0._parse_congress(119), 119)
        self.assertIsNone(b0._parse_congress("RH"))
        self.assertIsNone(b0._parse_congress(""))
        self.assertIsNone(b0._parse_congress("118.5"))

    def test_bicam_index_keeps_float_encoded_congress_rows(self) -> None:
        rows = [
            {"bill_id": "hr1-118", "congress": "118.0", "bill_type": "hr", "bill_number": "1"},
            {"bill_id": "s2-117", "congress": "117", "bill_type": "s", "bill_number": "2"},
            {"bill_id": "bad-1", "congress": "RH", "bill_type": "hr", "bill_number": "3"},
        ]
        index = b0._bicam_bill_index(rows)
        self.assertIn((118, "H", "R", 1), index)
        self.assertIn((117, "S", "R", 2), index)
        self.assertEqual(sum(len(v) for v in index.values()), 2)

    def test_text_presence_converts_canonical_keys_to_bicam_ids(self) -> None:
        report = b0.audit_text_presence(
            [{"canonical_key": "117:hr:2434"}],
            {"hr2434-117"},
            {"117:hr:2434"},
        )
        self.assertEqual(report["matrix"]["BICAM_AND_LEGFIX5"], 1)
        self.assertEqual(report["matrix"]["LEGFIX5_ONLY"], 0)


if __name__ == "__main__":
    unittest.main()

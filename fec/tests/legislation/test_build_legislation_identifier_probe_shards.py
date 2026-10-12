import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


FEC_DIR = Path(__file__).resolve().parents[2]
ROOT = FEC_DIR.parent
if str(FEC_DIR) not in sys.path:
    sys.path.insert(0, str(FEC_DIR))

from legislation import build_legislation_identifier_probe_shards as shards


class IdentifierProbeShardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = self.root / "registry.json"
        self.preserved = self.root / "preserved"
        self.shard_root = self.root / "shards"
        self.plan = self.root / "plan.json"
        self.output = self.root / "consolidated"
        self.entries = [self._entry(index) for index in range(1, 211)]
        self.registry.write_text(
            json.dumps(
                {
                    "schema": "acu-congress-advanced-identifier-probes-v1",
                    "category_count": 210,
                    "coverage_probes": [],
                    "categories": self.entries,
                    "collection_contract": {
                        "primary_instrument": (
                            "Congress.gov Advanced Search - Legislation"
                        ),
                        "quorum_used": False,
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.registry_hash = hashlib.sha256(
            self.registry.read_bytes()
        ).hexdigest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _entry(index: int) -> dict:
        return {
            "id": f"q{index:03d}",
            "label": f"Probe {index}",
            "scoring_category": False,
            "record_kind": "identifier_probe",
            "parent_category_id": "parent",
            "parent_category_label": "Parent",
            "identifier_text": f"term {index}",
            "advanced_search_query": f"(term {index})",
        }

    def _write_zero_query(self, run: Path, entry: dict) -> None:
        query_dir = run / "queries"
        query_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict] = []
        artifact = {
            "schema": "congress-advanced-search-query-v1",
            "completed_at": "2026-07-26T00:00:00Z",
            "primary_instrument": (
                "Congress.gov Advanced Search - Legislation"
            ),
            "query_id": entry["id"],
            "label": entry["label"],
            "scoring_category": False,
            "record_kind": "identifier_probe",
            "parent_category_id": "parent",
            "parent_category_label": "Parent",
            "identifier_text": entry["identifier_text"],
            "title_alias": None,
            "ambiguous_short_act_alias": False,
            "membership_non_dispositive": False,
            "source_paragraph": 1,
            "registry_sha256": self.registry_hash,
            "query_sha256": "unused",
            "advanced_search_query": entry["advanced_search_query"],
            "search_url": "https://www.congress.gov/",
            "result_total": 0,
            "page_count": 0,
            "unique_record_count": 0,
            "counts_by_congress": {},
            "counts_by_type": {},
            "pages": [],
            "records_sha256": shards.continuity.javascript_json_sha256(
                records
            ),
            "zero_result_surface_validation": "strict-main-v2",
            "validation": {
                "exact_result_total": True,
                "unique_result_total": True,
                "every_page_exact": True,
            },
            "records": records,
        }
        (query_dir / f"{entry['id']}.json").write_text(
            json.dumps(artifact, indent=2) + "\n", encoding="utf-8"
        )

    def _plan_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            registry=self.registry,
            preserved_run=self.preserved,
            plan=self.plan,
            shard_root=self.shard_root,
            consolidated=self.output,
            profile_root=str(self.root / "profiles"),
            shards=3,
            delay_ms=1500,
            expected_preserved=2,
        )

    def _make_plan(self) -> dict:
        for entry in self.entries[:2]:
            self._write_zero_query(self.preserved, entry)
        with mock.patch.object(shards, "ROOT", self.root):
            return shards.build_plan(self._plan_args())

    def test_plan_is_disjoint_complete_and_hash_binds_preserved(self) -> None:
        plan = self._make_plan()
        self.assertEqual(2, plan["preserved"]["query_count"])
        self.assertEqual(208, plan["partition"]["collection_query_count"])
        self.assertEqual([70, 69, 69], [
            shard["query_count"] for shard in plan["shards"]
        ])
        ids = [
            *[row["query_id"] for row in plan["preserved"]["queries"]],
            *[
                query_id
                for shard in plan["shards"]
                for query_id in shard["query_ids"]
            ],
        ]
        self.assertEqual(210, len(ids))
        self.assertEqual(210, len(set(ids)))
        self.assertTrue(all(shard["headless"] for shard in plan["shards"]))
        self.assertEqual(
            3, len({shard["profile_dir"] for shard in plan["shards"]})
        )
        self.assertTrue(Path(f"{self.plan}.sha256").is_file())

    def test_preserved_mutation_fails_closed(self) -> None:
        self._make_plan()
        target = self.preserved / "queries" / "q001.json"
        target.write_text("{}", encoding="utf-8")
        with mock.patch.object(shards, "ROOT", self.root):
            with self.assertRaisesRegex(
                shards.ShardValidationError, "failed query/page validation"
            ):
                shards.consolidate(
                    argparse.Namespace(plan=self.plan, output=self.output)
                )

    def test_consolidation_requires_and_publishes_exact_210(self) -> None:
        plan = self._make_plan()
        for shard in plan["shards"]:
            run = self.root / shard["output_dir"]
            manifest_queries = []
            for query_id in shard["query_ids"]:
                entry = next(row for row in self.entries if row["id"] == query_id)
                self._write_zero_query(run, entry)
                artifact = json.loads(
                    (run / "queries" / f"{query_id}.json").read_text(
                        encoding="utf-8"
                    )
                )
                manifest_queries.append(
                    shards.query_manifest_projection(
                        artifact, f"queries/{query_id}.json"
                    )
                )
            (run / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": (
                            "acu-congress-advanced-pages-manifest-v1"
                        ),
                        "status": "complete",
                        "registry_sha256": self.registry_hash,
                        "selected_query_ids": shard["query_ids"],
                        "queries": manifest_queries,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        with mock.patch.object(shards, "ROOT", self.root):
            manifest = shards.consolidate(
                argparse.Namespace(plan=self.plan, output=self.output)
            )
        self.assertEqual(210, len(manifest["queries"]))
        self.assertTrue(
            manifest["consolidation"]["acceptance_passed"]
        )
        self.assertEqual(0, manifest["union"]["unique_record_count"])
        self.assertTrue((self.output / "manifest.json.sha256").is_file())
        self.assertTrue(
            (self.output / "consolidation_receipt.json").is_file()
        )
        with mock.patch.object(shards, "ROOT", self.root):
            with self.assertRaisesRegex(
                shards.ShardValidationError, "refusing overwrite"
            ):
                shards.consolidate(
                    argparse.Namespace(plan=self.plan, output=self.output)
                )

    def test_launcher_has_cooldown_headless_and_legacy_guards(self) -> None:
        launcher = (
            FEC_DIR / "run_legislation_identifier_probe_shards.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Wait-GlobalCooldown $document", launcher)
        self.assertIn("Assert-NoLegacyCollector $document", launcher)
        self.assertIn('"--categories", $categories', launcher)
        self.assertIn('"--headless"', launcher)
        self.assertIn("--resume", launcher)
        self.assertIn("-ArgumentList $quotedArguments", launcher)
        self.assertIn("distinct output directory and browser profile", launcher)


if __name__ == "__main__":
    unittest.main()

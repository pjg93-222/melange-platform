# -*- coding: utf-8 -*-
"""Offline contracts for resumable staged legislation review batches."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


TESTS = Path(__file__).resolve().parent.parent
FEC = TESTS.parent
sys.path.insert(0, str(FEC))
sys.path.insert(0, str(TESTS))
from legislation import run_legislation_review_batches as BATCH  # noqa: E402
from test_build_legislation_membership_triage import (  # noqa: E402
    Fixture,
    read_ndjson,
    write_json,
)


def run_fixture(fixture: Fixture, out: Path, *, batch_size=2, resume=False):
    return BATCH.run(
        run_dir=fixture.run,
        registry_path=fixture.registry,
        relationships_dir=fixture.relationships,
        text_dir=fixture.text,
        article_dir=fixture.articles,
        review_ledger_path=fixture.review,
        out_dir=out,
        batch_size=batch_size,
        resume=resume,
        workspace_root=fixture.base,
    )


class ReviewBatchTests(unittest.TestCase):
    def test_atomic_write_retries_transient_windows_replace_denial(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "checkpoint.json"
            target.write_bytes(b"old\n")
            real_replace = BATCH.os.replace
            attempts = 0

            def transient_denial(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("transient scanner lock")
                return real_replace(source, destination)

            with mock.patch.object(BATCH.os, "replace", transient_denial), mock.patch.object(
                BATCH.time, "sleep"
            ):
                BATCH.atomic_write(target, b"new\n")

            self.assertEqual(3, attempts)
            self.assertEqual(b"new\n", target.read_bytes())
            self.assertEqual([], list(target.parent.glob(f".{target.name}.tmp-*")))

    def test_complete_shards_and_views_expose_every_decision_state(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            out = fixture.base / "audit-review-batches"
            manifest, runtime = run_fixture(fixture, out, batch_size=1)

            self.assertTrue(manifest["complete"])
            self.assertEqual(8, manifest["source_membership_count"])
            self.assertEqual(8, manifest["distinct_source_membership_count"])
            self.assertEqual(8, manifest["sharded_membership_count"])
            self.assertEqual(8, manifest["shard_count"])
            self.assertEqual(8, manifest["valid_checkpoint_count"])
            self.assertEqual(0, manifest["missing_membership_count"])
            self.assertEqual(0, manifest["extra_membership_count"])
            self.assertEqual(0, manifest["duplicate_membership_count"])
            self.assertEqual({"rebuilt_shards": 8, "reused_shards": 0}, runtime)
            self.assertEqual(0, manifest["machine_proposals_converted_to_ratified_decisions"])
            self.assertFalse(manifest["machine_proposals_are_scoring_eligible"])
            self.assertFalse(manifest["acceptance_for_scoring"])

            views = out / "views"
            all_rows = read_ndjson(views / "all_memberships.ndjson")
            unresolved = read_ndjson(views / "unresolved_memberships.ndjson")
            ratified_exclusions = read_ndjson(
                views / "ratified_exclusions.ndjson"
            )
            proposals = read_ndjson(views / "machine_proposals.ndjson")
            proposed_inclusions = read_ndjson(
                views / "proposed_inclusions.ndjson"
            )
            proposed_exclusions = read_ndjson(
                views / "proposed_exclusions.ndjson"
            )
            self.assertEqual(8, len(all_rows))
            self.assertEqual(7, len(unresolved))
            self.assertEqual(1, len(ratified_exclusions))
            self.assertEqual(3, len(proposals))
            self.assertEqual(1, len(proposed_inclusions))
            self.assertEqual(2, len(proposed_exclusions))
            self.assertEqual(
                len(proposals), len(proposed_inclusions) + len(proposed_exclusions)
            )
            for proposal in proposals:
                self.assertEqual("unresolved", proposal["disposition"])
                self.assertEqual("machine_proposed", proposal["adjudication_state"])
                self.assertFalse(proposal["scoring_eligible"])
                self.assertTrue(proposal["requires_human_ratification"])

            observed_memberships = []
            for receipt in manifest["shards"]:
                shard_path = out / receipt["path"]
                checkpoint_path = out / receipt["checkpoint_path"]
                self.assertTrue(shard_path.is_file())
                self.assertTrue(checkpoint_path.is_file())
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                self.assertTrue(checkpoint["complete"])
                self.assertEqual(0, checkpoint["machine_proposals_ratified"])
                self.assertIn("shards/", receipt["path"])
                observed_memberships.extend(
                    BATCH.membership_identity(row)
                    for row in read_ndjson(shard_path)
                )
            self.assertEqual(
                sorted(BATCH.membership_identity(row) for row in all_rows),
                sorted(observed_memberships),
            )

    def test_resume_reuses_valid_checkpoints_and_repairs_one_corrupt_shard(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            out = fixture.base / "audit-review-batches"
            manifest1, _runtime1 = run_fixture(fixture, out, batch_size=2)
            manifest_bytes = (out / "completeness_manifest.json").read_bytes()
            target = out / manifest1["shards"][0]["path"]
            expected_bytes = target.read_bytes()
            target.write_bytes(b"corrupt\n")

            manifest2, runtime2 = run_fixture(
                fixture, out, batch_size=2, resume=True
            )
            self.assertEqual(expected_bytes, target.read_bytes())
            self.assertEqual(1, runtime2["rebuilt_shards"])
            self.assertEqual(
                manifest2["shard_count"] - 1, runtime2["reused_shards"]
            )
            self.assertEqual(manifest1, manifest2)
            self.assertEqual(
                manifest_bytes, (out / "completeness_manifest.json").read_bytes()
            )
            self.assertFalse((out / "state.partial.json").exists())

    def test_resume_fails_closed_when_source_or_batch_contract_changes(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            out = fixture.base / "audit-review-batches"
            run_fixture(fixture, out, batch_size=2)
            registry = json.loads(fixture.registry.read_text(encoding="utf-8"))
            registry["fixture_mutation"] = True
            write_json(fixture.registry, registry)
            with self.assertRaisesRegex(BATCH.BatchError, "source input changed"):
                run_fixture(fixture, out, batch_size=2, resume=True)
            self.assertTrue((out / "completeness_manifest.json").is_file())

        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            out = fixture.base / "audit-review-batches"
            run_fixture(fixture, out, batch_size=2)
            with self.assertRaisesRegex(BATCH.BatchError, "batch size differs"):
                run_fixture(fixture, out, batch_size=1, resume=True)

    def test_incomplete_enrichment_fails_before_output_is_created(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            manifest_path = fixture.text / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["status"] = "running"
            write_json(manifest_path, manifest)
            out = fixture.base / "audit-review-batches"
            with self.assertRaisesRegex(
                BATCH.BatchError, "final merged/enrichment inputs are not ready"
            ):
                run_fixture(fixture, out)
            self.assertFalse(out.exists())

    def test_complete_generations_are_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(Path(raw))
            out1 = fixture.base / "audit-review-batches-one"
            out2 = fixture.base / "audit-review-batches-two"
            manifest1, _ = run_fixture(fixture, out1, batch_size=2)
            manifest2, _ = run_fixture(fixture, out2, batch_size=2)
            self.assertEqual(manifest1, manifest2)
            self.assertEqual(
                (out1 / "completeness_manifest.json").read_bytes(),
                (out2 / "completeness_manifest.json").read_bytes(),
            )
            for receipt in manifest1["shards"]:
                self.assertEqual(
                    (out1 / receipt["path"]).read_bytes(),
                    (out2 / receipt["path"]).read_bytes(),
                )
                self.assertEqual(
                    (out1 / receipt["checkpoint_path"]).read_bytes(),
                    (out2 / receipt["checkpoint_path"]).read_bytes(),
                )
            for name in BATCH.VIEW_NAMES:
                self.assertEqual(
                    (out1 / "views" / name).read_bytes(),
                    (out2 / "views" / name).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

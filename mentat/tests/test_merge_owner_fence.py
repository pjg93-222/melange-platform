"""Disposable source and shard fixtures for orphaned merge-child fencing."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_company_cycle_fanout as fanout  # noqa: E402
import merge_company_shards as merger  # noqa: E402
from test_merge_company_shards import create_database, seed_shard, scalar  # noqa: E402


class MergeOwnerFenceTest(unittest.TestCase):
    def fixture(self, root: Path, count: int = 1):
        target = root / "source.db"
        create_database(target)
        shards = []
        for index in range(1, count + 1):
            shard = root / f"shard-{index}.db"
            create_database(shard)
            seed_shard(shard, f"Company {index}", "Other", str(index))
            shards.append(f"{shard}::Company {index}")
        return target, shards

    def test_orphaned_merge_lock_blocks_supervisor_and_second_child(self):
        with tempfile.TemporaryDirectory(prefix="mentat-merge-lock-") as tmp:
            root = Path(tmp)
            target, shards = self.fixture(root)
            ready, release = root / "ready", root / "release"
            code = (
                "import sys,time; from pathlib import Path; "
                "sys.path.insert(0,sys.argv[1]); import merge_company_shards as m; "
                "\nwith m.merge_writer_lock(Path(sys.argv[2])):\n"
                " Path(sys.argv[3]).write_text('ready');\n"
                " while not Path(sys.argv[4]).exists(): time.sleep(.02)\n"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(PROJECT_ROOT / "tools"),
                 str(target), str(ready), str(release)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists(), child.stderr.read() if child.poll() is not None else "")
                before = merger.file_sha256(target)
                with self.assertRaisesRegex(RuntimeError, "merge child still owns"):
                    fanout.assert_no_active_merge_writer(target)
                with self.assertRaisesRegex(RuntimeError, "Another merge child"):
                    merger.main([str(target), *shards])
                self.assertEqual(merger.file_sha256(target), before)
            finally:
                release.write_text("go", encoding="utf-8")
                _, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stderr)
            fanout.assert_no_active_merge_writer(target)

    def test_late_child_from_old_supervisor_cannot_merge(self):
        with tempfile.TemporaryDirectory(prefix="mentat-merge-token-") as tmp:
            root = Path(tmp)
            target, shards = self.fixture(root)
            owner = root / "supervisor-owner.json"
            owner.write_text(json.dumps({"token": "replacement", "active": True}), encoding="utf-8")
            before = merger.file_sha256(target)
            with self.assertRaisesRegex(RuntimeError, "superseded supervisor"):
                merger.main([str(target), "--supervisor-owner-path", str(owner),
                             "--supervisor-token", "old", *shards])
            self.assertEqual(merger.file_sha256(target), before)

    def test_owner_switch_during_single_transaction_rolls_back(self):
        with tempfile.TemporaryDirectory(prefix="mentat-merge-rollback-") as tmp:
            root = Path(tmp)
            target, shards = self.fixture(root)
            owner = root / "supervisor-owner.json"
            owner.write_text(json.dumps({"token": "old", "active": True}), encoding="utf-8")
            before = merger.file_sha256(target)
            original = merger.merge_shard

            def switch_owner(*args, **kwargs):
                result = original(*args, **kwargs)
                owner.write_text(json.dumps({"token": "replacement", "active": True}), encoding="utf-8")
                return result

            with mock.patch.object(merger, "merge_shard", side_effect=switch_owner):
                with self.assertRaisesRegex(RuntimeError, "superseded supervisor"):
                    merger.main([str(target), "--supervisor-owner-path", str(owner),
                                 "--supervisor-token", "old", *shards])
            self.assertEqual(merger.file_sha256(target), before)

    def test_batched_merge_rechecks_source_after_staging(self):
        with tempfile.TemporaryDirectory(prefix="mentat-merge-staged-") as tmp:
            root = Path(tmp)
            target, shards = self.fixture(root, count=3)
            before = merger.file_sha256(target)
            original = merger.merge_shard
            touched = False

            def mutate_source(*args, **kwargs):
                nonlocal touched
                result = original(*args, **kwargs)
                if not touched:
                    touched = True
                    with closing(sqlite3.connect(target)) as connection, connection:
                        connection.execute(
                            "INSERT INTO contributions(sub_id,company,amount) VALUES ('external','Other',1)"
                        )
                return result

            with mock.patch.object(merger, "merge_shard", side_effect=mutate_source):
                with self.assertRaisesRegex(RuntimeError, "Target database changed"):
                    with redirect_stdout(StringIO()):
                        merger.main([str(target), "--expected-target-sha256", before,
                                     "--merge-batch-limit", "2", *shards])
            self.assertEqual(scalar(target, "SELECT count(*) FROM contributions"), 1)
            self.assertEqual(scalar(target, "SELECT sub_id FROM contributions"), "external")


if __name__ == "__main__":
    unittest.main()

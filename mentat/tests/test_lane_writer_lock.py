"""Process-level lane writer fencing; all databases and processes are disposable."""

from __future__ import annotations

import subprocess
import json
import os
import sqlite3
from contextlib import closing
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RSCRIPT = Path(r"C:\Program Files\R\R-4.6.0\bin\Rscript.exe")
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import api_company_cycle_fanout as fanout  # noqa: E402


class LaneWriterLockTest(unittest.TestCase):
    @unittest.skipUnless(RSCRIPT.exists(), "Rscript unavailable")
    def test_orphaned_writer_fences_restarted_lane_until_exit(self):
        with tempfile.TemporaryDirectory(prefix="mentat-lane-writer-") as directory:
            root = Path(directory)
            script = root / "hold-writer.R"
            script.write_text(
                "args <- commandArgs(trailingOnly=TRUE)\n"
                f"source('{(PROJECT_ROOT / 'R' / 'paths.R').as_posix()}')\n"
                f"source('{(PROJECT_ROOT / 'R' / 'lane_owner.R').as_posix()}')\n"
                "run <- function() {\n"
                "owner <- acquire_lane_writer(args[[1L]])\n"
                "on.exit(release_lane_writer(owner), add=TRUE)\n"
                "if (length(args)>2L) {\n"
                "  writeLines('ready', args[[3L]])\n"
                "  while (!file.exists(args[[4L]])) Sys.sleep(0.05)\n"
                "}\n"
                "cat('acquired\\n')\n"
                "}\nrun()\n",
                encoding="utf-8",
            )
            database = root / "lanes" / "lane-01" / "mentat.db"
            database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)):
                pass
            ready = root / "ready"
            release = root / "release"
            holder = subprocess.Popen(
                [str(RSCRIPT), str(script), str(database), "hold", str(ready), str(release)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(ready.exists(), holder.stderr.read() if holder.poll() is not None else "")
                with self.assertRaisesRegex(RuntimeError, "R lane writer still owns"):
                    fanout.assert_no_active_lane_writers(root)
                contender = subprocess.run(
                    [str(RSCRIPT), str(script), str(database), "contend"],
                    capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(contender.returncode, 0)
                self.assertIn("Another R lane writer owns this database", contender.stderr)
                assignments = root / "assignments.json"
                assignments.write_text(json.dumps({"lane": 1, "tasks": [
                    {"task_id": "fixture-2026", "company": "Fixture", "cycle": 2026}
                ]}), encoding="utf-8")
                receipt = root / "receipt.json"
                sentinel = '{"status":"succeeded","checkpoint":"preserved"}\n'
                receipt.write_text(sentinel, encoding="utf-8")
                environment = os.environ.copy()
                environment["FEC_API_KEY"] = "fixture-key"
                duplicate = subprocess.run([
                    str(RSCRIPT), str(PROJECT_ROOT / "tools" / "collect_api_lane.R"),
                    str(database), "Fintech", str(assignments), str(receipt),
                    "fixture-key-id", "1",
                ], env=environment, capture_output=True, text=True, timeout=20)
                self.assertEqual(duplicate.returncode, 4, duplicate.stderr + duplicate.stdout)
                self.assertEqual(receipt.read_text(encoding="utf-8"), sentinel)
            finally:
                release.write_text("release", encoding="utf-8")
                holder.communicate(timeout=15)
            replacement = subprocess.run(
                [str(RSCRIPT), str(script), str(database), "replace"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(replacement.returncode, 0, replacement.stderr)
            self.assertIn("acquired", replacement.stdout)
            # A child spawned by a dead supervisor may start only after the
            # replacement's lock probe. Its old token must still fence it.
            owner_receipt = root / "supervisor-owner.json"
            owner_receipt.write_text(json.dumps({"token": "new-owner", "active": True}),
                                     encoding="utf-8")
            assignments.write_text(json.dumps({"lane": 1, "resume": False,
                "supervisor_owner_path": str(owner_receipt),
                "supervisor_token": "old-owner", "tasks": [
                    {"task_id": "fixture-2026", "company": "Fixture", "cycle": 2026}
                ]}), encoding="utf-8")
            late_child = subprocess.run([
                str(RSCRIPT), str(PROJECT_ROOT / "tools" / "collect_api_lane.R"),
                str(database), "Fintech", str(assignments), str(receipt),
                "fixture-key-id", "1",
            ], env=environment, capture_output=True, text=True, timeout=20)
            self.assertEqual(late_child.returncode, 4, late_child.stderr + late_child.stdout)
            self.assertIn("superseded supervisor", late_child.stderr)
            self.assertEqual(receipt.read_text(encoding="utf-8"), sentinel)


if __name__ == "__main__":
    unittest.main()

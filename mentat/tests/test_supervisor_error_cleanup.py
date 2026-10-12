"""Disposable failures after the shared pool starts must release ownership."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_company_cycle_fanout as fanout  # noqa: E402
from test_merge_company_shards import create_database  # noqa: E402


class SupervisorErrorCleanupTest(unittest.TestCase):
    def test_rejected_restart_preserves_prior_terminal_telemetry(self):
        with tempfile.TemporaryDirectory(prefix="mentat-rejected-restart-") as tmp:
            root = Path(tmp)
            source = root / "source.db"
            create_database(source)
            run = root / "run"
            run.mkdir()
            fanout.atomic_json(run / "manifest.json", {"run_id": "prior"})
            fanout.atomic_json(run / "status.json", {
                "run_id": "prior", "status": "succeeded", "completed": 1,
                "total": 1,
            })
            fanout.atomic_json(run / "workers.json", {
                "finished": True, "active_workers": 0,
            })
            before = {
                path.name: path.read_bytes()
                for path in (run / "status.json", run / "workers.json")
            }
            with self.assertRaisesRegex(RuntimeError, "already contains a fanout manifest"):
                fanout.main([
                    "--source-database", str(source), "--industry", "Fixture",
                    "--cycles", "2026", "--run-directory", str(run),
                    "--rscript-path", sys.executable,
                    "--budget-database", str(root / "budget.db"),
                ])
            self.assertEqual(
                {path.name: path.read_bytes()
                 for path in (run / "status.json", run / "workers.json")},
                before,
            )

    def test_pool_and_control_watcher_close_when_dispatch_raises(self):
        with tempfile.TemporaryDirectory(prefix="mentat-supervisor-error-") as tmp:
            root = Path(tmp)
            source = root / "source.db"
            create_database(source)
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.execute(
                    "CREATE TABLE companies(industry TEXT,company TEXT,variant TEXT)"
                )
                connection.execute(
                    "INSERT INTO companies VALUES ('Fixture','Company','COMPANY')"
                )
            run = root / "run"
            created = []
            real_pool = fanout.RequestPool

            def capture_pool(*args, **kwargs):
                pool = real_pool(*args, **kwargs)
                created.append(pool)
                return pool

            prior_threads = set(threading.enumerate())
            try:
                with mock.patch.object(fanout, "discover_keys", return_value=["synthetic-key"]), \
                     mock.patch.object(fanout, "RequestPool", side_effect=capture_pool), \
                     mock.patch.object(fanout.CompanyWorkQueue, "claim",
                                       side_effect=RuntimeError("injected dispatch failure")):
                    with self.assertRaisesRegex(RuntimeError, "injected dispatch failure"):
                        fanout.main([
                            "--source-database", str(source), "--industry", "Fixture",
                            "--cycles", "2026", "--run-directory", str(run),
                            "--rscript-path", sys.executable,
                            "--budget-database", str(root / "budget.db"),
                            "--max-parallel", "1",
                        ])
                self.assertEqual(len(created), 1)
                self.assertFalse(created[0].thread.is_alive())
                controls = [thread for thread in threading.enumerate()
                            if thread not in prior_threads
                            and thread.name == "mentat-api-control"]
                self.assertEqual(controls, [])
                workers = json.loads((run / "workers.json").read_text())
                self.assertEqual(workers["active_workers"], 0)
                self.assertEqual(workers["database_writers"], 0)
                self.assertTrue(workers["stopping"])
                self.assertFalse(fanout.read_json(run / "supervisor-owner.json")["active"])
            finally:
                for pool in created:
                    pool.close()


if __name__ == "__main__":
    unittest.main()

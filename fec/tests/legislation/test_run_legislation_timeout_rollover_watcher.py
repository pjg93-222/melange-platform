import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


FEC_DIR = Path(__file__).resolve().parents[2]
if str(FEC_DIR) not in sys.path:
    sys.path.insert(0, str(FEC_DIR))

from legislation import run_legislation_timeout_rollover_watcher as watcher


class TimeoutRolloverWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.identity = watcher.process_identity(os.getpid())
        assert self.identity is not None

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_stale_lock_is_archived_before_atomic_acquisition(self) -> None:
        stale = {
            "schema": watcher.LOCK_SCHEMA,
            "watcher_pid": 999_999_999,
            "watcher_process_identity": "stale",
        }
        lock_path = self.state / "watcher.lock.json"
        lock_path.write_text(json.dumps(stale), encoding="utf-8")
        acquired, lock, observed_path = watcher.acquire_lock(
            self.state,
            main_pid=os.getpid(),
            main_identity=self.identity,
        )
        self.assertTrue(acquired)
        self.assertEqual(lock_path, observed_path)
        self.assertTrue(watcher.lock_owned(lock_path, lock))
        self.assertEqual(
            1,
            len(list((self.state / "stale_locks").glob("*.json"))),
        )

    def test_live_lock_prevents_duplicate_watcher(self) -> None:
        acquired, lock, lock_path = watcher.acquire_lock(
            self.state,
            main_pid=os.getpid(),
            main_identity=self.identity,
        )
        self.assertTrue(acquired)
        duplicate, existing, duplicate_path = watcher.acquire_lock(
            self.state,
            main_pid=os.getpid(),
            main_identity=self.identity,
        )
        self.assertFalse(duplicate)
        self.assertEqual(lock, existing)
        self.assertEqual(lock_path, duplicate_path)

    def test_parent_exit_and_current_receipt_decisions(self) -> None:
        self.assertEqual(
            "EXIT_MAIN_PROCESS_ENDED",
            watcher.watcher_decision(
                main_alive=False,
                final_coverage_complete=False,
                selected_sha256="a" * 64,
                applied_sha256="a" * 64,
            ),
        )
        self.assertEqual(
            "EXIT_FINAL_COVERAGE_COMPLETE",
            watcher.watcher_decision(
                main_alive=True,
                final_coverage_complete=True,
                selected_sha256="a" * 64,
                applied_sha256="a" * 64,
            ),
        )
        self.assertEqual(
            "NOOP_CURRENT",
            watcher.watcher_decision(
                main_alive=True,
                final_coverage_complete=False,
                selected_sha256="a" * 64,
                applied_sha256="a" * 64,
            ),
        )
        self.assertEqual(
            "RUN_ROLLOVER",
            watcher.watcher_decision(
                main_alive=True,
                final_coverage_complete=False,
                selected_sha256="b" * 64,
                applied_sha256="a" * 64,
            ),
        )

    def test_lock_tamper_revokes_ownership(self) -> None:
        acquired, lock, lock_path = watcher.acquire_lock(
            self.state,
            main_pid=os.getpid(),
            main_identity=self.identity,
        )
        self.assertTrue(acquired)
        tampered = dict(lock)
        tampered["main_process_identity"] = "tampered"
        lock_path.write_text(
            json.dumps(tampered, sort_keys=True),
            encoding="utf-8",
        )
        self.assertFalse(watcher.lock_owned(lock_path, lock))


if __name__ == "__main__":
    unittest.main()

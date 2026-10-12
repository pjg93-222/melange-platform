"""Real process interruption around transactional source-merge publication."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
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
from validate_industry_coverage import validate_fanout_receipt  # noqa: E402
from test_merge_company_shards import create_database, seed_shard, scalar  # noqa: E402


class MergeCrashRecoveryTest(unittest.TestCase):
    def fixture(self, root: Path, count: int = 1, wal: bool = False):
        run = root / "run"
        lanes = run / "lanes"
        lanes.mkdir(parents=True)
        target = root / "source.db"
        create_database(target)
        if wal:
            with closing(sqlite3.connect(target)) as connection:
                self.assertEqual(connection.execute(
                    "PRAGMA journal_mode=WAL"
                ).fetchone()[0].lower(), "wal")
        source_before = merger.file_sha256(target)
        specs = []
        for index in range(1, count + 1):
            shard = lanes / f"lane-{index:02d}" / "mentat.db"
            shard.parent.mkdir()
            create_database(shard)
            seed_shard(shard, f"Company {index}", "Other", str(index))
            specs.append(f"{shard}::Company {index}")
        run_id = "fixture-run"
        manifest = {
            "run_id": run_id, "source_database": str(target),
            "source_state_before": source_before, "industry": "Fixture",
            "cycles": [2026],
            "lanes": [
                {"lane": index, "database": str(lanes / f"lane-{index:02d}" / "mentat.db"),
                 "companies": [f"Company {index}"],
                 "tasks": [{"task_id": f"company-{index}-2026",
                            "company": f"Company {index}", "cycle": 2026}]}
                for index in range(1, count + 1)
            ],
        }
        fanout.atomic_json(run / "manifest.json", manifest)
        prepared_path = run / "merge-prepared-fixture.json"
        tasks = [
            {"task_id": f"company-{index}-2026", "company": f"Company {index}",
             "cycle": 2026, "status": "succeeded"}
            for index in range(1, count + 1)
        ]
        fanout.atomic_json(prepared_path, {
            "run_id": run_id, "status": "merge_prepared", "industry": "Fixture",
            "source_database": str(target), "source_state_before": source_before,
            "status_path": str(run / "status.json"), "task_count": count,
            "succeeded": count, "failed": 0, "failures": [], "tasks": tasks,
            "merge": {"attempted": True, "exit_code": None},
        })
        grouped = merger.parse_specifications(specs, target)
        intent_path = run / "merge-intent-fixture.json"
        fanout.atomic_json(intent_path, {
            "version": 1, "run_id": run_id, "industry": "Fixture",
            "cycles": [2026], "source_database": str(target),
            "source_state_before": source_before,
            "prepared_receipt_path": str(prepared_path),
            "prepared_receipt_sha256": merger.file_sha256(prepared_path),
            "specifications": [
                {"database": str(path), "sha256": merger.file_sha256(path),
                 "companies": list(companies)}
                for path, companies in grouped
            ],
        })
        fanout.atomic_json(run / "status.json", {
            "status": "merging", "completed": 0, "total": count,
            "running": 1, "current": [{"company": "Company 1"}],
        })
        fanout.atomic_json(run / "workers.json", {
            "active_workers": 3, "database_writers": 1,
            "pending_companies": 2,
            "request_pool": {"active_workers": 2, "queued_requests": 1,
                             "completed_requests": 5},
        })
        fanout.atomic_json(run / "request-workers.json", {
            "active_workers": 2, "queued_requests": 1,
            "completed_requests": 5,
        })
        command = [str(target), "--expected-target-sha256", source_before,
                   "--merge-intent-json", str(intent_path),
                   "--merge-batch-limit", "2", *specs]
        return target, run, manifest, command

    @staticmethod
    def wait_ready(process: subprocess.Popen, ready: Path):
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.03)
        if not ready.exists():
            stdout, stderr = process.communicate(timeout=5)
            raise AssertionError(f"child did not reach checkpoint: {stdout} {stderr}")

    def test_process_killed_after_commit_recovers_receipt_once(self):
        with tempfile.TemporaryDirectory(prefix="mentat-merge-after-commit-") as tmp:
            root = Path(tmp)
            target, run, _, command = self.fixture(root, count=3, wal=True)
            ready = root / "after-commit"
            code = (
                "import sys,time; from pathlib import Path; "
                "sys.path.insert(0,sys.argv[1]); import merge_company_shards as m; "
                "m.main(sys.argv[3:-1]); Path(sys.argv[-1]).write_text('committed'); "
                "time.sleep(60)"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(PROJECT_ROOT / "tools"),
                 "unused", *command, str(ready)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                self.wait_ready(child, ready)
                self.assertFalse((run / "receipt.json").exists())
            finally:
                child.kill()
                child.communicate(timeout=10)
            with closing(sqlite3.connect(target)) as connection:
                before_resume = merger.logical_database_sha256(connection)
            with redirect_stdout(StringIO()):
                result = fanout.main([
                    "--source-database", str(target), "--industry", "Fixture",
                    "--cycles", "2026", "--run-directory", str(run), "--resume",
                ])
            self.assertEqual(result, 0)
            receipt = json.loads((run / "receipt.json").read_text())
            self.assertEqual(receipt["status"], "succeeded")
            self.assertTrue(receipt["merged"])
            self.assertTrue(receipt["merge"]["recovered_after_commit"])
            self.assertEqual(receipt["task_count"], 3)
            self.assertTrue(all(validate_fanout_receipt(
                receipt, target, 3,
            )["checks"].values()))
            self.assertEqual(scalar(target, "SELECT count(*) FROM contributions"), 3)
            with closing(sqlite3.connect(target)) as connection:
                self.assertEqual(merger.logical_database_sha256(connection), before_resume)
            status = json.loads((run / "status.json").read_text())
            self.assertEqual((status["completed"], status["total"], status["running"]), (3, 3, 0))
            self.assertEqual(status["current"], [])
            workers = json.loads((run / "workers.json").read_text())
            self.assertEqual((workers["active_workers"], workers["database_writers"],
                              workers["pending_companies"]), (0, 0, 0))
            self.assertEqual(workers["request_pool"]["active_workers"], 0)
            self.assertEqual(workers["request_pool"]["completed_requests"], 5)
            request_workers = json.loads((run / "request-workers.json").read_text())
            self.assertEqual((request_workers["active_workers"],
                              request_workers["queued_requests"]), (0, 0))
            self.assertTrue(request_workers["stopped"])
            with redirect_stdout(StringIO()):
                self.assertEqual(fanout.main([
                    "--source-database", str(target), "--industry", "Fixture",
                    "--cycles", "2026", "--run-directory", str(run), "--resume",
                ]), 0)
            self.assertEqual(scalar(target, "SELECT count(*) FROM contributions"), 3)

    def test_process_killed_before_commit_rolls_back_marker_and_source(self):
        with tempfile.TemporaryDirectory(prefix="mentat-merge-before-commit-") as tmp:
            root = Path(tmp)
            target, run, manifest, command = self.fixture(root)
            ready = root / "before-commit"
            code = (
                "import sys,time; from pathlib import Path; "
                "sys.path.insert(0,sys.argv[1]); import merge_company_shards as m; "
                "original=m.write_commit_marker\n"
                "def pause(*a,**k):\n"
                " result=original(*a,**k)\n"
                " Path(sys.argv[-1]).write_text('open transaction')\n"
                " time.sleep(60)\n"
                " return result\n"
                "m.write_commit_marker=pause\n"
                "m.main(sys.argv[3:-1])\n"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(PROJECT_ROOT / "tools"),
                 "unused", *command, str(ready)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                self.wait_ready(child, ready)
            finally:
                child.kill()
                child.communicate(timeout=10)
            self.assertEqual(merger.file_sha256(target), manifest["source_state_before"])
            self.assertIsNone(merger.committed_merge(target, manifest["run_id"]))
            self.assertFalse(fanout.recover_committed_fanout(
                target, run, manifest, run / "receipt.json",
                merger.file_sha256(target),
            ))
            self.assertFalse((run / "receipt.json").exists())

    def test_changed_source_after_commit_refuses_recovery(self):
        with tempfile.TemporaryDirectory(prefix="mentat-merge-changed-") as tmp:
            root = Path(tmp)
            target, run, _, command = self.fixture(root)
            with redirect_stdout(StringIO()):
                merger.main(command)
            with closing(sqlite3.connect(target)) as connection, connection:
                connection.execute(
                    "INSERT INTO contributions(sub_id,company,amount) "
                    "VALUES('later','Other',1)"
                )
            with self.assertRaisesRegex(RuntimeError, "contents changed"):
                fanout.main([
                    "--source-database", str(target), "--industry", "Fixture",
                    "--cycles", "2026", "--run-directory", str(run), "--resume",
                ])
            self.assertFalse((run / "receipt.json").exists())

    def test_external_writer_contention_rolls_back_or_refuses_changed_source(self):
        for commit_external in (False, True):
            with self.subTest(commit_external=commit_external):
                with tempfile.TemporaryDirectory(prefix="mentat-merge-contention-") as tmp:
                    root = Path(tmp)
                    target, _, _, command = self.fixture(root, count=3)
                    ready, release = root / "writer-ready", root / "writer-release"
                    writer_code = (
                        "import sqlite3,sys,time; from pathlib import Path; "
                        "c=sqlite3.connect(sys.argv[1],isolation_level=None); "
                        "c.execute('BEGIN IMMEDIATE'); "
                        "Path(sys.argv[2]).write_text('ready'); "
                        "\nwhile not Path(sys.argv[3]).exists(): time.sleep(.02)\n"
                        "if sys.argv[4]=='commit':\n"
                        " c.execute(\"INSERT INTO contributions(sub_id,company,amount) VALUES('external','Other',1)\"); c.commit()\n"
                        "else: c.rollback()\n"
                        "c.close()\n"
                    )
                    writer = subprocess.Popen(
                        [sys.executable, "-c", writer_code, str(target),
                         str(ready), str(release),
                         "commit" if commit_external else "rollback"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                    try:
                        self.wait_ready(writer, ready)
                        merger_process = subprocess.Popen(
                            [sys.executable, str(PROJECT_ROOT / "tools" / "merge_company_shards.py"),
                             *command],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        )
                        time.sleep(.25)
                        self.assertIsNone(merger_process.poll())
                    finally:
                        release.write_text("release")
                        _, writer_error = writer.communicate(timeout=10)
                    self.assertEqual(writer.returncode, 0, writer_error)
                    output, error = merger_process.communicate(timeout=30)
                    if commit_external:
                        self.assertNotEqual(merger_process.returncode, 0, output + error)
                        self.assertEqual(scalar(target, "SELECT sub_id FROM contributions"), "external")
                    else:
                        self.assertEqual(merger_process.returncode, 0, output + error)
                        self.assertEqual(scalar(target, "SELECT count(*) FROM contributions"), 3)

    def test_external_writer_at_final_staged_publication_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="mentat-merge-final-race-") as tmp:
            root = Path(tmp)
            target, _, _, command = self.fixture(root, count=3)
            stage_ready, stage_release = root / "stage-ready", root / "stage-release"
            writer_ready, writer_release = root / "writer-ready", root / "writer-release"
            merge_code = (
                "import sys,time; from pathlib import Path; "
                "sys.path.insert(0,sys.argv[1]); import merge_company_shards as m; "
                "original=m.logical_database_sha256\n"
                "def gated(c):\n"
                " result=original(c)\n"
                " path=c.execute('PRAGMA database_list').fetchone()[2]\n"
                " if '.merge-staging.' in path:\n"
                "  Path(sys.argv[2]).write_text('staged')\n"
                "  while not Path(sys.argv[3]).exists(): time.sleep(.02)\n"
                " return result\n"
                "m.logical_database_sha256=gated\n"
                "m.main(sys.argv[4:])\n"
            )
            writer_code = (
                "import sqlite3,sys,time; from pathlib import Path; "
                "c=sqlite3.connect(sys.argv[1],isolation_level=None); "
                "c.execute('BEGIN IMMEDIATE'); Path(sys.argv[2]).write_text('locked'); "
                "\nwhile not Path(sys.argv[3]).exists(): time.sleep(.02)\n"
                "c.execute(\"INSERT INTO contributions(sub_id,company,amount) VALUES('external','Other',1)\"); "
                "c.commit(); c.close()\n"
            )
            merger_process = subprocess.Popen(
                [sys.executable, "-c", merge_code, str(PROJECT_ROOT / "tools"),
                 str(stage_ready), str(stage_release), *command],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            writer = None
            try:
                self.wait_ready(merger_process, stage_ready)
                writer = subprocess.Popen(
                    [sys.executable, "-c", writer_code, str(target),
                     str(writer_ready), str(writer_release)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                self.wait_ready(writer, writer_ready)
                stage_release.write_text("go")
                time.sleep(.2)
                self.assertIsNone(merger_process.poll())
            finally:
                writer_release.write_text("go")
                stage_release.write_text("go")
                if writer is not None:
                    _, writer_error = writer.communicate(timeout=10)
                    self.assertEqual(writer.returncode, 0, writer_error)
            output, error = merger_process.communicate(timeout=30)
            self.assertNotEqual(merger_process.returncode, 0, output + error)
            self.assertEqual(scalar(target, "SELECT sub_id FROM contributions"), "external")

    def test_recovered_receipt_publication_holds_source_write_lease(self):
        with tempfile.TemporaryDirectory(prefix="mentat-receipt-lease-") as tmp:
            root = Path(tmp)
            target, run, manifest, command = self.fixture(root)
            with redirect_stdout(StringIO()):
                merger.main(command)
            source_fingerprint = fanout.quiescent_database_fingerprint(target)
            entered = threading.Event()
            release = threading.Event()
            outcome = []
            original_atomic_json = fanout.atomic_json
            receipt_path = run / "receipt.json"

            def pause_receipt(path, value):
                if path == receipt_path:
                    entered.set()
                    if not release.wait(10):
                        raise TimeoutError("receipt publication did not resume")
                return original_atomic_json(path, value)

            def recover():
                try:
                    outcome.append(fanout.recover_committed_fanout(
                        target, run, manifest, receipt_path, source_fingerprint,
                    ))
                except BaseException as error:
                    outcome.append(error)

            writer_ready = root / "writer-acquired"
            writer_code = (
                "import sqlite3,sys; from pathlib import Path; "
                "c=sqlite3.connect(sys.argv[1],timeout=10,isolation_level=None); "
                "c.execute('BEGIN IMMEDIATE'); Path(sys.argv[2]).write_text('acquired'); "
                "c.execute(\"INSERT INTO contributions(sub_id,company,amount) VALUES('later','Other',1)\"); "
                "c.commit(); c.close()"
            )
            with mock.patch.object(fanout, "atomic_json", side_effect=pause_receipt):
                thread = threading.Thread(target=recover)
                thread.start()
                self.assertTrue(entered.wait(10))
                writer = subprocess.Popen(
                    [sys.executable, "-c", writer_code,
                     str(target), str(writer_ready)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                try:
                    time.sleep(.25)
                    self.assertFalse(writer_ready.exists())
                    self.assertIsNone(writer.poll())
                finally:
                    release.set()
                    thread.join(timeout=10)
                self.assertFalse(thread.is_alive())
                self.assertEqual(outcome, [True])
                _, writer_error = writer.communicate(timeout=10)
                self.assertEqual(writer.returncode, 0, writer_error)
            self.assertTrue(writer_ready.exists())
            self.assertTrue(receipt_path.exists())
            self.assertEqual(scalar(target, "SELECT count(*) FROM contributions"), 2)

    def test_logical_digest_is_typed_order_independent_and_covers_schema(self):
        with tempfile.TemporaryDirectory(prefix="mentat-logical-hash-") as tmp:
            path = Path(tmp) / "typed.db"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE values_test (a, b)")
                rows = [(None, "x"), (None, "x"), (1, "a"),
                        (1.0, "a"), ("1", "a"), (b"1", "a")]
                connection.executemany("INSERT INTO values_test VALUES (?,?)", rows)
                first = merger.logical_database_sha256(connection)
                connection.execute("DELETE FROM values_test")
                connection.executemany("INSERT INTO values_test VALUES (?,?)", reversed(rows))
                self.assertEqual(first, merger.logical_database_sha256(connection))
                connection.execute("DELETE FROM values_test WHERE rowid=(SELECT min(rowid) FROM values_test)")
                self.assertNotEqual(first, merger.logical_database_sha256(connection))
                second = merger.logical_database_sha256(connection)
                connection.execute("CREATE INDEX values_test_a ON values_test(a)")
                self.assertNotEqual(second, merger.logical_database_sha256(connection))

    def test_merge_intent_refuses_uncheckpointed_shard_wal(self):
        with tempfile.TemporaryDirectory(prefix="mentat-shard-wal-") as tmp:
            root = Path(tmp)
            target, run, manifest, command = self.fixture(root)
            shard = run / "lanes" / "lane-01" / "mentat.db"
            reader = sqlite3.connect(shard)
            try:
                self.assertEqual(reader.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower(), "wal")
                reader.execute("BEGIN")
                reader.execute("SELECT count(*) FROM contributions").fetchone()
                with closing(sqlite3.connect(shard)) as writer, writer:
                    writer.execute(
                        "INSERT INTO contributions(sub_id,company,amount) "
                        "VALUES('late-shard','Other',1)"
                    )
                self.assertGreater(Path(f"{shard}-wal").stat().st_size, 0)
                intent_path = run / "merge-intent-fixture.json"
                intent = json.loads(intent_path.read_text())
                intent["specifications"][0]["sha256"] = merger.file_sha256(shard)
                fanout.atomic_json(intent_path, intent)
                with self.assertRaisesRegex(RuntimeError, "uncheckpointed WAL data"):
                    merger.main(command)
                self.assertEqual(merger.file_sha256(target), manifest["source_state_before"])
            finally:
                reader.close()

    def test_resume_recovers_hot_delete_journal_before_source_fingerprint(self):
        with tempfile.TemporaryDirectory(prefix="mentat-hot-journal-") as tmp:
            root = Path(tmp)
            target = root / "source.db"
            create_database(target)
            with closing(sqlite3.connect(target)) as connection, connection:
                connection.execute("CREATE TABLE spill_test (id INTEGER PRIMARY KEY, payload BLOB)")
                connection.executemany(
                    "INSERT INTO spill_test(id,payload) VALUES (?,zeroblob(4096))",
                    [(index,) for index in range(512)],
                )
            before = merger.file_sha256(target)
            run = root / "run"
            run.mkdir()
            fanout.atomic_json(run / "manifest.json", {
                "run_id": "hot-journal-fixture", "industry": "Fixture",
                "cycles": [2026], "source_database": str(target),
                "source_state_before": before,
            })
            ready = root / "spill-ready"
            code = (
                "import sqlite3,sys,time; from pathlib import Path; "
                "c=sqlite3.connect(sys.argv[1],isolation_level=None); "
                "c.execute('PRAGMA journal_mode=DELETE'); "
                "c.execute('PRAGMA cache_size=5'); "
                "c.execute('BEGIN IMMEDIATE'); "
                "c.execute('UPDATE spill_test SET payload=randomblob(4096)'); "
                "Path(sys.argv[2]).write_text('spilled'); time.sleep(60)"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(target), str(ready)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                self.wait_ready(child, ready)
            finally:
                child.kill()
                child.communicate(timeout=10)
            self.assertTrue(Path(f"{target}-journal").exists())
            self.assertNotEqual(merger.file_sha256(target), before)
            with mock.patch.object(fanout, "discover_keys",
                                   side_effect=RuntimeError("key discovery reached")):
                with self.assertRaisesRegex(RuntimeError, "key discovery reached"):
                    fanout.main([
                        "--source-database", str(target), "--industry", "Fixture",
                        "--cycles", "2026", "--run-directory", str(run), "--resume",
                    ])
            self.assertEqual(merger.file_sha256(target), before)
            self.assertIsNone(merger.committed_merge(target, "hot-journal-fixture"))


if __name__ == "__main__":
    unittest.main()

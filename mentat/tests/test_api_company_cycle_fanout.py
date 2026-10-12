from __future__ import annotations

import io
import json
import concurrent.futures
import sys
import tempfile
import threading
import time
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
import api_company_cycle_fanout as fanout  # noqa: E402
import test_merge_company_shards as merge_fixture  # noqa: E402


class ApiCompanyCycleFanoutTest(unittest.TestCase):
    def test_key_discovery_is_ordered_deduplicated_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mentat-key-fixture-") as temp:
            keys_file = Path(temp) / "keys.txt"
            keys_file.write_text(
                "# one per line\nfile-a\nfile-b\nfile-a\n",
                encoding="utf-8",
            )
            keys = fanout.discover_keys(
                keys_file,
                {
                    "FEC_API_KEYS": "pool-a;pool-b|file-b",
                    "FEC_API_KEY": "single",
                    "FEC_API_KEY_2": "numbered-b",
                    "FEC_API_KEY_1": "numbered-a",
                },
            )
        self.assertEqual(
            keys,
            [
                "file-a",
                "file-b",
                "pool-a",
                "pool-b",
                "single",
                "numbered-a",
                "numbered-b",
            ],
        )
        identifiers = [fanout.key_id(key) for key in keys]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for key, identifier in zip(keys, identifiers):
            self.assertNotIn(key, identifier)
            self.assertRegex(identifier, r"^[0-9a-f]{12}$")

    def test_missing_keys_fails_loudly(self) -> None:
        with mock.patch.object(fanout, 'load_keys', return_value=[]), self.assertRaisesRegex(RuntimeError, "No FEC API keys were found"):
            fanout.discover_keys(None, {})

    def test_encrypted_store_is_fallback_without_expanding_explicit_lane_keys(self):
        with mock.patch.object(fanout, 'load_keys', return_value=['saved-one','saved-two']):
            self.assertEqual(fanout.discover_keys(None, {}), ['saved-one','saved-two'])
            self.assertEqual(fanout.discover_keys(None, {'FEC_API_KEY':'lane-key'}), ['lane-key'])

    def test_company_cycles_never_cross_lane_ownership(self) -> None:
        companies = [
            ("Alpha", 10),
            ("Beta", 4),
            ("Gamma", 2),
            ("Delta", 1),
        ]
        first = fanout.schedule_companies(companies, (2022, 2024, 2026), 3)
        second = fanout.schedule_companies(companies, (2022, 2024, 2026), 3)
        self.assertEqual(first, second)
        owner: dict[str, int] = {}
        task_pairs: set[tuple[str, int]] = set()
        for lane in first:
            for task in lane["tasks"]:
                company = task["company"]
                owner.setdefault(company, lane["lane"])
                self.assertEqual(owner[company], lane["lane"])
                task_pairs.add((company, task["cycle"]))
        self.assertEqual(
            task_pairs,
            {
                (company, cycle)
                for company, _ in companies
                for cycle in (2022, 2024, 2026)
            },
        )

    def test_status_is_atomic_has_eta_and_contains_no_key_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mentat-status-fixture-") as temp:
            root = Path(temp)
            state = root / "lane-status.json"
            fanout.atomic_json(
                state,
                {
                    "status": "running",
                    "lane": 1,
                    "completed": 1,
                    "failed": 0,
                    "current_company": "Beta",
                    "current_cycle": 2024,
                    "tasks": [
                        {
                            "task_id": "alpha-2022",
                            "status": "succeeded",
                            "wall_clock_seconds": 10,
                        }
                    ],
                },
            )
            lane_run = fanout.LaneRun(
                lane=1,
                database=root / "lane.db",
                assignments=root / "assignments.json",
                receipt=root / "receipt.json",
                state=state,
                log=root / "process.log",
                key="must-not-appear",
                key_id="0123456789ab",
                tasks=(
                    {"task_id": "alpha-2022", "company": "Alpha", "cycle": 2022},
                    {"task_id": "beta-2024", "company": "Beta", "cycle": 2024},
                ),
                attempt=1,
            )
            status = root / "status.json"
            fanout.write_status(
                status,
                run_id="fixture",
                industry="Fintech",
                total=2,
                lane_runs=[lane_run],
                stage="collecting",
                started_monotonic=fanout.time.monotonic() - 5,
            )
            payload = json.loads(status.read_text(encoding="utf-8"))
        self.assertEqual(payload["completed"], 1)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["running"], 1)
        self.assertEqual(payload["failed"], 0)
        self.assertEqual(payload["estimated_remaining_seconds"], 10)
        self.assertEqual(
            payload["current"],
            [{"lane": 1, "company": "Beta", "cycle": 2024}],
        )
        serialized = json.dumps(payload)
        self.assertNotIn("key", serialized.lower())
        self.assertNotIn("must-not-appear", serialized)

    @unittest.skipUnless(
        sys.platform == "win32",
        "Windows delete-sharing behavior is required",
    )
    def test_locked_status_is_nonfatal_but_locked_receipt_raises(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="mentat-status-lock-fixture-"
        ) as temp:
            root = Path(temp)
            status = root / "status.json"
            status.write_text('{"status":"prior"}\n', encoding="utf-8")
            warning = io.StringIO()
            with status.open("r", encoding="utf-8"), mock.patch.object(
                fanout,
                "ATOMIC_REPLACE_INITIAL_DELAY_SECONDS",
                0.00001,
            ), mock.patch.object(
                fanout.sys,
                "stderr",
                warning,
            ), mock.patch.object(
                fanout.os,
                "replace",
                wraps=fanout.os.replace,
            ) as replace:
                written = fanout.write_status(
                    status,
                    run_id="fixture",
                    industry="Fintech",
                    total=0,
                    lane_runs=[],
                    stage="collecting",
                    started_monotonic=fanout.time.monotonic(),
                )
            self.assertFalse(written)
            self.assertEqual(
                replace.call_count,
                fanout.ATOMIC_REPLACE_ATTEMPTS,
            )
            self.assertIn(
                "continuing collection",
                warning.getvalue(),
            )
            self.assertEqual(
                json.loads(status.read_text(encoding="utf-8")),
                {"status": "prior"},
            )

            receipt = root / "receipt.json"
            receipt.write_text('{"status":"prior"}\n', encoding="utf-8")
            with receipt.open("r", encoding="utf-8"), mock.patch.object(
                fanout,
                "ATOMIC_REPLACE_INITIAL_DELAY_SECONDS",
                0.00001,
            ):
                with self.assertRaises(PermissionError):
                    fanout.atomic_json(receipt, {"status": "replacement"})
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8")),
                {"status": "prior"},
            )
            self.assertFalse(
                receipt.with_name(
                    f".{receipt.name}.{fanout.os.getpid()}.pending"
                ).exists()
            )

    def test_lane_key_is_environment_only_and_budget_id_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mentat-lane-fixture-") as temp:
            root = Path(temp)
            assignments = root / "assignments.json"
            receipt = root / "receipt.json"
            fanout.atomic_json(
                assignments,
                {
                    "industry": "Fintech",
                    "lane": 1,
                    "tasks": [
                        {
                            "task_id": "alpha-2022",
                            "company": "Alpha",
                            "cycle": 2022,
                        }
                    ],
                },
            )
            secret = "NEVER-LOG-THIS-FEC-KEY"
            identifier = fanout.key_id(secret)
            lane_run = fanout.LaneRun(
                lane=1,
                database=root / "lane.db",
                assignments=assignments,
                receipt=receipt,
                state=root / "status.json",
                log=root / "process.log",
                key=secret,
                key_id=identifier,
                tasks=(
                    {"task_id": "alpha-2022", "company": "Alpha", "cycle": 2022},
                ),
                attempt=1,
            )

            def fake_run(command, **kwargs):
                self.assertNotIn(secret, command)
                self.assertEqual(kwargs["env"]["FEC_API_KEY"], secret)
                self.assertEqual(
                    kwargs["env"]["MENTAT_API_BUDGET_ID"],
                    f"openfec:sha256:{identifier}",
                )
                self.assertNotIn("FEC_API_KEYS", kwargs["env"])
                fanout.atomic_json(
                    receipt,
                    {
                        "status": "succeeded",
                        "tasks": [
                            {
                                "task_id": "alpha-2022",
                                "company": "Alpha",
                                "cycle": 2022,
                                "status": "succeeded",
                            }
                        ],
                    },
                )
                return mock.Mock(returncode=0)

            with mock.patch.object(fanout.subprocess, "run", side_effect=fake_run):
                result = fanout.execute_lane(
                    lane_run,
                    rscript_path=root / "Rscript.exe",
                    lane_script=root / "project" / "tools" / "collect_api_lane.R",
                    r_library_path=None,
                    concurrency=8,
                    sqlite_cache_mb=256,
                    budget_database=root / "persistent-budget.sqlite",
                )
            log_text = lane_run.log.read_text(encoding="utf-8")
        self.assertEqual(result["exit_code"], 0)
        self.assertNotIn(secret, log_text)
        self.assertIn(identifier, log_text)

    def test_injected_native_crash_writes_complete_invocation_telemetry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mentat-crash-fixture-") as temp:
            root = Path(temp)
            assignments = root / "assignments.json"
            receipt = root / "receipt.json"
            task = {
                "task_id": "stripe-2026",
                "company": "Stripe",
                "cycle": 2026,
            }
            fanout.atomic_json(
                assignments,
                {
                    "industry": "Fintech",
                    "lane": 1,
                    "tasks": [task],
                },
            )
            lane_run = fanout.LaneRun(
                lane=1,
                database=root / "lane.db",
                assignments=assignments,
                receipt=receipt,
                state=root / "status.json",
                log=root / "process.log",
                key="fixture-key",
                key_id=fanout.key_id("fixture-key"),
                tasks=(task,),
                attempt=1,
            )

            def injected_crash(command, **kwargs):
                kwargs["stdout"].write(
                    "FEC export downloaded and verified: 15907 rows.\n"
                )
                kwargs["stdout"].flush()
                return mock.Mock(returncode=-1)

            with mock.patch.object(
                fanout.subprocess,
                "run",
                side_effect=injected_crash,
            ):
                result = fanout.execute_lane(
                    lane_run,
                    rscript_path=root / "Rscript.exe",
                    lane_script=root / "project" / "tools" / "collect_api_lane.R",
                    r_library_path=None,
                    concurrency=8,
                    sqlite_cache_mb=256,
                    budget_database=root / "budget.sqlite",
                )

            task_receipt = json.loads(receipt.read_text(encoding="utf-8"))
            invocation = json.loads(
                Path(result["invocation_receipt_path"]).read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(result["exit_code"], -1)
        self.assertEqual(task_receipt["failed"], 1)
        self.assertEqual(task_receipt["tasks"][0]["company"], "Stripe")
        self.assertEqual(task_receipt["tasks"][0]["cycle"], 2026)
        self.assertEqual(invocation["exit_code"], -1)
        self.assertGreaterEqual(invocation["wall_clock_seconds"], 0)
        self.assertEqual(invocation["units"], [task])
        self.assertEqual(
            invocation["last_log_line"],
            "FEC export downloaded and verified: 15907 rows.",
        )

    def test_resource_cap_honors_cpu_ram_keys_and_merge_limit(self) -> None:
        with mock.patch.object(
            fanout, "available_memory_bytes", return_value=6 * 1024**3
        ), mock.patch.object(fanout.os, "cpu_count", return_value=8):
            cap = fanout.resource_cap(
                key_count=12,
                company_count=20,
                requested_parallel=0,
                memory_per_lane_mb=640,
                memory_reserve_mb=1024,
            )
        self.assertEqual(cap["memory_lane_cap"], 8)
        self.assertEqual(cap["active_lane_cap"], 8)
        self.assertEqual(cap["sqlite_merge_cap"], 10)
        self.assertFalse(cap["cpu_bound_lane_cap_applied"])

    def test_resource_cap_allows_one_io_lane_per_company_past_cpu_count(
        self,
    ) -> None:
        with mock.patch.object(
            fanout, "available_memory_bytes", return_value=32 * 1024**3
        ), mock.patch.object(fanout.os, "cpu_count", return_value=8):
            cap = fanout.resource_cap(
                key_count=10,
                company_count=10,
                requested_parallel=10,
                memory_per_lane_mb=640,
                memory_reserve_mb=1024,
            )
        self.assertEqual(cap["logical_cpu"], 8)
        self.assertEqual(cap["active_lane_cap"], 10)
        self.assertFalse(cap["cpu_bound_lane_cap_applied"])

    def test_resume_execution_throttles_persisted_lanes_to_live_cap(
        self,
    ) -> None:
        self.assertEqual(
            fanout.execution_worker_cap(
                10,
                {"active_lane_cap": 2},
            ),
            2,
        )

    def test_primary_and_recovery_share_one_execution_cap(self) -> None:
        original_execute = fanout.execute_lane
        lock = threading.Lock()
        active = 0
        maximum = 0

        def fake_execute(lane_run, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return {"lane": lane_run}

        fanout.execute_lane = fake_execute
        try:
            limiter = fanout.LaneLimiter(2)
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                futures = [
                    pool.submit(
                        fanout.execute_lane_with_limit,
                        limiter,
                        fanout.LaneRun(lane, Path(f'lane-{lane}.db'), Path('assignments'),
                            Path('receipt'), Path('state'), Path('log'), f'key-{lane}', f'id-{lane}', (), 1),
                    )
                    for lane in range(3)
                ]
                for future in futures:
                    future.result()
        finally:
            fanout.execute_lane = original_execute
        self.assertEqual(maximum, 2)

    def test_resume_receipts_select_only_non_success_tasks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mentat-resume-fixture-") as temp:
            lane = Path(temp)
            fanout.atomic_json(
                lane / "receipt.json",
                {
                    "status": "failed",
                    "quick_check": "ok",
                    "tasks": [
                        {"task_id": "alpha-2022", "status": "succeeded"},
                        {"task_id": "alpha-2024", "status": "failed"},
                    ],
                },
            )
            fanout.atomic_json(
                lane / "receipt.recovery-1.json",
                {
                    "status": "succeeded",
                    "quick_check": "ok",
                    "tasks": [
                        {"task_id": "alpha-2024", "status": "succeeded"},
                    ],
                },
            )
            fanout.atomic_json(
                lane / "receipt.recovery-1.invocation.json",
                {
                    "attempt": 2,
                    "exit_code": 0,
                    "units": [{"task_id": "alpha-2024"}],
                },
            )
            attempts = fanout.existing_lane_attempts(lane, 1)
            latest = fanout.latest_tasks_from_attempts(attempts)
        self.assertEqual([attempt["attempt"] for attempt in attempts], [1, 2])
        self.assertEqual(latest["alpha-2022"]["status"], "succeeded")
        self.assertEqual(latest["alpha-2024"]["status"], "succeeded")
        planned = [
            {"task_id": "alpha-2022"},
            {"task_id": "alpha-2024"},
            {"task_id": "alpha-2026"},
        ]
        pending = [
            task
            for task in planned
            if latest.get(task["task_id"], {}).get("status") != "succeeded"
        ]
        self.assertEqual(pending, [{"task_id": "alpha-2026"}])

    def test_resume_rejects_unverified_raw_r_success_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mentat-r-postflight-") as temp:
            lane = Path(temp)
            path = lane / "receipt.json"
            task = {"task_id": "alpha-2024", "status": "succeeded"}
            fanout.atomic_json(
                path,
                {"status": "succeeded", "quick_check": "corrupt", "tasks": [task]},
            )
            attempts = fanout.existing_lane_attempts(lane, 1)
            latest = fanout.latest_tasks_from_attempts(attempts)
            self.assertEqual(latest["alpha-2024"]["status"], "failed")
            self.assertEqual(fanout.read_json(path)["tasks"], [task])

            # An R receipt with a passing postflight is sufficient if Python
            # died before it could append exit_code to that receipt.
            fanout.atomic_json(
                path,
                {"status": "succeeded", "quick_check": "ok", "tasks": [task]},
            )
            latest = fanout.latest_tasks_from_attempts(
                fanout.existing_lane_attempts(lane, 1)
            )
            self.assertEqual(latest["alpha-2024"]["status"], "succeeded")

            # A later Python receipt with a failed process exit cannot certify
            # a succeeded task, regardless of its earlier postflight result.
            fanout.atomic_json(
                path,
                {"status": "succeeded", "quick_check": "ok", "exit_code": 3,
                 "tasks": [task]},
            )
            latest = fanout.latest_tasks_from_attempts(
                fanout.existing_lane_attempts(lane, 1)
            )
            self.assertEqual(latest["alpha-2024"]["status"], "failed")

    def test_disposable_end_to_end_merge_and_restart_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mentat-fanout-e2e-") as temp:
            root = Path(temp)
            source = root / "source.db"
            merge_fixture.create_database(source)
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.execute(
                    """
                    CREATE TABLE companies (
                      industry TEXT NOT NULL,
                      company TEXT NOT NULL,
                      variant TEXT NOT NULL
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO companies VALUES ('Fintech', ?, ?)",
                    [("Alpha", "ALPHA"), ("Beta", "BETA")],
                )
            run_directory = root / "run"
            receipt_path = run_directory / "receipt.json"

            def fake_execute(lane_run, **kwargs):
                with closing(
                    sqlite3.connect(lane_run.database)
                ) as connection, connection:
                    for task in lane_run.tasks:
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO contributions
                              (sub_id, company, amount)
                            VALUES (?, ?, ?)
                            """,
                            (
                                task["task_id"],
                                task["company"],
                                float(task["cycle"]),
                            ),
                        )
                tasks = [
                    {**task, "status": "succeeded", "wall_clock_seconds": 0.01}
                    for task in lane_run.tasks
                ]
                lane_receipt = {
                    "status": "succeeded",
                    "key_id": lane_run.key_id,
                    "wall_clock_seconds": 0.01,
                    "tasks": tasks,
                }
                fanout.atomic_json(lane_run.receipt, lane_receipt)
                return {
                    "lane": lane_run.lane,
                    "attempt": lane_run.attempt,
                    "exit_code": 0,
                    "wall_clock_seconds": 0.01,
                    "receipt_path": str(lane_run.receipt),
                    "state_path": str(lane_run.state),
                    "log_path": str(lane_run.log),
                    "database": str(lane_run.database),
                    "key_id": lane_run.key_id,
                    "receipt": lane_receipt,
                }

            arguments = [
                "--source-database",
                str(source),
                "--industry",
                "Fintech",
                "--cycles",
                "2022",
                "--run-directory",
                str(run_directory),
                "--output-receipt",
                str(receipt_path),
                "--rscript-path",
                sys.executable,
                "--budget-database",
                str(root / "budget.sqlite"),
                "--max-parallel",
                "2",
            ]
            with mock.patch.dict(
                fanout.os.environ,
                {"FEC_API_KEYS": "fixture-key-a|fixture-key-b"},
                clear=False,
            ), mock.patch.object(
                fanout, "execute_lane", side_effect=fake_execute
            ):
                self.assertEqual(fanout.main(arguments), 0)
                with self.assertRaisesRegex(
                    RuntimeError,
                    "refusing to recreate lane snapshots",
                ):
                    fanout.main(arguments)
                self.assertEqual(fanout.main([*arguments, "--resume"]), 0)

            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            with closing(sqlite3.connect(source)) as connection, connection:
                contribution_count = connection.execute(
                    "SELECT COUNT(*) FROM contributions"
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO contributions (sub_id, company, amount)
                    VALUES ('same-path-mutation', 'Alpha', 1)
                    """
                )
            with self.assertRaisesRegex(
                RuntimeError,
                "does not match the current same-path source database",
            ):
                fanout.main([*arguments, "--resume"])
            manifest = json.loads(
                (run_directory / "manifest.json").read_text(encoding="utf-8")
            )
        self.assertEqual(receipt["status"], "succeeded")
        self.assertTrue(receipt["merged"])
        self.assertEqual(receipt["succeeded"], 2)
        self.assertEqual(contribution_count, 2)
        self.assertEqual(
            Path(receipt["runtimes"]["python_path"]).resolve(),
            Path(sys.executable).resolve(),
        )
        self.assertEqual(receipt["runtimes"], manifest["runtimes"])
        self.assertEqual(receipt["telemetry"]["status_write_failures"], 0)
        self.assertEqual(manifest["company_order"], ["Alpha", "Beta"])
        self.assertEqual(
            receipt["merge"]["attribution_conflicts_recorded"],
            0,
        )

    def test_resume_tolerates_fewer_keys_than_the_manifest_planned_lanes(self) -> None:
        # Keys are quarantined, rotated or disabled between attempts. Refusing to
        # resume on a short key list threw away every company-cycle the stopped
        # attempt had already verified. Lanes now share the remaining keys, and
        # the "key:" resource each lane holds keeps two of them off one key at
        # the same time.
        with tempfile.TemporaryDirectory(prefix="mentat-resume-keys-") as temp:
            root = Path(temp)
            source = root / "source.db"
            merge_fixture.create_database(source)
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.execute(
                    "CREATE TABLE companies (industry TEXT NOT NULL, "
                    "company TEXT NOT NULL, variant TEXT NOT NULL)"
                )
                connection.executemany(
                    "INSERT INTO companies VALUES ('Fintech', ?, ?)",
                    [("Alpha", "ALPHA"), ("Beta", "BETA")],
                )
            run_directory = root / "run"
            receipt_path = run_directory / "receipt.json"
            state = {"fail_beta": True}
            guard = threading.Lock()
            live: dict[str, int] = {}
            shared_key_overlap: list[str] = []
            observed_keys: list[str] = []
            executed: list[tuple[str, int]] = []

            def fake_execute(lane_run, **kwargs):
                with guard:
                    observed_keys.append(lane_run.key)
                    live[lane_run.key] = live.get(lane_run.key, 0) + 1
                    if live[lane_run.key] > 1:
                        shared_key_overlap.append(lane_run.key)
                try:
                    tasks = []
                    with closing(
                        sqlite3.connect(lane_run.database)
                    ) as connection, connection:
                        for task in lane_run.tasks:
                            broken = (
                                state["fail_beta"] and task["company"] == "Beta"
                            )
                            if not broken:
                                executed.append((task["company"], task["cycle"]))
                                connection.execute(
                                    "INSERT OR IGNORE INTO contributions "
                                    "(sub_id, company, amount) VALUES (?, ?, ?)",
                                    (
                                        task["task_id"],
                                        task["company"],
                                        float(task["cycle"]),
                                    ),
                                )
                            tasks.append(
                                {
                                    **task,
                                    "status": "failed" if broken else "succeeded",
                                    "wall_clock_seconds": 0.01,
                                }
                            )
                finally:
                    with guard:
                        live[lane_run.key] -= 1
                failed = [task for task in tasks if task["status"] != "succeeded"]
                lane_receipt = {
                    "status": "failed" if failed else "succeeded",
                    "key_id": lane_run.key_id,
                    "wall_clock_seconds": 0.01,
                    "quick_check": "ok",
                    "tasks": tasks,
                }
                fanout.atomic_json(lane_run.receipt, lane_receipt)
                return {
                    "lane": lane_run.lane,
                    "attempt": lane_run.attempt,
                    "exit_code": 1 if failed else 0,
                    "wall_clock_seconds": 0.01,
                    "receipt_path": str(lane_run.receipt),
                    "state_path": str(lane_run.state),
                    "log_path": str(lane_run.log),
                    "database": str(lane_run.database),
                    "key_id": lane_run.key_id,
                    "receipt": lane_receipt,
                }

            arguments = [
                "--source-database",
                str(source),
                "--industry",
                "Fintech",
                "--cycles",
                "2022",
                "--run-directory",
                str(run_directory),
                "--output-receipt",
                str(receipt_path),
                "--rscript-path",
                sys.executable,
                "--budget-database",
                str(root / "budget.sqlite"),
                "--max-parallel",
                "2",
            ]
            planned = ["fixture-key-a", "fixture-key-b"]
            with mock.patch.object(
                fanout, "execute_lane", side_effect=fake_execute
            ), mock.patch.object(
                fanout,
                "discover_keys",
                side_effect=lambda keys_file, environment: list(planned),
            ):
                # Beta fails every rung of the ladder, so nothing is merged.
                self.assertEqual(fanout.main(arguments), 2)
                stopped = json.loads(
                    receipt_path.read_text(encoding="utf-8")
                )
                manifest = json.loads(
                    (run_directory / "manifest.json").read_text(encoding="utf-8")
                )
                # One of the two planned keys is gone before the resume.
                planned[:] = ["fixture-key-a"]
                state["fail_beta"] = False
                observed_keys.clear()
                executed.clear()
                self.assertEqual(fanout.main([*arguments, "--resume"]), 0)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            with closing(sqlite3.connect(source)) as connection:
                merged_rows = connection.execute(
                    "SELECT company FROM contributions ORDER BY company"
                ).fetchall()
        self.assertEqual(manifest["lane_count"], 2)
        self.assertFalse(stopped["merged"])
        # The resume ran on the surviving key alone and never doubled up on it.
        self.assertEqual(set(observed_keys), {"fixture-key-a"})
        self.assertEqual(shared_key_overlap, [])
        # Alpha succeeded before the stop and is not collected a second time.
        self.assertEqual(executed, [("Beta", 2022)])
        self.assertEqual(receipt["status"], "succeeded")
        self.assertTrue(receipt["merged"])
        self.assertEqual(receipt["configured_key_count"], 1)
        self.assertEqual([row[0] for row in merged_rows], ["Alpha", "Beta"])

    def test_failure_ladder_retries_then_splits_company_cycle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mentat-ladder-e2e-") as temp:
            root = Path(temp)
            source = root / "source.db"
            merge_fixture.create_database(source)
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.execute(
                    """
                    CREATE TABLE companies (
                      industry TEXT NOT NULL,
                      company TEXT NOT NULL,
                      variant TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO companies VALUES ('Fintech', 'Stripe', 'Stripe')"
                )
            run_directory = root / "run"
            receipt_path = run_directory / "receipt.json"
            executed: list[tuple[int, tuple[str, ...]]] = []
            assignment_resumes: list[bool] = []

            def fake_execute(lane_run, **kwargs):
                assignment_resumes.append(
                    bool(fanout.read_json(lane_run.assignments).get("resume"))
                )
                task_ids = tuple(
                    str(task["task_id"]) for task in lane_run.tasks
                )
                executed.append((lane_run.attempt, task_ids))
                succeeded = lane_run.attempt >= 3
                if succeeded:
                    with closing(
                        sqlite3.connect(lane_run.database)
                    ) as connection, connection:
                        for task in lane_run.tasks:
                            connection.execute(
                                """
                                INSERT OR IGNORE INTO contributions
                                  (sub_id, company, amount)
                                VALUES (?, ?, ?)
                                """,
                                (
                                    task["task_id"],
                                    task["company"],
                                    float(task["cycle"]),
                                ),
                            )
                tasks = [
                    {
                        **task,
                        "status": "succeeded" if succeeded else "failed",
                        "wall_clock_seconds": 0.01,
                        "error": "" if succeeded else "injected exit -1",
                    }
                    for task in lane_run.tasks
                ]
                lane_receipt = {
                    "status": "succeeded" if succeeded else "failed",
                    "tasks": tasks,
                }
                fanout.atomic_json(lane_run.receipt, lane_receipt)
                return {
                    "lane": lane_run.lane,
                    "attempt": lane_run.attempt,
                    "exit_code": 0 if succeeded else -1,
                    "wall_clock_seconds": 0.01,
                    "last_log_line": (
                        "TASK SUCCEEDED" if succeeded else "injected exit -1"
                    ),
                    "receipt_path": str(lane_run.receipt),
                    "invocation_receipt_path": str(
                        lane_run.receipt.with_suffix(".invocation.json")
                    ),
                    "state_path": str(lane_run.state),
                    "log_path": str(lane_run.log),
                    "database": str(lane_run.database),
                    "key_id": lane_run.key_id,
                    "units": list(lane_run.tasks),
                    "receipt": lane_receipt,
                }

            arguments = [
                "--source-database",
                str(source),
                "--industry",
                "Fintech",
                "--cycles",
                "2026",
                "--run-directory",
                str(run_directory),
                "--output-receipt",
                str(receipt_path),
                "--rscript-path",
                sys.executable,
                "--budget-database",
                str(root / "budget.sqlite"),
                "--max-parallel",
                "1",
            ]
            with mock.patch.dict(
                fanout.os.environ,
                {"FEC_API_KEY": "fixture-key"},
                clear=False,
            ), mock.patch.object(
                fanout,
                "execute_lane",
                side_effect=fake_execute,
            ):
                self.assertEqual(fanout.main(arguments), 0)

            final = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(executed, [
            (1, ("stripe-2026",)),
            (2, ("stripe-2026",)),
            (3, ("stripe-2026",)),
        ])
        self.assertEqual(assignment_resumes, [False, True, True])
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(
            [attempt["exit_code"] for attempt in final["lane_attempts"]],
            [-1, -1, 0],
        )
        self.assertTrue(final["merged"])


if __name__ == "__main__":
    unittest.main()

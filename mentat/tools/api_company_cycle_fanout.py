"""Run one industry's FEC API collection across private SQLite key lanes.

The initialized source database is never modified until every company-cycle
task succeeds. Each company belongs to exactly one lane (the merge invariant),
all cycles for that company run sequentially in that lane. HTTP requests share
a pool of independently leased API keys across lanes while each lane retains
one database writer. A failed lane is retried in a fresh R process;
remaining failures are then split into one fresh process per company-cycle.
Keys are supplied only through child-process environments and are never
written to manifests, receipts, command lines, or logs. On complete success,
the final deterministic merge writes the source database in place; on any
collection/recovery failure it remains untouched.
"""

from __future__ import annotations

import atexit
import argparse
import concurrent.futures
import copy
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlite_snapshot import create_snapshot, verify_database
from fec_key_store import load_keys
from mentat_env import seam
from api_request_pool import RequestPool
from merge_company_shards import (
    committed_merge_lease, content_sha256, file_sha256, merge_lock_path,
    parse_specifications,
)


SUPPORTED_CYCLES = (2022, 2024, 2026)
MERGE_ATTACHMENT_LIMIT = 10
DEFAULT_MEMORY_PER_LANE_MB = 640
DEFAULT_MEMORY_RESERVE_MB = 1024
ATOMIC_REPLACE_ATTEMPTS = 10
ATOMIC_REPLACE_INITIAL_DELAY_SECONDS = 0.002


@contextmanager
def supervisor_lock(run_directory: Path):
    """Fence another supervisor from the same run, including on resume.

    SQLite's writer lock is released by the OS if this process dies. The
    separate owner receipt survives a crash, while the transaction prevents
    two live coordinators from dispatching
    against the same lane databases and checkpoint files.
    """
    run_directory.mkdir(parents=True, exist_ok=True)
    path = run_directory / ".supervisor-lock.sqlite"
    owner_path = run_directory / "supervisor-owner.json"
    connection = sqlite3.connect(path, timeout=0, isolation_level=None)
    token = secrets.token_hex(16)
    acquired = False
    owner_written = False
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower():
                raise RuntimeError(
                    f"Another fanout supervisor owns this run directory: {run_directory}"
                ) from error
            raise
        acquired = True
        atomic_json(owner_path, {
            "token": token, "pid": os.getpid(),
            "started_at": utc_now(), "active": True,
        })
        owner_written = True
        yield token
    finally:
        try:
            if acquired:
                if owner_written:
                    atomic_json(owner_path, {
                        "token": token, "pid": os.getpid(),
                        "ended_at": utc_now(), "active": False,
                    })
                connection.rollback()
        finally:
            connection.close()


def assert_no_active_lane_writers(run_directory: Path) -> None:
    """Refuse a restart while an orphaned R child owns a lane snapshot.

    This runs before the coordinator rewrites assignments, lane pointers or
    aggregate status. A second check in R protects the database if a child
    acquires its lock after this probe.
    """
    for path in sorted((run_directory / "lanes").glob(
        "lane-*/mentat.db.writer-lock.sqlite"
    )):
        connection = sqlite3.connect(path, timeout=0, isolation_level=None)
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if "locked" in str(error).lower():
                    raise RuntimeError(
                        f"An R lane writer still owns {path.parent / 'mentat.db'}; "
                        "resume after that process exits."
                    ) from error
                raise
            connection.rollback()
        finally:
            connection.close()


def assert_no_active_merge_writer(source_database: Path) -> None:
    """Refuse takeover while an orphaned merge child can still write source."""
    path = merge_lock_path(source_database)
    if not path.exists():
        return
    connection = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower():
                raise RuntimeError(
                    f"A merge child still owns source database {source_database}; "
                    "resume after that process exits."
                ) from error
            raise
        connection.rollback()
    finally:
        connection.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for attempt in range(ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(pending, path)
            return
        except PermissionError:
            if attempt + 1 >= ATOMIC_REPLACE_ATTEMPTS:
                pending.unlink(missing_ok=True)
                raise
            time.sleep(
                ATOMIC_REPLACE_INITIAL_DELAY_SECONDS * (2**attempt)
            )


def quiescent_database_fingerprint(path: Path) -> str:
    resolved = path.resolve(strict=True)
    wal = Path(f"{resolved}-wal")
    if wal.is_file() and wal.stat().st_size > 0:
        raise RuntimeError(
            "Source database has a nonempty WAL sidecar; stop writers and "
            f"checkpoint SQLite before fanout: {wal}"
        )
    digest = hashlib.sha256()
    with resolved.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_keys(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[\r\n,;|]+", value)
        if item.strip()
    ]


def discover_keys(keys_file: Path | None, environment: dict[str, str]) -> list[str]:
    values: list[str] = []
    if keys_file is not None:
        for line in keys_file.read_text(encoding="utf-8-sig").splitlines():
            clean = line.strip()
            if clean and not clean.startswith("#"):
                values.extend(split_keys(clean))
    values.extend(split_keys(environment.get("FEC_API_KEYS", "")))
    single = environment.get("FEC_API_KEY", "").strip()
    if single:
        values.append(single)
    numbered = sorted(
        (
            (int(match.group(1)), value.strip())
            for name, value in environment.items()
            if (match := re.fullmatch(r"FEC_API_KEY_(\d+)", name))
            and value.strip()
        ),
        key=lambda item: item[0],
    )
    values.extend(value for _, value in numbered)
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    if not unique:
        unique = load_keys()
    if not unique:
        raise RuntimeError(
            "No FEC API keys were found. Set FEC_API_KEY, set delimiter-separated "
            "FEC_API_KEYS, or pass --keys-file. Key values are never logged."
        )
    return unique


def key_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def available_memory_bytes() -> int:
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.available_physical)
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
        return int(page_size * available_pages)
    except (AttributeError, OSError, ValueError):
        return 0


def parse_cycles(value: str) -> tuple[int, ...]:
    labels = [item.strip() for item in re.split(r"[,|]+", value) if item.strip()]
    cycle_map = {
        "2021-2022": 2022,
        "2022": 2022,
        "2023-2024": 2024,
        "2024": 2024,
        "2025-2026": 2026,
        "2026": 2026,
    }
    unknown = [label for label in labels if label not in cycle_map]
    if not labels or unknown:
        raise argparse.ArgumentTypeError(
            "cycles must be a delimited subset of 2022, 2024, 2026 "
            f"(invalid: {','.join(unknown)})"
        )
    return tuple(sorted({cycle_map[label] for label in labels}))


def database_companies(database: Path, industry: str) -> list[tuple[str, int]]:
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=30,
    )
    try:
        rows = list(
            connection.execute(
                """
                SELECT company, COUNT(*) AS variants
                FROM companies
                WHERE industry=?
                GROUP BY company
                ORDER BY company COLLATE NOCASE, company
                """,
                (industry,),
            )
        )
    finally:
        connection.close()
    if not rows:
        raise RuntimeError(
            f"No companies were found for industry {industry!r} in {database}."
        )
    return [(str(company), int(variants)) for company, variants in rows]


def company_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "company"


def task_id(company: str, cycle: int) -> str:
    return f"{company_slug(company)}-{cycle}"


def resource_cap(
    *,
    key_count: int,
    company_count: int,
    requested_parallel: int,
    memory_per_lane_mb: int,
    memory_reserve_mb: int,
    merge_batch_limit: int = MERGE_ATTACHMENT_LIMIT,
    lane_ceiling: int = 0,
) -> dict[str, int]:
    cpu_count = max(1, os.cpu_count() or 1)
    available = available_memory_bytes()
    available_mb = available // (1024 * 1024) if available else 0
    requested = requested_parallel if requested_parallel > 0 else max(key_count, 1)
    # A failed memory probe must not silently strand the run on a single lane.
    # Treat an unreadable probe as "unknown, do not constrain" and record it.
    memory_probe_failed = not available_mb
    memory_cap = (
        max(1, (available_mb - memory_reserve_mb) // memory_per_lane_mb)
        if available_mb
        else requested
    )
    # A lane is primarily remote-API and SQLite-I/O bound. Keep CPU count as
    # telemetry, but do not strand a dedicated key/company pair merely because
    # there are fewer logical CPUs than independently rate-limited API keys.
    # merge_company_shards batches its attachments, so the ten-shard SQLite
    # limit is no longer a ceiling on lane count.
    limits = [requested, key_count, company_count, memory_cap]
    if lane_ceiling > 0:
        limits.append(lane_ceiling)
    cap = max(1, min(limits))
    return {
        "requested_parallel": requested_parallel,
        "logical_cpu": cpu_count,
        "cpu_bound_lane_cap_applied": False,
        "available_memory_mb": int(available_mb),
        "memory_probe_failed": memory_probe_failed,
        "memory_per_lane_mb": memory_per_lane_mb,
        "memory_reserve_mb": memory_reserve_mb,
        "memory_lane_cap": int(memory_cap),
        "key_cap": key_count,
        "company_cap": company_count,
        "lane_ceiling": lane_ceiling,
        "sqlite_merge_cap": merge_batch_limit,
        "active_lane_cap": cap,
    }


class LaneLimiter:
    """Resizable concurrency gate so a running fanout can gain or shed lanes."""

    def __init__(self, permits: int) -> None:
        self.condition = threading.Condition()
        self.limit = max(1, int(permits))
        self.held = 0
        self.stopped = False
        self.resources: set[str] = set()

    def resize(self, permits: int) -> None:
        with self.condition:
            self.limit = max(1, int(permits))
            self.condition.notify_all()

    def stop(self) -> None:
        """Refuse every further permit. Lanes already holding one finish."""
        with self.condition:
            self.stopped = True
            self.condition.notify_all()

    def active(self) -> int:
        with self.condition:
            return self.held

    def target(self) -> int:
        with self.condition:
            return self.limit

    def acquire(self, resources: Sequence[str] = ()) -> bool:
        """Block for a permit. Returns False once the operator has stopped."""
        with self.condition:
            while not self.stopped and (
                self.held >= self.limit or self.resources.intersection(resources)
            ):
                self.condition.wait()
            if self.stopped:
                return False
            self.held += 1
            self.resources.update(resources)
            return True

    def release(self, resources: Sequence[str] = ()) -> None:
        with self.condition:
            self.held = max(0, self.held - 1)
            self.resources.difference_update(resources)
            self.condition.notify_all()


def read_control(path: Path) -> dict[str, Any]:
    """Read the operator control file. A missing or half-written file is ignored."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def execution_worker_cap(lane_count: int, resources: dict[str, int]) -> int:
    """Throttle persisted lane ownership to the current live resource cap."""
    return max(1, min(lane_count, int(resources["active_lane_cap"])))


def schedule_companies(
    companies: Sequence[tuple[str, int]],
    cycles: Sequence[int],
    lane_count: int,
) -> list[dict[str, Any]]:
    lanes = [
        {"lane": index + 1, "weight": 0, "companies": [], "tasks": []}
        for index in range(lane_count)
    ]
    ordered = sorted(
        companies,
        key=lambda item: (-item[1], item[0].upper(), item[0]),
    )
    for company, variants in ordered:
        lane = min(lanes, key=lambda item: (item["weight"], item["lane"]))
        lane["companies"].append(company)
        lane["weight"] += max(1, variants) * len(cycles)
    for lane in lanes:
        lane["companies"].sort(key=lambda value: (value.upper(), value))
        lane["tasks"] = [
            {
                "task_id": task_id(company, cycle),
                "company": company,
                "cycle": int(cycle),
            }
            for company in lane["companies"]
            for cycle in sorted(cycles)
        ]
    return lanes


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class CompanyWorkQueue:
    """Single-coordinator queue. Only untouched companies can change owner.

    Ownership is persisted BEFORE dispatch, including for a company's first
    page. A crash can therefore never make a partially written company eligible
    for stealing on resume. Old manifests conservatively pin every company.
    """

    def __init__(self, plans, pending_by_lane, manifest_path, *, resume=False):
        self.plans = plans
        self.manifest_path = manifest_path
        self.manifest = read_json(manifest_path)
        dynamic = self.manifest.get("scheduling", {}).get("strategy") == "company_work_sharing"
        self.managed_initialization = self.manifest.get('scheduling', {}).get('initialization_markers', dynamic or not resume)
        self.claimed = {
            company for lane in self.manifest["lanes"]
            for company in (lane.get("claimed_companies", []) if dynamic or not resume else lane["companies"])
        }
        self.pending = {
            lane: {company: [task for task in tasks if task["company"] == company]
                   for company in dict.fromkeys(task["company"] for task in tasks)}
            for lane, tasks in pending_by_lane.items()
        }
        self.handoffs = list(self.manifest.get("scheduling", {}).get("handoffs", []))

    def claim(self, lane):
        own = self.pending.setdefault(lane, {})
        donor = lane
        if own:
            company = next(iter(own))
        else:
            donors = [(number, work) for number, work in self.pending.items()
                      if any(company not in self.claimed for company in work)]
            if not donors:
                return None
            donor, work = max(donors, key=lambda item: (sum(len(v) for v in item[1].values()), -item[0]))
            company = next(company for company in reversed(work) if company not in self.claimed)
        tasks = self.pending[donor][company]
        resuming = company in self.claimed
        updated = copy.deepcopy(self.plans)
        handoffs = list(self.handoffs)
        if donor != lane:
            source = next(p for p in updated if p["lane"] == donor)
            target = next(p for p in updated if p["lane"] == lane)
            owned_tasks = [task for task in source["tasks"] if task["company"] == company]
            source["companies"].remove(company)
            source["tasks"] = [task for task in source["tasks"] if task["company"] != company]
            target["companies"].append(company)
            target["tasks"].extend(owned_tasks)
            handoffs.append({"company": company, "from_lane": donor, "to_lane": lane, "claimed_at": utc_now()})
        claimed = self.claimed | {company}
        manifest = copy.deepcopy(self.manifest)
        for entry in manifest["lanes"]:
            plan = next(p for p in updated if p["lane"] == entry["lane"])
            entry["companies"] = plan["companies"]
            entry["tasks"] = plan["tasks"]
            entry["claimed_companies"] = [c for c in plan["companies"] if c in claimed]
        manifest["scheduling"] = {"strategy": "company_work_sharing", "handoffs": handoffs,
                                  "initialization_markers": self.managed_initialization}
        atomic_json(self.manifest_path, manifest)
        # Commit in-memory state only after the durable ownership write succeeds.
        self.plans[:] = updated
        self.manifest, self.claimed, self.handoffs = manifest, claimed, handoffs
        del self.pending[donor][company]
        return tasks, resuming

    def pending_companies(self):
        return sum(len(work) for work in self.pending.values())

    def publish_run(self, run):
        manifest = copy.deepcopy(self.manifest)
        entry = next(lane for lane in manifest['lanes'] if lane['lane'] == run.lane)
        entry.update(state=str(run.state), receipt=str(run.receipt), log=str(run.log))
        atomic_json(self.manifest_path, manifest)
        self.manifest = manifest


def merge_attribution_conflict_count(path: Path) -> int:
    prefix = "attribution_conflicts_recorded="
    for line in reversed(
        path.read_text(encoding="utf-8", errors="replace").splitlines()
    ):
        if line.startswith(prefix):
            return int(line[len(prefix) :])
    raise RuntimeError(
        f"Merge log is missing its attribution-conflict receipt line: {path}"
    )


def last_nonempty_log_line(path: Path, tail_bytes: int = 64 * 1024) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - tail_bytes), os.SEEK_SET)
        tail = stream.read().decode("utf-8", errors="replace")
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def failed_tasks(receipt: dict[str, Any], fallback: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = receipt.get("tasks")
    if not isinstance(tasks, list):
        return list(fallback)
    failed = [
        {
            "task_id": str(item["task_id"]),
            "company": str(item["company"]),
            "cycle": int(item["cycle"]),
        }
        for item in tasks
        if item.get("status") != "succeeded"
    ]
    return failed


@dataclass(frozen=True)
class LaneRun:
    lane: int
    database: Path
    assignments: Path
    receipt: Path
    state: Path
    log: Path
    key: str
    key_id: str
    tasks: tuple[dict[str, Any], ...]
    attempt: int


def execute_lane(
    lane_run: LaneRun,
    *,
    rscript_path: Path,
    lane_script: Path,
    r_library_path: Path | None,
    concurrency: int,
    sqlite_cache_mb: int,
    budget_database: Path,
    request_pool: RequestPool | None = None,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["FEC_API_KEY"] = lane_run.key
    environment.pop("FEC_API_KEYS", None)
    for name in list(environment):
        if re.fullmatch(r"FEC_API_KEY_\d+", name):
            environment.pop(name, None)
    environment["MENTAT_LANE_STATUS_PATH"] = str(lane_run.state)
    environment["MENTAT_SQLITE_CACHE_MB"] = str(sqlite_cache_mb)
    environment["MENTAT_API_BUDGET_DB"] = str(budget_database)
    environment["MENTAT_API_BUDGET_ID"] = (
        f"openfec:sha256:{lane_run.key_id}"
    )
    if request_pool is not None:
        environment["MENTAT_REQUEST_POOL_URL"] = request_pool.url
        environment["MENTAT_REQUEST_POOL_TOKEN"] = request_pool.token
        environment["MENTAT_REQUEST_POOL_LANE"] = str(lane_run.lane)
        environment["MENTAT_REQUEST_POOL_WIDTH"] = str(len(request_pool.keys))
    if r_library_path is not None:
        environment["R_LIBS_USER"] = str(r_library_path)
    command = [
        str(rscript_path),
        str(lane_script),
        str(lane_run.database),
        read_json(lane_run.assignments)["industry"],
        str(lane_run.assignments),
        str(lane_run.receipt),
        lane_run.key_id,
        str(concurrency),
    ]
    started_wall = utc_now()
    started = time.monotonic()
    lane_run.log.parent.mkdir(parents=True, exist_ok=True)
    with lane_run.log.open("a", encoding="utf-8", errors="replace") as log:
        log.write(
            f"\n[{utc_now()}] lane={lane_run.lane} attempt={lane_run.attempt} "
            f"tasks={len(lane_run.tasks)} key_id={lane_run.key_id}\n"
        )
        log.flush()
        process = subprocess.run(
            command,
            cwd=str(lane_script.parents[2]),
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if process.returncode == 4:
        raise RuntimeError(
            f"Another R lane writer still owns {lane_run.database}; "
            "resume after that process exits."
        )
    elapsed = round(time.monotonic() - started, 3)
    last_log_line = last_nonempty_log_line(lane_run.log)
    receipt: dict[str, Any] = {}
    if lane_run.receipt.is_file():
        try:
            receipt = read_json(lane_run.receipt)
        except (OSError, ValueError):
            receipt = {}
    state = lane_state(lane_run.state)
    received_tasks = receipt.get("tasks")
    if not isinstance(received_tasks, list):
        received_tasks = state.get("tasks", [])
    latest_tasks = {
        str(task.get("task_id", "")): dict(task)
        for task in received_tasks
        if isinstance(task, dict) and task.get("task_id")
    }
    for task in lane_run.tasks:
        identifier = str(task["task_id"])
        if identifier in latest_tasks:
            continue
        latest_tasks[identifier] = {
            **task,
            "status": "failed",
            "wall_clock_seconds": elapsed,
            "error": (
                f"Managed Rscript exited with code {int(process.returncode)} "
                "before producing a task receipt."
            ),
        }
    normalized_tasks = [
        latest_tasks[str(task["task_id"])]
        for task in lane_run.tasks
    ]
    if process.returncode != 0:
        # A collector can write all task rows and then fail its postflight
        # quick_check (or another final integrity step). Never promote those
        # rows to a successful lane just because the receipt says succeeded.
        normalized_tasks = [
            {
                **task,
                "status": "failed",
                "error": (
                    f"Managed Rscript exited with code {int(process.returncode)} "
                    "after reporting task success; postflight not trusted."
                ),
            }
            if task.get("status") == "succeeded" else task
            for task in normalized_tasks
        ]
    failed_count = sum(
        1 for task in normalized_tasks if task.get("status") != "succeeded"
    )
    receipt.update(
        {
            "status": "failed" if failed_count else "succeeded",
            "task_count": len(normalized_tasks),
            "succeeded": len(normalized_tasks) - failed_count,
            "failed": failed_count,
            "wall_clock_seconds": elapsed,
            "exit_code": int(process.returncode),
            "last_log_line": last_log_line,
            "tasks": normalized_tasks,
        }
    )
    if process.returncode and not receipt.get("error"):
        receipt["error"] = (
            f"Managed Rscript exited with code {int(process.returncode)}."
        )
    atomic_json(lane_run.receipt, receipt)
    atomic_json(lane_run.state, {**state, "status": receipt["status"],
                "lane": lane_run.lane, "completed": len(normalized_tasks),
                "failed": failed_count, "tasks": normalized_tasks,
                "current_company": None, "current_cycle": None})
    invocation_receipt = lane_run.receipt.with_name(
        f"{lane_run.receipt.stem}.invocation.json"
    )
    invocation = {
        "lane": lane_run.lane,
        "attempt": lane_run.attempt,
        "started_at": started_wall,
        "ended_at": utc_now(),
        "exit_code": int(process.returncode),
        "wall_clock_seconds": elapsed,
        "last_log_line": last_log_line,
        "log_path": str(lane_run.log),
        "database": str(lane_run.database),
        "key_id": lane_run.key_id,
        "units": [
            {
                "task_id": str(task["task_id"]),
                "company": str(task["company"]),
                "cycle": int(task["cycle"]),
            }
            for task in lane_run.tasks
        ],
    }
    atomic_json(invocation_receipt, invocation)
    return {
        "lane": lane_run.lane,
        "attempt": lane_run.attempt,
        "exit_code": int(process.returncode),
        "wall_clock_seconds": elapsed,
        "last_log_line": last_log_line,
        "receipt_path": str(lane_run.receipt),
        "invocation_receipt_path": str(invocation_receipt),
        "state_path": str(lane_run.state),
        "log_path": str(lane_run.log),
        "database": str(lane_run.database),
        "key_id": lane_run.key_id,
        "units": invocation["units"],
        "receipt": receipt,
    }


def skipped_lane(lane_run: LaneRun) -> dict[str, Any]:
    """A lane the operator stopped before it started. Its tasks stay unfinished."""
    return {
        "lane": lane_run.lane,
        "attempt": lane_run.attempt,
        "database": str(lane_run.database),
        "key_id": lane_run.key_id,
        "units": [],
        "receipt": {
            "tasks": [
                {
                    **task,
                    "status": "skipped",
                    "error": "Operator stopped the run before this lane started.",
                }
                for task in lane_run.tasks
            ]
        },
    }


def execute_lane_with_limit(
    limiter: LaneLimiter,
    lane_run: LaneRun,
    **kwargs: Any,
) -> dict[str, Any]:
    # A queued lane must not start once the operator has stopped the run. The
    # thread pool has already marked every future RUNNING, so Future.cancel()
    # cannot do this for us; the permit gate is the only reliable place.
    resources = ("database:" + os.path.normcase(str(lane_run.database.resolve())),
                 "key:" + key_id(lane_run.key))
    if not limiter.acquire(resources):
        return skipped_lane(lane_run)
    try:
        return execute_lane(lane_run, **kwargs)
    finally:
        limiter.release(resources)


def lane_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return read_json(path)
    except (OSError, ValueError):
        return {}


def write_status(
    status_path: Path,
    *,
    run_id: str,
    industry: str,
    total: int,
    lane_runs: Sequence[LaneRun],
    stage: str,
    started_monotonic: float,
    final_failed: int | None = None,
) -> bool:
    states = [lane_state(run.state) for run in lane_runs]
    latest_tasks: dict[str, dict[str, Any]] = {}
    for state in states:
        for task in state.get("tasks", []):
            identifier = str(task.get("task_id", ""))
            if identifier:
                latest_tasks[identifier] = task
    completed = len(latest_tasks)
    running_states = [
        state for state in states if state.get("status") == "running"
    ]
    failed = (
        final_failed
        if final_failed is not None
        else sum(
            1
            for task in latest_tasks.values()
            if task.get("status") != "succeeded"
        )
    )
    durations = [
        float(task.get("wall_clock_seconds", 0))
        for task in latest_tasks.values()
        if task.get("status") == "succeeded"
        and float(task.get("wall_clock_seconds", 0)) > 0
    ]
    remaining = max(0, total - completed)
    estimated = None
    if durations and remaining:
        active = max(1, len(running_states))
        estimated = round((sum(durations) / len(durations)) * remaining / active, 1)
    current = [
        {
            "lane": int(state.get("lane", 0)),
            "company": state.get("current_company"),
            "cycle": state.get("current_cycle"),
        }
        for state in running_states
        if state.get("current_company")
    ]
    try:
        atomic_json(
            status_path,
            {
                "run_id": run_id,
                "industry": industry,
                "status": stage,
                "updated_at": utc_now(),
                "elapsed_seconds": round(
                    time.monotonic() - started_monotonic,
                    3,
                ),
                "completed": completed,
                "total": total,
                "running": len(running_states),
                "failed": int(failed),
                "estimated_remaining_seconds": estimated,
                "current": current,
            },
        )
        return True
    except PermissionError as error:
        print(
            "WARNING: fanout status telemetry write failed after "
            f"{ATOMIC_REPLACE_ATTEMPTS} attempts; continuing collection; "
            f"path={status_path}; error={error}",
            file=sys.stderr,
            flush=True,
        )
        return False


def combine_task_results(
    plans: Sequence[dict[str, Any]],
    attempts: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    latest: dict[str, dict[str, Any]] = {}
    expected = {
        task["task_id"]: task
        for plan in plans
        for task in plan["tasks"]
    }
    for attempt in sorted(
        attempts,
        key=lambda item: (int(item["attempt"]), int(item["lane"])),
    ):
        for task in attempt.get("receipt", {}).get("tasks", []):
            latest[str(task.get("task_id", ""))] = dict(task)
    combined: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for identifier in sorted(expected):
        task = latest.get(
            identifier,
            {
                **expected[identifier],
                "status": "failed",
                "error": "No task receipt was produced.",
            },
        )
        combined.append(task)
        if task.get("status") != "succeeded":
            failed.append(task)
    return combined, failed


def receipt_attempt_number(path: Path) -> int:
    if path.name == "receipt.json":
        return 1
    work = re.fullmatch(r"receipt\.work-(\d+)-[a-z0-9-]+\.json", path.name)
    if work:
        return int(work.group(1))
    recovery = re.fullmatch(
        r"receipt\.recovery-(\d+)(?:-[a-z0-9-]+)?\.json",
        path.name,
    )
    if recovery:
        return 1 + int(recovery.group(1))
    split = re.fullmatch(
        r"receipt\.split-(\d+)-[a-z0-9-]+\.json",
        path.name,
    )
    if split:
        return int(split.group(1))
    resume = re.fullmatch(r"receipt\.resume-(\d+)(?:-[a-z0-9]+)?\.json", path.name)
    return int(resume.group(1)) if resume else 1


def existing_lane_attempts(lane_directory: Path, lane: int) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for path in sorted(
        (
            path
            for path in lane_directory.glob("receipt*.json")
            if not path.name.endswith(".invocation.json")
        ),
        key=lambda item: (receipt_attempt_number(item), item.name),
    ):
        try:
            receipt = read_json(path)
        except (OSError, ValueError):
            continue
        recorded_exit = receipt.get("exit_code")
        postflight = receipt.get("quick_check")
        verified_exit = type(recorded_exit) is int and recorded_exit == 0
        verified_postflight = postflight == "ok"
        # R publishes the receipt after quick_check but before its process has
        # exited. A supervisor killed in that interval never gets to record
        # the exit code. Trust that raw receipt only when its postflight passed;
        # likewise, never skip successful tasks after a recorded bad exit.
        trusted_success = (
            (verified_exit and postflight in (None, "ok"))
            or (recorded_exit is None and verified_postflight)
        )
        if not trusted_success:
            receipt = dict(receipt)
            receipt["status"] = "failed"
            receipt["tasks"] = [
                {
                    **task,
                    "status": "failed",
                    "error": "Lane postflight or process exit was not verified.",
                }
                if isinstance(task, dict) and task.get("status") == "succeeded"
                else task
                for task in receipt.get("tasks", [])
            ]
        attempts.append(
            {
                "lane": lane,
                "attempt": receipt_attempt_number(path),
                "exit_code": 0 if receipt.get("status") == "succeeded" else 2,
                "wall_clock_seconds": float(
                    receipt.get("wall_clock_seconds", 0)
                ),
                "receipt_path": str(path),
                "state_path": str(
                    path.with_name(path.name.replace("receipt", "status", 1))
                ),
                "log_path": str(lane_directory / "process.log"),
                "database": str(lane_directory / "mentat.db"),
                "key_id": str(receipt.get("key_id", "")),
                "receipt": receipt,
            }
        )
    return attempts


def latest_tasks_from_attempts(
    attempts: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for attempt in sorted(
        attempts,
        key=lambda item: (int(item.get("attempt", 0)), int(item.get("lane", 0))),
    ):
        for task in attempt.get("receipt", {}).get("tasks", []):
            identifier = str(task.get("task_id", ""))
            if identifier:
                latest[identifier] = dict(task)
    return latest


def parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--industry", required=True)
    parser.add_argument("--cycles", type=parse_cycles, default=SUPPORTED_CYCLES)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--output-receipt", type=Path)
    parser.add_argument("--keys-file", type=Path)
    parser.add_argument("--max-parallel", type=int, default=0)
    parser.add_argument("--no-shared-requests", action="store_true")
    parser.add_argument("--recovery-workers", type=int, default=0,
                        help="Recovery concurrency; zero shares available worker capacity.")
    parser.add_argument("--concurrency-per-key", type=int, default=8)
    parser.add_argument(
        "--rscript-path",
        type=Path,
        default=Path(seam("MENTAT_RSCRIPT")),
    )
    parser.add_argument("--r-library-path", type=Path)
    parser.add_argument(
        "--memory-per-lane-mb",
        type=int,
        default=DEFAULT_MEMORY_PER_LANE_MB,
    )
    parser.add_argument(
        "--memory-reserve-mb",
        type=int,
        default=DEFAULT_MEMORY_RESERVE_MB,
    )
    parser.add_argument("--sqlite-cache-mb", type=int, default=256)
    parser.add_argument("--merge-batch-limit", type=int, default=MERGE_ATTACHMENT_LIMIT)
    parser.add_argument("--max-lane-ceiling", type=int, default=0)
    parser.add_argument("--initial-workers", type=int, default=0)
    parser.add_argument(
        "--pool-width",
        type=int,
        default=0,
        help=(
            "Shared HTTP request-pool width. Zero uses every healthy API key; "
            "the RAM-derived lane cap never bounds it."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse verified lane databases and run only non-success tasks.",
    )
    parser.add_argument(
        "--budget-database",
        type=Path,
        help=(
            "Persistent redacted per-key hourly-budget SQLite. Defaults to "
            "mentat/.cache/fec-api-budget.sqlite."
        ),
    )
    parsed = parser.parse_args(arguments)
    if parsed.max_parallel < 0:
        parser.error("--max-parallel must be zero (auto) or positive.")
    if not 0 <= parsed.recovery_workers <= 64:
        parser.error("--recovery-workers must be between 0 (auto) and 64.")
    if parsed.concurrency_per_key < 1 or parsed.concurrency_per_key > 64:
        parser.error("--concurrency-per-key must be between 1 and 64.")
    if parsed.memory_per_lane_mb < 128:
        parser.error("--memory-per-lane-mb must be at least 128.")
    if parsed.memory_reserve_mb < 256:
        parser.error("--memory-reserve-mb must be at least 256.")
    if parsed.sqlite_cache_mb < 32 or parsed.sqlite_cache_mb > 2048:
        parser.error("--sqlite-cache-mb must be between 32 and 2048.")
    if parsed.merge_batch_limit < 2:
        parser.error("--merge-batch-limit must be at least 2.")
    if parsed.max_lane_ceiling < 0:
        parser.error("--max-lane-ceiling must be zero (no ceiling) or positive.")
    if parsed.initial_workers < 0:
        parser.error("--initial-workers must be zero (auto) or positive.")
    if not 0 <= parsed.pool_width <= 64:
        parser.error("--pool-width must be between 0 (every healthy key) and 64.")
    return parsed


def settle_source_journals(source_database: Path) -> None:
    """Rollback a hot DELETE journal and checkpoint committed WAL after death."""
    connection = sqlite3.connect(
        source_database.as_uri() + "?mode=rw", uri=True,
        timeout=60, isolation_level=None,
    )
    try:
        # Opening a read-only connection can fail on a hot rollback journal;
        # acquiring this write lease makes SQLite recover it first.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ROLLBACK")
        busy, _, _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise RuntimeError(
                "Source WAL is still owned by a live writer; retry resume later."
            )
    finally:
        connection.close()


def recover_committed_fanout(
    source_database: Path, run_directory: Path,
    manifest: dict[str, Any], output_receipt: Path,
    current_source_fingerprint: str,
) -> bool:
    """Finish a receipt only after proving the same target transaction committed."""
    run_id = str(manifest["run_id"])
    with committed_merge_lease(source_database, run_id) as marker:
        if marker is None:
            return False
        stable_fingerprint = quiescent_database_fingerprint(source_database)
        if stable_fingerprint != current_source_fingerprint:
            raise RuntimeError("Source database changed while recovering merge receipt")
        return finish_committed_fanout(
            source_database, run_directory, manifest, output_receipt,
            stable_fingerprint, marker,
        )


def finish_committed_fanout(
    source_database: Path, run_directory: Path,
    manifest: dict[str, Any], output_receipt: Path,
    current_source_fingerprint: str, marker: dict[str, str],
) -> bool:
    run_id = str(manifest["run_id"])
    intent_path = Path(marker["intent_path"]).resolve(strict=True)
    if intent_path.parent != run_directory:
        raise RuntimeError("Committed merge intent is outside its run directory")
    intent = read_json(intent_path)
    if content_sha256(intent) != marker["intent_sha256"]:
        raise RuntimeError("Committed merge intent changed after publication")
    prepared_path = Path(str(intent.get("prepared_receipt_path", ""))).resolve(strict=True)
    if prepared_path.parent != run_directory:
        raise RuntimeError("Prepared merge receipt is outside its run directory")
    if (
        intent.get("version") != 1
        or intent.get("run_id") != run_id
        or intent.get("source_database") != str(source_database)
        or intent.get("source_state_before") != manifest.get("source_state_before")
        or intent.get("industry") != manifest.get("industry")
        or intent.get("cycles") != manifest.get("cycles")
        or marker["prepared_receipt_sha256"] != intent.get("prepared_receipt_sha256")
        or file_sha256(prepared_path) != marker["prepared_receipt_sha256"]
    ):
        raise RuntimeError("Committed merge intent does not match this fanout run")
    manifest_specs = {
        (str(Path(lane["database"]).resolve()), tuple(sorted(lane["companies"])))
        for lane in manifest.get("lanes", []) if lane.get("companies")
    }
    intent_specs = {
        (str(Path(item["database"]).resolve()), tuple(sorted(item["companies"])))
        for item in intent.get("specifications", [])
    }
    if manifest_specs != intent_specs or not intent_specs:
        raise RuntimeError("Committed merge shards do not match the run manifest")
    for specification in intent.get("specifications", []):
        shard = Path(specification["database"]).resolve(strict=True)
        if not shard.is_relative_to(run_directory / "lanes"):
            raise RuntimeError("Committed merge shard is outside this run")
        if quiescent_database_fingerprint(shard) != specification["sha256"]:
            raise RuntimeError("Committed merge shard changed after publication")
    prepared = read_json(prepared_path)
    expected_task_ids = {
        str(task["task_id"])
        for lane in manifest.get("lanes", [])
        for task in lane.get("tasks", [])
    }
    observed_task_ids = [str(task.get("task_id", ""))
                         for task in prepared.get("tasks", [])]
    if (
        prepared.get("status") != "merge_prepared"
        or prepared.get("run_id") != run_id
        or prepared.get("source_state_before") != manifest.get("source_state_before")
        or prepared.get("source_database") != str(source_database)
        or prepared.get("failed") != 0
        or prepared.get("failures") != []
        or prepared.get("task_count") != prepared.get("succeeded")
        or len(observed_task_ids) != prepared.get("task_count")
        or len(set(observed_task_ids)) != len(observed_task_ids)
        or set(observed_task_ids) != expected_task_ids
        or any(task.get("status") != "succeeded" for task in prepared.get("tasks", []))
    ):
        raise RuntimeError("Prepared fanout receipt is not a complete successful run")
    ended = utc_now()
    receipt = dict(prepared)
    receipt.update({
        "status": "succeeded", "merged": True,
        "source_state_after_merge": current_source_fingerprint,
        "ended_at": ended, "failed": 0, "failures": [],
    })
    receipt["merge"] = {
        **prepared.get("merge", {}), "attempted": True,
        "exit_code": 0, "recovered_after_commit": True,
        "intent_sha256": marker["intent_sha256"],
        "output_logical_sha256": marker["output_logical_sha256"],
    }
    status_path = Path(prepared["status_path"])
    status = read_json(status_path) if status_path.is_file() else {}
    status.update(
        run_id=run_id, industry=prepared["industry"], status="succeeded",
        completed=prepared["task_count"], total=prepared["task_count"],
        failed=0, running=0, current=[], updated_at=ended,
        estimated_remaining_seconds=0,
    )
    atomic_json(status_path, status)
    workers_path = run_directory / "workers.json"
    workers = read_json(workers_path) if workers_path.is_file() else {}
    pool_state = workers.get("request_pool")
    if isinstance(pool_state, dict):
        pool_state = {**pool_state, "active_workers": 0, "queued_requests": 0}
    workers.update(
        active_workers=0, database_writers=0, pending_companies=0,
        request_pool=pool_state, stopping=False, finished=True,
        updated_at=ended,
    )
    atomic_json(workers_path, workers)
    request_workers_path = run_directory / "request-workers.json"
    if request_workers_path.is_file():
        request_workers = read_json(request_workers_path)
        request_workers.update(
            active_workers=0, queued_requests=0, stopped=True,
            updated_at_epoch=time.time(),
        )
        atomic_json(request_workers_path, request_workers)
    atomic_json(output_receipt, receipt)
    print(f"MENTAT API FANOUT RECOVERED COMMITTED MERGE receipt={output_receipt}",
          flush=True)
    return True


def _main_unlocked(arguments: Sequence[str] | None = None,
                   *, owner_token: str | None = None,
                   lifecycle: dict[str, Any] | None = None) -> int:
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    source_database = options.source_database.resolve(strict=True)
    run_directory = options.run_directory.resolve(strict=False)
    run_directory.mkdir(parents=True, exist_ok=True)
    ownership = ({"supervisor_owner_path": str(run_directory / "supervisor-owner.json"),
                  "supervisor_token": owner_token} if owner_token else {})
    output_receipt = (
        options.output_receipt.resolve(strict=False)
        if options.output_receipt
        else run_directory / "receipt.json"
    )
    status_path = run_directory / "status.json"
    manifest_path = run_directory / "manifest.json"
    lane_script = Path(__file__).with_name("collect_api_lane.R").resolve(strict=True)
    merge_script = Path(__file__).with_name("merge_company_shards.py").resolve(
        strict=True
    )
    rscript_path = options.rscript_path.resolve(strict=True)
    r_library_path = (
        options.r_library_path.resolve(strict=True)
        if options.r_library_path
        else None
    )
    budget_database = (
        options.budget_database.resolve(strict=False)
        if options.budget_database
        else Path(__file__).resolve().parents[1]
        / ".cache"
        / "fec-api-budget.sqlite"
    )
    budget_database.parent.mkdir(parents=True, exist_ok=True)
    if options.keys_file is not None:
        options.keys_file = options.keys_file.resolve(strict=True)

    if manifest_path.is_file() and not options.resume:
        raise RuntimeError(
            "RunDirectory already contains a fanout manifest; refusing to "
            "recreate lane snapshots and lose checkpoints. Resume with: "
            f"{sys.executable} {Path(__file__).resolve()} "
            f"--source-database \"{source_database}\" "
            f"--industry \"{options.industry.strip()}\" "
            f"--cycles \"{'|'.join(map(str, options.cycles))}\" "
            f"--run-directory \"{run_directory}\" --resume"
        )
    if options.resume and not manifest_path.is_file():
        raise RuntimeError(
            f"--resume requires an existing manifest: {manifest_path}"
        )
    existing_manifest: dict[str, Any] | None = None
    if options.resume:
        existing_manifest = read_json(manifest_path)
        if Path(existing_manifest["source_database"]).resolve() != source_database:
            raise RuntimeError("Resume source database does not match manifest.")
        if existing_manifest.get("industry") != options.industry.strip():
            raise RuntimeError("Resume industry does not match manifest.")
        if tuple(existing_manifest.get("cycles", [])) != tuple(options.cycles):
            raise RuntimeError("Resume cycles do not match manifest.")
        settle_source_journals(source_database)
    verify_database(source_database)
    current_source_fingerprint = quiescent_database_fingerprint(source_database)
    if options.resume and output_receipt.is_file():
        prior_final = read_json(output_receipt)
        if (
            prior_final.get("status") == "succeeded"
            and prior_final.get("merged") is True
        ):
            if (
                prior_final.get("source_state_after_merge")
                != current_source_fingerprint
            ):
                raise RuntimeError(
                    "Completed fanout receipt does not match the current "
                    "same-path source database."
                )
            print(
                "MENTAT API FANOUT ALREADY COMPLETE "
                f"receipt={output_receipt}",
                flush=True,
            )
            return 0
    if options.resume:
        assert existing_manifest is not None
        if recover_committed_fanout(
            source_database, run_directory, existing_manifest,
            output_receipt, current_source_fingerprint,
        ):
            return 0

    keys = discover_keys(options.keys_file, dict(os.environ))
    companies = database_companies(source_database, options.industry.strip())
    canonical_company_order = [company for company, _ in companies]
    resources = resource_cap(
        key_count=len(keys),
        company_count=len(companies),
        requested_parallel=options.max_parallel,
        memory_per_lane_mb=options.memory_per_lane_mb,
        memory_reserve_mb=options.memory_reserve_mb,
        merge_batch_limit=options.merge_batch_limit,
        lane_ceiling=options.max_lane_ceiling,
    )
    if options.resume:
        assert existing_manifest is not None
        if (
            existing_manifest.get("source_state_before")
            != current_source_fingerprint
        ):
            raise RuntimeError(
                "Resume source database contents do not match the fanout "
                "manifest; refusing stale lane snapshots."
            )
        lane_count = int(existing_manifest["lane_count"])
        if len(keys) < lane_count:
            # A key quarantined, rotated or disabled between attempts leaves
            # fewer keys than the manifest planned lanes for. Refusing here
            # discarded every verified company-cycle task the stopped attempt
            # had already finished, which is the opposite of what a resume is
            # for. Lanes take keys round-robin below instead, and
            # execute_lane_with_limit holds a "key:" resource for the whole
            # lane, so two lanes that share a key are serialized and still
            # never hold the same key concurrently.
            print(
                f"[fanout] resuming {lane_count} planned lanes on "
                f"{len(keys)} keys; lanes sharing a key run one at a time.",
                flush=True,
            )
        plans = [
            {
                "lane": int(lane["lane"]),
                "weight": 0,
                "companies": list(lane["companies"]),
                "tasks": list(lane["tasks"]),
            }
            for lane in existing_manifest["lanes"]
        ]
        plans.sort(key=lambda item: item["lane"])
        canonical_company_order = list(
            existing_manifest.get("company_order", canonical_company_order)
        )
        run_id = str(existing_manifest["run_id"])
    else:
        # Plan every lane the key supply and roster structurally allow, so the
        # operator can raise workers mid-run. Memory only decides where the run
        # starts (--initial-workers), never where its ceiling sits.
        lane_count = max(1, min(len(keys), len(companies)))
        if options.max_parallel > 0:
            lane_count = min(lane_count, options.max_parallel)
        if options.max_lane_ceiling > 0:
            lane_count = min(lane_count, options.max_lane_ceiling)
        plans = schedule_companies(companies, options.cycles, lane_count)
        run_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    invocation_id = secrets.token_hex(8)
    spare_keys = keys[lane_count:]
    started_wall = utc_now()
    started_monotonic = time.monotonic()
    all_lane_runs: list[LaneRun] = []
    primary_runs: list[LaneRun] = []
    merge_runs: list[LaneRun] = []
    attempts: list[dict[str, Any]] = []
    status_write_failures = 0

    for index, plan in enumerate(plans):
        lane_number = int(plan["lane"])
        lane_directory = run_directory / "lanes" / f"lane-{lane_number:02d}"
        lane_directory.mkdir(parents=True, exist_ok=True)
        lane_database = lane_directory / "mentat.db"
        if options.resume:
            verify_database(lane_database)
        else:
            create_snapshot(source_database, lane_database)
        prior_attempts = (
            existing_lane_attempts(lane_directory, lane_number)
            if options.resume
            else []
        )
        attempts.extend(prior_attempts)
        latest_prior = latest_tasks_from_attempts(prior_attempts)
        pending_tasks = [
            task
            for task in plan["tasks"]
            if latest_prior.get(task["task_id"], {}).get("status") != "succeeded"
        ]
        resume_number = (
            max(
                (int(item["attempt"]) for item in prior_attempts),
                default=0,
            )
            + 1
        )
        assignment_path = lane_directory / (
            f"assignments.resume-{resume_number}-{invocation_id}.json"
            if options.resume
            else "assignments.json"
        )
        receipt_path = lane_directory / (
            f"receipt.resume-{resume_number}-{invocation_id}.json"
            if options.resume
            else "receipt.json"
        )
        state_path = lane_directory / (
            f"status.resume-{resume_number}-{invocation_id}.json"
            if options.resume
            else "status.json"
        )
        log_path = lane_directory / (
            f"process.resume-{resume_number}-{invocation_id}.log"
            if options.resume else "process.log"
        )
        if not options.resume or pending_tasks:
            atomic_json(
                assignment_path,
                {
                    **ownership,
                    "run_id": run_id,
                    "industry": options.industry.strip(),
                    "lane": lane_number,
                    "companies": plan["companies"],
                    "cycles": list(options.cycles),
                    "resume": bool(options.resume),
                    "tasks": pending_tasks if options.resume else plan["tasks"],
                },
            )
        merge_run = LaneRun(
            lane=lane_number,
            database=lane_database,
            assignments=assignment_path,
            receipt=receipt_path,
            state=state_path,
            log=log_path,
            key=keys[index % len(keys)],
            key_id=key_id(keys[index % len(keys)]),
            tasks=tuple(pending_tasks if options.resume else plan["tasks"]),
            attempt=resume_number if options.resume else 1,
        )
        merge_runs.append(merge_run)
        if prior_attempts:
            baseline_state = lane_directory / f"status.resume-baseline-{invocation_id}.json"
            prior_tasks = list(latest_prior.values())
            atomic_json(
                baseline_state,
                {
                    "status": "checkpointed",
                    "lane": lane_number,
                    "completed": len(prior_tasks),
                    "total": len(plan["tasks"]),
                    "failed": sum(
                        1
                        for task in prior_tasks
                        if task.get("status") != "succeeded"
                    ),
                    "current_company": None,
                    "current_cycle": None,
                    "tasks": prior_tasks,
                },
            )
            all_lane_runs.append(
                LaneRun(
                    lane=lane_number,
                    database=lane_database,
                    assignments=assignment_path,
                    receipt=receipt_path,
                    state=baseline_state,
                    log=log_path,
                    key="",
                    key_id="",
                    tasks=tuple(),
                    attempt=0,
                )
            )
        if merge_run.tasks:
            primary_runs.append(merge_run)

    task_total = sum(len(plan["tasks"]) for plan in plans)
    if not options.resume:
        atomic_json(
            manifest_path,
            {
            "run_id": run_id,
            "industry": options.industry.strip(),
            "source_database": str(source_database),
            "source_state_before": current_source_fingerprint,
            "cycles": list(options.cycles),
            "started_at": started_wall,
            "task_count": task_total,
            "company_count": len(companies),
            "company_order": canonical_company_order,
            "configured_key_count": len(keys),
            "spare_key_count": len(spare_keys),
            "lane_count": lane_count,
            "resource_cap": resources,
            "concurrency_per_key": options.concurrency_per_key,
            "recovery_workers": options.recovery_workers,
            "recovery_ladder": [
                "primary_lane",
                "fresh_lane_retry",
                "fresh_company_cycle_split",
            ],
            "runtimes": {
                "python_path": str(Path(sys.executable).resolve()),
                "rscript_path": str(rscript_path),
                "r_library_path": (
                    str(r_library_path) if r_library_path else None
                ),
            },
            "budget_database": str(budget_database),
            "lanes": [
                {
                    "lane": run.lane,
                    "key_id": run.key_id,
                    "companies": plans[run.lane - 1]["companies"],
                    "tasks": plans[run.lane - 1]["tasks"],
                    "database": str(run.database),
                    "receipt": str(run.receipt),
                    "state": str(run.state),
                    "log": str(run.log),
                }
                for run in merge_runs
            ],
            },
        )
    print(
        "MENTAT API FANOUT "
        f"industry={options.industry.strip()} tasks={task_total} "
        f"keys={len(keys)} lanes={lane_count} "
        f"cpu_telemetry={resources['logical_cpu']} "
        f"memory_cap={resources['memory_lane_cap']} "
        f"available_memory_mb={resources['available_memory_mb']} "
        f"merge_cap={resources['sqlite_merge_cap']} "
        f"resume={options.resume} pending_tasks="
        f"{sum(len(run.tasks) for run in primary_runs)}",
        flush=True,
    )

    work_queue = CompanyWorkQueue(
        plans, {run.lane: run.tasks for run in merge_runs}, manifest_path,
        resume=options.resume,
    )
    lane_templates = {run.lane: run for run in merge_runs}
    lane_failures: dict[int, list[dict[str, Any]]] = {run.lane: [] for run in merge_runs}

    def initialization_markers(lane, tasks):
        if not work_queue.managed_initialization:
            return {}
        directory = lane_templates[lane].database.parent / "initialized"
        return {task["task_id"]: str(directory / (hashlib.sha256(task["task_id"].encode("utf-8")).hexdigest() + ".json")) for task in tasks}

    def next_company(lane: int) -> LaneRun | None:
        claimed = work_queue.claim(lane)
        if claimed is None:
            return None
        tasks, resuming = claimed
        template = lane_templates[lane]
        token = hashlib.sha256(tasks[0]["company"].encode("utf-8")).hexdigest()[:16]
        suffix = (f"work-{template.attempt}-{token}"
                  f"{'-' + invocation_id if options.resume else ''}.json")
        assignment = template.assignments.with_name("assignments." + suffix)
        atomic_json(assignment, {
            **ownership,
            "industry": options.industry.strip(), "run_id": run_id,
            "lane": lane, "resume": resuming, "tasks": tasks,
            "initialization_markers": initialization_markers(lane, tasks),
        })
        run = LaneRun(
            lane=lane, database=template.database, assignments=assignment,
            receipt=template.receipt.with_name("receipt." + suffix),
            state=template.state.with_name("status." + suffix), log=template.log,
            key=template.key, key_id=template.key_id, tasks=tuple(tasks),
            attempt=template.attempt,
        )
        all_lane_runs.append(run)
        work_queue.publish_run(run)
        return run

    recovery_futures: dict[concurrent.futures.Future[dict[str, Any]], LaneRun] = {}
    recovery_results: list[tuple[LaneRun, dict[str, Any]]] = []
    primary_worker_count = (
        max(1, min(options.initial_workers, lane_count))
        if options.initial_workers > 0
        else execution_worker_cap(lane_count, resources)
    )
    execution_limiter = LaneLimiter(primary_worker_count)
    request_pool = None
    worker_ceiling = lane_count if options.no_shared_requests else min(len(keys), options.max_parallel or len(keys), options.max_lane_ceiling or len(keys))
    if not options.no_shared_requests:
        # HTTP width follows the key supply, never the RAM-derived lane cap: the
        # keys are the rate limit. R lanes stay bounded by execution_limiter.
        pool_width = options.pool_width if options.pool_width > 0 else worker_ceiling
        request_pool = RequestPool(keys, budget_database, pool_width)
        if lifecycle is not None:
            lifecycle["request_pool"] = request_pool
        request_pool.url = request_pool.start()
        if lifecycle is None:
            atexit.register(request_pool.close)
    control_path = options.run_directory / "control.json"
    workers_path = options.run_directory / "workers.json"
    stop_requested = threading.Event()
    control_finished = threading.Event()

    def publish_workers() -> None:
        pool_state = request_pool.snapshot() if request_pool else None
        try:
            atomic_json(
                workers_path,
                {
                    "lane_count": lane_count,
                    "worker_ceiling": worker_ceiling,
                    "active_workers": pool_state["active_workers"] if pool_state else execution_limiter.active(),
                    "database_writers": execution_limiter.active(),
                    "request_pool": pool_state,
                    "requested_workers": pool_state['requested_workers'] if pool_state else execution_limiter.target(),
                    "quarantined_keys": pool_state['quarantined_keys'] if pool_state else [],
                    "reissued_requests": pool_state['reissued_requests'] if pool_state else 0,
                    "stopping": stop_requested.is_set(),
                    "pending_companies": work_queue.pending_companies(),
                    "company_handoffs": len(work_queue.handoffs),
                    "scheduling": "company_work_sharing",
                    "updated_at": utc_now(),
                },
            )
        except OSError:
            pass

    def watch_control() -> None:
        # The operator console writes control.json; a missing or half-written
        # file must never disturb a live collection.
        while not control_finished.wait(3):
            control = read_control(control_path)
            desired = control.get("desired_workers")
            if isinstance(desired, int) and desired > 0:
                # This dial owns the R database coordinators only. The shared HTTP
                # pool is as wide as the key supply and never follows it.
                target = max(1, min(int(desired), worker_ceiling, lane_count))
                if target != execution_limiter.target():
                    execution_limiter.resize(target)
                    width = request_pool.snapshot()['requested_workers'] if request_pool else 0
                    print(
                        f"[fanout] operator set database coordinators to {target} "
                        f"(lane ceiling {lane_count}; request pool width {width})",
                        flush=True,
                    )
            if control.get("stop") and not stop_requested.is_set():
                stop_requested.set()
                execution_limiter.stop()
                if request_pool: request_pool.stop()
                print(
                    "[fanout] operator stop requested; no further lanes will "
                    "start and in-flight lanes will finish.",
                    flush=True,
                )
            publish_workers()

    publish_workers()
    if lifecycle is not None:
        lifecycle["telemetry_owned"] = True
    control_watcher = threading.Thread(
        target=watch_control,
        name="mentat-api-control",
        daemon=True,
    )
    if lifecycle is not None:
        lifecycle["control_finished"] = control_finished
        lifecycle["control_watcher"] = control_watcher
    control_watcher.start()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=lane_count,
        thread_name_prefix="mentat-api-primary",
    ) as primary_pool, concurrent.futures.ThreadPoolExecutor(
        max_workers=min(lane_count, options.recovery_workers or lane_count),
        thread_name_prefix="mentat-api-recovery",
    ) as recovery_pool:
        primary_futures = {}

        def submit_company(lane: int):
            if stop_requested.is_set():
                return None
            run = next_company(lane)
            if run is None:
                return None
            future = primary_pool.submit(
                execute_lane_with_limit,
                execution_limiter,
                run,
                rscript_path=rscript_path,
                lane_script=lane_script,
                r_library_path=r_library_path,
                concurrency=options.concurrency_per_key,
                sqlite_cache_mb=options.sqlite_cache_mb,
                budget_database=budget_database,
                request_pool=request_pool,
            )
            primary_futures[future] = run
            return future

        for lane in lane_templates:
            submit_company(lane)
        pending: set[concurrent.futures.Future[dict[str, Any]]] = set(
            primary_futures
        )
        while pending:
            if not write_status(
                status_path,
                run_id=run_id,
                industry=options.industry.strip(),
                total=task_total,
                lane_runs=all_lane_runs,
                stage="collecting",
                started_monotonic=started_monotonic,
            ):
                status_write_failures += 1
            if stop_requested.is_set():
                # Drop lanes that have not started. Anything already running
                # keeps its API budget and finishes its own write cleanly.
                for queued in list(pending):
                    queued.cancel()
                pending = {
                    queued for queued in pending if not queued.cancelled()
                }
            done, pending = concurrent.futures.wait(
                pending,
                timeout=1,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                run = primary_futures[future]
                attempt = future.result()
                attempts.append(attempt)
                lane_failures[run.lane].extend(failed_tasks(attempt["receipt"], run.tasks))
                next_future = submit_company(run.lane)
                if next_future is not None:
                    pending.add(next_future)
                    continue
                retry_tasks = lane_failures[run.lane]
                if not retry_tasks:
                    continue
                if stop_requested.is_set():
                    continue
                recovery_assignment = run.assignments.with_name(
                    f"assignments.recovery-{run.attempt}-{invocation_id}.json"
                )
                recovery_receipt = run.receipt.with_name(
                    f"receipt.recovery-{run.attempt}-{invocation_id}.json"
                )
                recovery_state = run.state.with_name(
                    f"status.recovery-{run.attempt}-{invocation_id}.json"
                )
                atomic_json(
                    recovery_assignment,
                    {
                        **ownership,
                        "run_id": run_id,
                        "industry": options.industry.strip(),
                        "lane": run.lane,
                        "recovery_attempt": run.attempt,
                        "resume": True,
                        "tasks": retry_tasks,
                        "initialization_markers": initialization_markers(run.lane, retry_tasks),
                    },
                )
                recovery_run = LaneRun(
                    lane=run.lane,
                    database=run.database,
                    assignments=recovery_assignment,
                    receipt=recovery_receipt,
                    state=recovery_state,
                    log=run.log,
                    key=(
                        spare_keys[run.lane - 1]
                        if run.lane <= len(spare_keys)
                        else run.key
                    ),
                    key_id=key_id(
                        spare_keys[run.lane - 1]
                        if run.lane <= len(spare_keys)
                        else run.key
                    ),
                    tasks=tuple(retry_tasks),
                    attempt=run.attempt + 1,
                )
                all_lane_runs.append(recovery_run)
                work_queue.publish_run(recovery_run)
                recovery_future = recovery_pool.submit(
                    execute_lane_with_limit,
                    execution_limiter,
                    recovery_run,
                    rscript_path=rscript_path,
                    lane_script=lane_script,
                    r_library_path=r_library_path,
                    concurrency=options.concurrency_per_key,
                    sqlite_cache_mb=options.sqlite_cache_mb,
                    budget_database=budget_database,
                request_pool=request_pool,
                )
                recovery_futures[recovery_future] = recovery_run

        recovery_pending = set(recovery_futures)
        while recovery_pending:
            if not write_status(
                status_path,
                run_id=run_id,
                industry=options.industry.strip(),
                total=task_total,
                lane_runs=all_lane_runs,
                stage="recovering",
                started_monotonic=started_monotonic,
            ):
                status_write_failures += 1
            done, recovery_pending = concurrent.futures.wait(
                recovery_pending,
                timeout=1,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                recovery_run = recovery_futures[future]
                recovery_result = future.result()
                attempts.append(recovery_result)
                recovery_results.append((recovery_run, recovery_result))

        split_futures: dict[
            concurrent.futures.Future[dict[str, Any]], LaneRun
        ] = {}
        split_queues: dict[int, list[LaneRun]] = {}
        for recovery_run, recovery_result in recovery_results:
            if stop_requested.is_set():
                break
            split_tasks = failed_tasks(
                recovery_result["receipt"],
                recovery_run.tasks,
            )
            for task in split_tasks:
                identifier = str(task["task_id"])
                token = re.sub(r"[^a-z0-9-]+", "-", identifier.lower()).strip(
                    "-"
                )
                if not token:
                    token = f"unit-{recovery_run.lane}"
                split_attempt = recovery_run.attempt + 1
                split_assignment = recovery_run.assignments.with_name(
                    f"assignments.split-{split_attempt}-{token}-{invocation_id}.json"
                )
                split_receipt = recovery_run.receipt.with_name(
                    f"receipt.split-{split_attempt}-{token}-{invocation_id}.json"
                )
                split_state = recovery_run.state.with_name(
                    f"status.split-{split_attempt}-{token}-{invocation_id}.json"
                )
                atomic_json(
                    split_assignment,
                    {
                        **ownership,
                        "run_id": run_id,
                        "industry": options.industry.strip(),
                        "lane": recovery_run.lane,
                        "split_attempt": split_attempt,
                        "parent_attempt": recovery_run.attempt,
                        "resume": True,
                        "tasks": [task],
                        "initialization_markers": initialization_markers(recovery_run.lane, [task]),
                    },
                )
                split_run = LaneRun(
                    lane=recovery_run.lane,
                    database=recovery_run.database,
                    assignments=split_assignment,
                    receipt=split_receipt,
                    state=split_state,
                    log=recovery_run.log,
                    key=recovery_run.key,
                    key_id=recovery_run.key_id,
                    tasks=(task,),
                    attempt=split_attempt,
                )
                all_lane_runs.append(split_run)
                split_queues.setdefault(split_run.lane, []).append(split_run)

        def submit_split(lane):
            if stop_requested.is_set() or not split_queues[lane]:
                return None
            split_run = split_queues[lane].pop(0)
            work_queue.publish_run(split_run)
            future = recovery_pool.submit(
                execute_lane_with_limit, execution_limiter, split_run,
                rscript_path=rscript_path, lane_script=lane_script,
                r_library_path=r_library_path,
                concurrency=options.concurrency_per_key,
                sqlite_cache_mb=options.sqlite_cache_mb,
                budget_database=budget_database,
                request_pool=request_pool,
            )
            split_futures[future] = split_run
            return future

        # At most one queued split per database keeps blocked same-owner jobs
        # from occupying the pool while other companies could recover.
        for lane in split_queues:
            submit_split(lane)

        split_pending = set(split_futures)
        while split_pending:
            if not write_status(
                status_path,
                run_id=run_id,
                industry=options.industry.strip(),
                total=task_total,
                lane_runs=all_lane_runs,
                stage="splitting",
                started_monotonic=started_monotonic,
            ):
                status_write_failures += 1
            done, split_pending = concurrent.futures.wait(
                split_pending,
                timeout=1,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                attempts.append(future.result())
                next_future = submit_split(split_futures[future].lane)
                if next_future is not None:
                    split_pending.add(next_future)

    combined_tasks, final_failed = combine_task_results(plans, attempts)
    merge_log = run_directory / (
        f"merge.resume-{invocation_id}.log" if options.resume else "merge.log"
    )

    def receipt_document(
        status: str, after: str | None, ended: str | None,
        failures: list[dict[str, Any]], is_merged: bool,
        merge_details: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "status": status,
            "industry": options.industry.strip(),
            "source_database": str(source_database),
            "source_state_before": current_source_fingerprint,
            "source_state_after_merge": after,
            "run_directory": str(run_directory),
            "manifest": str(manifest_path),
            "status_path": str(status_path),
            "started_at": started_wall,
            "ended_at": ended,
            "wall_clock_seconds": round(time.monotonic() - started_monotonic, 3),
            "company_count": len(companies),
            "task_count": task_total,
            "succeeded": task_total - len(failures),
            "failed": len(failures),
            "merged": is_merged,
            "configured_key_count": len(keys),
            "spare_key_count": len(spare_keys),
            "request_pool": request_pool.snapshot() if request_pool else None,
            "lane_count": lane_count,
            "execution_worker_count": primary_worker_count,
            "final_worker_count": execution_limiter.target(),
            "stopped_early": stop_requested.is_set(),
            "resource_cap": resources,
            "recovery_ladder": [
                "primary_lane", "fresh_lane_retry", "fresh_company_cycle_split",
            ],
            "runtimes": {
                "python_path": str(Path(sys.executable).resolve()),
                "rscript_path": str(rscript_path),
                "r_library_path": str(r_library_path) if r_library_path else None,
            },
            "budget_database": str(budget_database),
            "telemetry": {
                "status_write_failures": status_write_failures,
                "company_handoffs": len(work_queue.handoffs),
                "scheduling": "company_work_sharing",
            },
            "tasks": combined_tasks,
            "failures": failures,
            "lane_attempts": [
                {key: value for key, value in attempt.items() if key != "receipt"}
                for attempt in attempts
            ],
            "merge": merge_details,
        }

    merge_result: dict[str, Any] = {
        "attempted": False,
        "exit_code": None,
        "log": str(merge_log),
    }
    merged = False
    merge_intent: dict[str, Any] | None = None
    # A stopped run never writes the source database, even if every lane that
    # did run happened to succeed. Resume it later instead.
    if not final_failed and not stop_requested.is_set():
        if (
            quiescent_database_fingerprint(source_database)
            != current_source_fingerprint
        ):
            raise RuntimeError(
                "Source database changed during fanout; refusing to merge "
                "lane snapshots into a moving target."
            )
        if not write_status(
            status_path,
            run_id=run_id,
            industry=options.industry.strip(),
            total=task_total,
            lane_runs=all_lane_runs,
            stage="merging",
            started_monotonic=started_monotonic,
            final_failed=0,
        ):
            status_write_failures += 1
        merge_specifications = [
            f"{run.database}::{'|'.join(plans[run.lane - 1]['companies'])}"
            for run in merge_runs
            if plans[run.lane - 1]["companies"]
        ]
        intent_path: Path | None = None
        if owner_token:
            prepared_path = run_directory / f"merge-prepared-{invocation_id}.json"
            prepared = receipt_document(
                "merge_prepared", None, None, [], False,
                {"attempted": True, "exit_code": None, "log": str(merge_log)},
            )
            atomic_json(prepared_path, prepared)
            grouped_specs = parse_specifications(
                merge_specifications, source_database
            )
            merge_intent = {
                "version": 1,
                "run_id": run_id,
                "industry": options.industry.strip(),
                "cycles": list(options.cycles),
                "source_database": str(source_database),
                "source_state_before": current_source_fingerprint,
                "prepared_receipt_path": str(prepared_path),
                "prepared_receipt_sha256": file_sha256(prepared_path),
                "specifications": [
                    {"database": str(shard),
                     "sha256": quiescent_database_fingerprint(shard),
                     "companies": list(owned_companies)}
                    for shard, owned_companies in grouped_specs
                ],
            }
            intent_path = run_directory / f"merge-intent-{invocation_id}.json"
            atomic_json(intent_path, merge_intent)
        merge_command = [
            sys.executable,
            str(merge_script),
            str(source_database),
            "--company-order-json",
            json.dumps(canonical_company_order, ensure_ascii=False),
            "--expected-target-sha256",
            current_source_fingerprint,
            *(["--supervisor-owner-path", str(run_directory / "supervisor-owner.json"),
               "--supervisor-token", owner_token] if owner_token else []),
            *(["--merge-intent-json", str(intent_path)] if intent_path else []),
            "--merge-batch-limit",
            str(options.merge_batch_limit),
            *merge_specifications,
        ]
        merge_started = time.monotonic()
        with merge_log.open("w", encoding="utf-8", errors="replace") as log:
            merge_process = subprocess.run(
                merge_command,
                cwd=str(merge_script.parents[2]),
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        merge_result = {
            "attempted": True,
            "exit_code": int(merge_process.returncode),
            "wall_clock_seconds": round(time.monotonic() - merge_started, 3),
            "log": str(merge_log),
            "attribution_conflicts_recorded": (
                merge_attribution_conflict_count(merge_log)
                if merge_process.returncode == 0
                else None
            ),
        }
        if merge_process.returncode == 0:
            verify_database(source_database)
            merged = True
        else:
            final_failed = [
                {
                    "task_id": "merge",
                    "status": "failed",
                    "error": "Deterministic shard merge failed; see merge log.",
                }
            ]

    final_status = "succeeded" if merged and not final_failed else "failed"

    def publish_final_receipt() -> dict[str, Any]:
        nonlocal status_write_failures
        if final_status != "succeeded":
            settle_source_journals(source_database)
        source_state_after_merge = quiescent_database_fingerprint(source_database)
        ended_wall = utc_now()
        if not write_status(
            status_path,
            run_id=run_id,
            industry=options.industry.strip(),
            total=task_total,
            lane_runs=all_lane_runs,
            stage=final_status,
            started_monotonic=started_monotonic,
            final_failed=len(final_failed),
        ):
            status_write_failures += 1
        receipt = receipt_document(
            final_status, source_state_after_merge, ended_wall,
            final_failed, merged, merge_result,
        )
        control_finished.set()
        publish_workers()
        atomic_json(output_receipt, receipt)
        return receipt

    if merged and merge_intent is not None:
        with committed_merge_lease(source_database, run_id) as marker:
            if (
                marker is None
                or marker["intent_sha256"] != content_sha256(merge_intent)
            ):
                raise RuntimeError(
                    "Merge child exited successfully without its matching "
                    "transactional source-DB commit marker."
                )
            receipt = publish_final_receipt()
    else:
        receipt = publish_final_receipt()
    print(
        f"MENTAT API FANOUT {final_status.upper()} "
        f"succeeded={receipt['succeeded']}/{task_total} merged={merged} "
        f"wall={receipt['wall_clock_seconds']:.3f}s receipt={output_receipt}",
        flush=True,
    )
    if stop_requested.is_set() and final_status != "succeeded":
        # Distinct from a real failure: the operator stopped this run, the
        # source database was never merged, and --resume can continue it.
        return 3
    if request_pool and lifecycle is None:
        request_pool.close()
        atexit.unregister(request_pool.close)
    return 0 if final_status == "succeeded" else 2


def cleanup_fanout_lifecycle(
    run_directory: Path, lifecycle: dict[str, Any], *, interrupted: bool,
) -> None:
    """Stop local background work before releasing supervisor ownership."""
    finished = lifecycle.get("control_finished")
    if finished is not None:
        finished.set()
    pool = lifecycle.get("request_pool")
    if pool is not None:
        pool.stop()
    watcher = lifecycle.get("control_watcher")
    if watcher is not None and watcher.ident is not None:
        watcher.join(timeout=10)
        if watcher.is_alive():
            raise RuntimeError("Fanout control watcher did not stop")
    if pool is not None:
        pool.close()
    if not interrupted or not lifecycle.get("telemetry_owned"):
        return
    # The caller may catch this exception and keep its Python process alive.
    # Clear telemetry while the supervisor lock still excludes a replacement.
    try:
        status_path = run_directory / "status.json"
        if status_path.is_file():
            status = read_json(status_path)
            status.update(status="interrupted", running=0, current=[],
                          updated_at=utc_now())
            atomic_json(status_path, status)
        workers_path = run_directory / "workers.json"
        if workers_path.is_file():
            workers = read_json(workers_path)
            workers.update(active_workers=0, database_writers=0,
                           stopping=True, interrupted=True, updated_at=utc_now())
            if pool is not None:
                workers["request_pool"] = pool.snapshot()
            atomic_json(workers_path, workers)
        request_workers_path = run_directory / "request-workers.json"
        if request_workers_path.is_file():
            request_workers = read_json(request_workers_path)
            request_workers.update(active_workers=0, queued_requests=0,
                                   stopped=True, updated_at_epoch=time.time())
            atomic_json(request_workers_path, request_workers)
    except (OSError, ValueError) as error:
        print(f"WARNING: could not finalize interrupted fanout telemetry: {error}",
              file=sys.stderr, flush=True)


def main(arguments: Sequence[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    options = parse_arguments(arguments)
    run_directory = options.run_directory.resolve(strict=False)
    with supervisor_lock(run_directory) as owner_token:
        lifecycle: dict[str, Any] = {}
        interrupted = False
        try:
            assert_no_active_lane_writers(run_directory)
            assert_no_active_merge_writer(options.source_database.resolve(strict=True))
            return _main_unlocked(
                arguments, owner_token=owner_token, lifecycle=lifecycle,
            )
        except BaseException:
            interrupted = True
            raise
        finally:
            cleanup_fanout_lifecycle(
                run_directory, lifecycle, interrupted=interrupted,
            )


if __name__ == "__main__":
    raise SystemExit(main())

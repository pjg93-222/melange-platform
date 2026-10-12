"""Export a lossless, auditable raw-contribution warehouse from Mentat SQLite.

The warehouse is partitioned for human navigation as:

    INDUSTRY / CYCLE / COMPANY / SNAPSHOT_ID /

Each immutable partition contains:

* ``contributions.jsonl.gz`` and ``corporate_giving.jsonl.gz`` as the
  lossless, type-preserving source;
* matching ``.csv.gz`` files for spreadsheet/database interoperability; and
* ``manifest.json`` with row counts, schemas, byte sizes, and SHA-256 hashes.

The industry ``current.json`` pointer is published only after every expected
company x cycle partition has been written and verified.  Existing immutable
snapshots are reused when their hashes pass.  A corrupt existing snapshot is
quarantined rather than silently overwritten.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
import uuid
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence, TextIO


CYCLE_MAP = {
    "2021-2022": 2022,
    "2022": 2022,
    "2023-2024": 2024,
    "2024": 2024,
    "2025-2026": 2026,
    "2026": 2026,
}
CYCLE_LABELS = {
    2022: "2021-2022",
    2024: "2023-2024",
    2026: "2025-2026",
}
TABLE_SPECS = {
    "contributions": "two_year_transaction_period",
    "corporate_giving": "cycle",
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
INVALID_PATH_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WAREHOUSE_README = """Mentat raw contribution warehouse

Hierarchy:
  Industry / Cycle / Company / Snapshot ID /

Each snapshot contains both source tables:
  - contributions: individual/employee-side FEC receipt rows
  - corporate_giving: corporate/entity-side FEC receipt rows

The .jsonl.gz files are authoritative and lossless: they preserve SQLite NULL,
number, and text types. The .csv.gz files contain the same rows for spreadsheet
and database interoperability, but CSV cannot distinguish NULL from empty text.

No analytical filter is applied. Accepted, review, rejected, memo, refund,
amendment, superseded, and duplicate-version rows are retained. Treat all raw
text as untrusted data when opening CSV files in spreadsheet software.

Read Industry/current.json to locate the current immutable snapshot manifest.
Every file, partition, row count, and schema is covered by SHA-256 receipts.
"""

# Mandated address-field block and its canonical order for the raw
# contribution warehouse (S2, contract owner). The single source of truth is
# audits/mentat_failings_20260915/contracts/raw_contribution_columns.json:
# surfaces.raw_warehouse_contributions / raw_warehouse_corporate_giving carry
# the fully-placed headers this constant reproduces, block_db is this same
# tuple, and placement_rule is the rule contract_column_order() implements.
CONTRACT_ADDRESS_BLOCK = (
    "contributor_employer",
    "contributor_occupation",
    "contributor_street_1",
    "contributor_street_2",
    "contributor_city",
    "contributor_state",
    "contributor_zip",
)


def contract_column_order(
    names: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Reorder `names` so CONTRACT_ADDRESS_BLOCK sits contiguous and in block
    order at the position of its first present member.

    All other columns keep their relative order from `names`. Block members
    absent from `names` are simply omitted (never invented) and reported in
    the second return value, in block order. If no block member is present,
    `names` is returned unchanged and every block member is reported missing.

    Returns (ordered_columns, missing_block_members).
    """
    present = [name for name in CONTRACT_ADDRESS_BLOCK if name in names]
    missing = tuple(
        name for name in CONTRACT_ADDRESS_BLOCK if name not in names
    )
    if not present:
        return tuple(names), missing
    anchor = min(names.index(name) for name in present)
    remaining = [name for name in names if name not in CONTRACT_ADDRESS_BLOCK]
    prefix = [
        name for name in names[:anchor] if name not in CONTRACT_ADDRESS_BLOCK
    ]
    ordered = tuple(prefix) + tuple(present) + tuple(remaining[len(prefix):])
    return ordered, missing


def missing_columns_by_table(
    placements: dict[str, tuple[tuple[str, ...], tuple[str, ...]]],
) -> dict[str, list[str]]:
    """{table: missing block members} for the tables that are missing some.

    Tables with a complete block are left out, so an empty result means the
    whole export matched the contract and the receipt key can be omitted.
    """
    return {
        table: list(missing)
        for table, (_ordered, missing) in placements.items()
        if missing
    }


def warn_missing_contract_columns(missing: dict[str, list[str]]) -> None:
    """One stderr line per table that lacks contract columns."""
    for table, names in missing.items():
        print(
            f"warning: {table} lacks contract columns {names}; run"
            " init_schema() from mentat/R/schema.R before treating this export"
            " as complete",
            file=sys.stderr,
        )


def parse_cycles(value: str) -> tuple[int, ...]:
    labels = [
        item.strip()
        for item in value.replace(",", "|").split("|")
        if item.strip()
    ]
    unknown = sorted(set(labels).difference(CYCLE_MAP))
    if not labels or unknown:
        raise ValueError(
            "Cycles must use 2021-2022/2022, 2023-2024/2024, or "
            f"2025-2026/2026; invalid={unknown!r}"
        )
    return tuple(sorted({CYCLE_MAP[label] for label in labels}))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def replace_with_retry(
    source: Path,
    target: Path,
    attempts: int = 8,
) -> None:
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(0.05 * (2**attempt), 0.8))


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.pending"
    )
    try:
        with pending.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                value,
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(pending, target)
    finally:
        if pending.exists():
            pending.unlink()


def ensure_text(path: Path, value: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == value:
        return
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.pending"
    )
    try:
        with pending.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(pending, target)
    finally:
        if pending.exists():
            pending.unlink()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_component(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    component = INVALID_PATH_CHARACTERS.sub("_", normalized)
    component = re.sub(r"\s+", " ", component).rstrip(" .")
    if not component or component in {".", ".."}:
        component = f"unnamed__{hashlib.sha256(value.encode()).hexdigest()[:8]}"
    if component.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        component = f"_{component}"
    if len(component) > 96:
        suffix = hashlib.sha256(value.encode()).hexdigest()[:10]
        component = f"{component[:83].rstrip()}__{suffix}"
    return component


def unique_components(values: Iterable[str]) -> dict[str, str]:
    materialized = tuple(dict.fromkeys(values))
    bases = {value: safe_component(value) for value in materialized}
    collisions: dict[str, list[str]] = {}
    for value, base in bases.items():
        collisions.setdefault(base.casefold(), []).append(value)
    result: dict[str, str] = {}
    for value in materialized:
        base = bases[value]
        if len(collisions[base.casefold()]) > 1:
            suffix = hashlib.sha256(value.encode()).hexdigest()[:8]
            result[value] = f"{base}__{suffix}"
        else:
            result[value] = base
    return result


def quote_identifier(value: str) -> str:
    if value not in TABLE_SPECS:
        raise ValueError(f"Unsupported raw table: {value!r}")
    return '"' + value.replace('"', '""') + '"'


def placeholders(values: Iterable[object]) -> str:
    materialized = tuple(values)
    if not materialized:
        raise ValueError("At least one SQL value is required.")
    return ",".join("?" for _ in materialized)


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name=?
        """,
        (table,),
    ).fetchone()
    return row is not None


def table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[dict[str, Any], ...]:
    rows = connection.execute(
        f"PRAGMA table_info({quote_identifier(table)})"
    ).fetchall()
    if not rows:
        raise RuntimeError(f"Raw table has no schema: {table}")
    return tuple(
        {
            "ordinal": int(row[0]),
            "name": str(row[1]),
            "sqlite_type": str(row[2]),
            "not_null": bool(row[3]),
            "default": row[4],
            "primary_key_ordinal": int(row[5]),
        }
        for row in rows
    )


def expected_companies(
    connection: sqlite3.Connection,
    industry: str,
) -> tuple[str, ...]:
    if not table_exists(connection, "companies"):
        raise RuntimeError("Database is missing the companies roster table.")
    rows = connection.execute(
        """
        SELECT DISTINCT company
        FROM companies
        WHERE industry=?
        ORDER BY company COLLATE BINARY
        """,
        (industry,),
    ).fetchall()
    companies = tuple(
        str(row[0]).strip()
        for row in rows
        if row[0] is not None and str(row[0]).strip()
    )
    if not companies:
        raise RuntimeError(
            f"Database has no company roster for industry {industry!r}."
        )
    return companies


@contextmanager
def deterministic_gzip_text(path: Path) -> Iterator[TextIO]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw_stream:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=6,
            fileobj=raw_stream,
            mtime=0,
        ) as gzip_stream:
            with io.TextIOWrapper(
                gzip_stream,
                encoding="utf-8",
                newline="",
            ) as text_stream:
                yield text_stream


def open_partition_writers(
    stack: ExitStack,
    partition: Path,
    table: str,
    columns: tuple[str, ...],
) -> tuple[csv.writer, TextIO]:
    csv_stream = stack.enter_context(
        deterministic_gzip_text(partition / f"{table}.csv.gz")
    )
    json_stream = stack.enter_context(
        deterministic_gzip_text(partition / f"{table}.jsonl.gz")
    )
    csv_writer = csv.writer(csv_stream, lineterminator="\n")
    csv_writer.writerow(columns)
    return csv_writer, json_stream


def write_empty_table(
    partition: Path,
    table: str,
    columns: tuple[str, ...],
) -> None:
    with ExitStack() as stack:
        open_partition_writers(stack, partition, table, columns)


def as_json_object(
    columns: tuple[str, ...],
    row: tuple[Any, ...],
) -> dict[str, Any]:
    return dict(zip(columns, row, strict=True))


def export_table(
    connection: sqlite3.Connection,
    table: str,
    industry: str,
    cycles: tuple[int, ...],
    companies: tuple[str, ...],
    company_components: dict[str, str],
    pending_root: Path,
    schemas: dict[str, tuple[dict[str, Any], ...]],
    logical_digest: Any,
) -> tuple[dict[tuple[int, str], int], int]:
    cycle_column = TABLE_SPECS[table]
    columns, _missing_block_members = contract_column_order(
        tuple(item["name"] for item in schemas[table])
    )
    column_sql = ", ".join(
        '"' + column.replace('"', '""') + '"' for column in columns
    )
    company_sql = placeholders(companies)
    cycle_sql = placeholders(cycles)
    query = f"""
        SELECT {column_sql}
        FROM {quote_identifier(table)}
        WHERE industry=?
          AND company IN ({company_sql})
          AND "{cycle_column}" IN ({cycle_sql})
        ORDER BY company COLLATE BINARY,
                 "{cycle_column}",
                 sub_id COLLATE BINARY
    """
    expected_keys = {
        (cycle, company)
        for company in companies
        for cycle in cycles
    }
    row_counts = {key: 0 for key in expected_keys}
    seen_keys: set[tuple[int, str]] = set()
    current_key: tuple[int, str] | None = None
    stack: ExitStack | None = None
    csv_writer: csv.writer | None = None
    json_stream: TextIO | None = None
    total = 0
    try:
        cursor = connection.execute(
            query,
            (industry, *companies, *cycles),
        )
        for raw_row in cursor:
            row = tuple(raw_row)
            company = str(row[columns.index("company")])
            cycle = int(row[columns.index(cycle_column)])
            key = (cycle, company)
            if key not in expected_keys:
                raise RuntimeError(
                    f"Unexpected {table} partition encountered: {key!r}"
                )
            if key != current_key:
                if stack is not None:
                    stack.close()
                stack = ExitStack()
                partition = (
                    pending_root
                    / CYCLE_LABELS[cycle]
                    / company_components[company]
                )
                csv_writer, json_stream = open_partition_writers(
                    stack,
                    partition,
                    table,
                    columns,
                )
                current_key = key
                seen_keys.add(key)
            assert csv_writer is not None
            assert json_stream is not None
            csv_writer.writerow(row)
            json_line = canonical_json(as_json_object(columns, row))
            json_stream.write(json_line.decode("utf-8"))
            logical_digest.update(canonical_json([table, company, cycle, row]))
            row_counts[key] += 1
            total += 1
    finally:
        if stack is not None:
            stack.close()

    for cycle, company in sorted(
        expected_keys.difference(seen_keys),
        key=lambda item: (item[0], item[1]),
    ):
        partition = (
            pending_root
            / CYCLE_LABELS[cycle]
            / company_components[company]
        )
        write_empty_table(partition, table, columns)
    return row_counts, total


def scoped_counts(
    connection: sqlite3.Connection,
    table: str,
    industry: str,
    cycles: tuple[int, ...],
    companies: tuple[str, ...],
) -> dict[str, int]:
    cycle_column = TABLE_SPECS[table]
    company_sql = placeholders(companies)
    cycle_sql = placeholders(cycles)
    selected = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM {quote_identifier(table)}
            WHERE industry=?
              AND company IN ({company_sql})
              AND "{cycle_column}" IN ({cycle_sql})
            """,
            (industry, *companies, *cycles),
        ).fetchone()[0]
    )
    roster_any_industry = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM {quote_identifier(table)}
            WHERE company IN ({company_sql})
              AND "{cycle_column}" IN ({cycle_sql})
            """,
            (*companies, *cycles),
        ).fetchone()[0]
    )
    unknown_company = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM {quote_identifier(table)}
            WHERE industry=?
              AND company NOT IN ({company_sql})
              AND "{cycle_column}" IN ({cycle_sql})
            """,
            (industry, *companies, *cycles),
        ).fetchone()[0]
    )
    if selected != roster_any_industry:
        raise RuntimeError(
            f"{table} has {roster_any_industry - selected} selected-cycle "
            "rows for the industry roster tagged to another industry."
        )
    if unknown_company:
        raise RuntimeError(
            f"{table} has {unknown_company} selected-cycle rows tagged to "
            f"{industry!r} but absent from its company roster."
        )
    return {
        "selected": selected,
        "roster_any_industry": roster_any_industry,
        "unknown_company": unknown_company,
    }


def file_receipt(path: Path, relative_to: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def verify_partition(
    partition: Path,
    expected_manifest: dict[str, Any],
) -> bool:
    manifest_path = partition / "manifest.json"
    try:
        with manifest_path.open(encoding="utf-8") as stream:
            observed = json.load(stream)
        if observed != expected_manifest:
            return False
        for table in TABLE_SPECS:
            for format_name in ("csv_gzip", "jsonl_gzip"):
                receipt = expected_manifest["tables"][table]["files"][
                    format_name
                ]
                path = partition / Path(receipt["path"]).name
                if not path.is_file():
                    return False
                if path.stat().st_size != receipt["bytes"]:
                    return False
                if sha256_file(path) != receipt["sha256"]:
                    return False
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def rebuild_catalog(output_root: Path) -> Path:
    industries: list[dict[str, Any]] = []
    for industry_root in sorted(
        (path for path in output_root.iterdir() if path.is_dir()),
        key=lambda path: (path.name.casefold(), path.name),
    ):
        current_path = industry_root / "current.json"
        if not current_path.is_file():
            continue
        try:
            with current_path.open(encoding="utf-8") as stream:
                current = json.load(stream)
            snapshot_id = str(current["snapshot_id"])
            manifest_path = (
                industry_root / "manifests" / f"{snapshot_id}.json"
            )
            with manifest_path.open(encoding="utf-8") as stream:
                manifest = json.load(stream)
            if (
                current.get("status") != "succeeded"
                or manifest.get("status") != "succeeded"
                or current.get("logical_sha256")
                != manifest.get("logical_sha256")
            ):
                continue
            industries.append(
                {
                    "industry": str(current["industry"]),
                    "path": industry_root.relative_to(
                        output_root
                    ).as_posix(),
                    "current_manifest": current_path.relative_to(
                        output_root
                    ).as_posix(),
                    "snapshot_manifest": manifest_path.relative_to(
                        output_root
                    ).as_posix(),
                    "snapshot_id": snapshot_id,
                    "logical_sha256": str(current["logical_sha256"]),
                    "cycles": list(manifest["cycles"]),
                    "company_count": len(manifest["companies"]),
                    "partition_count": int(manifest["partition_count"]),
                    "rows": {
                        table: int(
                            manifest["tables"][table]["row_count"]
                        )
                        for table in TABLE_SPECS
                    },
                }
            )
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            continue
    catalog_path = output_root / "catalog.json"
    atomic_write_json(
        catalog_path,
        {
            "schema_version": 1,
            "status": "succeeded",
            "industry_count": len(industries),
            "industries": industries,
        },
    )
    return catalog_path


def try_verified_fast_reuse(
    database: Path,
    source_database_sha256: str,
    output_root: Path,
    industry_root: Path,
    industry: str,
    cycles: tuple[int, ...],
    started: float,
) -> dict[str, Any] | None:
    wal_path = Path(f"{database}-wal")
    if wal_path.exists() and wal_path.stat().st_size:
        return None
    current_path = industry_root / "current.json"
    try:
        with current_path.open(encoding="utf-8") as stream:
            current = json.load(stream)
        if (
            current.get("status") != "succeeded"
            or current.get("industry") != industry
            or current.get("source_database_sha256")
            != source_database_sha256
        ):
            return None
        snapshot_id = str(current["snapshot_id"])
        manifest_path = industry_root / "manifests" / f"{snapshot_id}.json"
        with manifest_path.open(encoding="utf-8") as stream:
            manifest = json.load(stream)
        manifest_cycles = tuple(
            int(item["cycle"]) for item in manifest["cycles"]
        )
        if (
            manifest.get("status") != "succeeded"
            or manifest.get("industry") != industry
            or manifest.get("snapshot_id") != snapshot_id
            or manifest.get("logical_sha256")
            != current.get("logical_sha256")
            or not str(manifest.get("logical_sha256", "")).startswith(
                snapshot_id
            )
            or manifest_cycles != cycles
            or manifest.get("partition_count")
            != manifest.get("expected_partition_count")
            or manifest.get("partition_count") != len(manifest["partitions"])
        ):
            return None
        companies = [
            str(item["company"]) for item in manifest["companies"]
        ]
        expected_grid = {
            (cycle, company)
            for cycle in cycles
            for company in companies
        }
        observed_grid = {
            (int(item["cycle"]), str(item["company"]))
            for item in manifest["partitions"]
        }
        if observed_grid != expected_grid:
            return None
        for table in TABLE_SPECS:
            partition_rows = sum(
                int(item["rows"][table])
                for item in manifest["partitions"]
            )
            if partition_rows != int(
                manifest["tables"][table]["row_count"]
            ):
                return None
        output_root_resolved = output_root.resolve()
        for partition_receipt in manifest["partitions"]:
            partition = (
                output_root_resolved / partition_receipt["path"]
            ).resolve()
            partition.relative_to(output_root_resolved)
            with (partition / "manifest.json").open(
                encoding="utf-8"
            ) as stream:
                partition_manifest = json.load(stream)
            if sha256_file(partition / "manifest.json") != (
                partition_receipt["manifest_sha256"]
            ):
                return None
            if not verify_partition(partition, partition_manifest):
                return None
        rows = {
            table: int(manifest["tables"][table]["row_count"])
            for table in TABLE_SPECS
        }
        # Reusing a verified snapshot must not silently drop the schema-gap
        # signal, so recompute it from the schema the manifest already stores.
        # Same inputs as the full path, therefore the same answer. A malformed
        # manifest raises KeyError/TypeError and is caught below, which falls
        # back to the full export - the existing behaviour for bad manifests.
        fast_missing = missing_columns_by_table(
            {
                table: contract_column_order(
                    tuple(
                        item["name"]
                        for item in manifest["tables"][table]["schema"]
                    )
                )
                for table in TABLE_SPECS
            }
        )
        warn_missing_contract_columns(fast_missing)
        catalog_path = rebuild_catalog(output_root_resolved)
        elapsed = round(time.perf_counter() - started, 3)
        return {
            "status": "succeeded",
            "industry": industry,
            "cycles": list(cycles),
            "companies": companies,
            "company_count": len(companies),
            "partition_count": int(manifest["partition_count"]),
            "snapshot_id": snapshot_id,
            "logical_sha256": str(manifest["logical_sha256"]),
            "source_database": str(database),
            "source_database_sha256": source_database_sha256,
            "warehouse_root": str(output_root_resolved),
            "industry_root": str(industry_root),
            "current_manifest": str(current_path),
            "snapshot_manifest": str(manifest_path),
            "catalog": str(catalog_path),
            "rows": rows,
            "reused_partitions": int(manifest["partition_count"]),
            "new_partitions": 0,
            "quarantined_corrupt_paths": [],
            "sqlite_quick_check": ["reused_verified_source_sha256"],
            "foreign_key_failures": 0,
            "fast_path": True,
            "elapsed_seconds": elapsed,
            **({"missing_contract_columns": fast_missing} if fast_missing else {}),
        }
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def publish_partition(
    staged: Path,
    target: Path,
    manifest: dict[str, Any],
) -> tuple[bool, str | None]:
    if target.exists() and verify_partition(target, manifest):
        shutil.rmtree(staged)
        return True, None

    quarantined: str | None = None
    if target.exists():
        quarantine = target.with_name(
            f".corrupt-{target.name}-{uuid.uuid4().hex}"
        )
        replace_with_retry(target, quarantine)
        quarantined = str(quarantine)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        replace_with_retry(staged, target)
    except OSError:
        if target.exists() and verify_partition(target, manifest):
            shutil.rmtree(staged)
            return True, quarantined
        raise
    if not verify_partition(target, manifest):
        raise RuntimeError(
            f"Published raw partition failed hash verification: {target}"
        )
    return False, quarantined


def export_warehouse(
    database: Path,
    output_root: Path,
    industry: str,
    cycles: tuple[int, ...],
) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_database = database.resolve(strict=True)
    resolved_output = output_root.resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    ensure_text(resolved_output / "README.txt", WAREHOUSE_README)
    industry_component = safe_component(industry)
    industry_root = resolved_output / industry_component
    industry_root.mkdir(parents=True, exist_ok=True)
    source_database_sha256 = sha256_file(resolved_database)
    reused_result = try_verified_fast_reuse(
        resolved_database,
        source_database_sha256,
        resolved_output,
        industry_root,
        industry,
        cycles,
        started,
    )
    if reused_result is not None:
        return reused_result
    pending_root = industry_root / (
        f".pending-{os.getpid()}-{uuid.uuid4().hex}"
    )
    pending_root.mkdir()
    reused = 0
    quarantined: list[str] = []

    try:
        uri = resolved_database.as_uri() + "?mode=ro"
        with closing(
            sqlite3.connect(uri, uri=True, timeout=60)
        ) as connection:
            connection.execute("PRAGMA busy_timeout=60000")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            quick_check = [
                str(row[0])
                for row in connection.execute("PRAGMA quick_check")
            ]
            foreign_key_failures = sum(
                1 for _ in connection.execute("PRAGMA foreign_key_check")
            )
            if quick_check != ["ok"]:
                raise RuntimeError(
                    f"SQLite quick_check failed: {quick_check!r}"
                )
            if foreign_key_failures:
                raise RuntimeError(
                    "SQLite foreign_key_check found "
                    f"{foreign_key_failures} failure(s)."
                )
            for table in TABLE_SPECS:
                if not table_exists(connection, table):
                    raise RuntimeError(
                        f"Database is missing required raw table: {table}"
                    )
            companies = expected_companies(connection, industry)
            company_components = unique_components(companies)
            schemas = {
                table: table_columns(connection, table)
                for table in TABLE_SPECS
            }
            count_checks = {
                table: scoped_counts(
                    connection,
                    table,
                    industry,
                    cycles,
                    companies,
                )
                for table in TABLE_SPECS
            }
            logical_digest = hashlib.sha256()
            logical_digest.update(
                canonical_json(
                    {
                        "schema_version": 1,
                        "industry": industry,
                        "cycles": list(cycles),
                        "companies": list(companies),
                        "schemas": schemas,
                        "row_policy": "all_scoped_database_rows",
                    }
                )
            )
            table_counts: dict[
                str, dict[tuple[int, str], int]
            ] = {}
            exported_totals: dict[str, int] = {}
            for table in TABLE_SPECS:
                row_counts, total = export_table(
                    connection,
                    table,
                    industry,
                    cycles,
                    companies,
                    company_components,
                    pending_root,
                    schemas,
                    logical_digest,
                )
                if total != count_checks[table]["selected"]:
                    raise RuntimeError(
                        f"{table} export count mismatch: selected="
                        f"{count_checks[table]['selected']}, exported={total}"
                    )
                table_counts[table] = row_counts
                exported_totals[table] = total

        logical_sha256 = logical_digest.hexdigest()
        snapshot_id = logical_sha256[:20]
        # One placement computation per table, reused for every partition's
        # manifest below, so the recorded `columns` always matches what
        # export_table() actually wrote to the CSV/JSONL for that table.
        contract_columns = {
            table: contract_column_order(
                tuple(item["name"] for item in schemas[table])
            )
            for table in TABLE_SPECS
        }
        # A table that lacks contract columns still exports, and the export is
        # still `"status": "succeeded"`: the rows written are a complete record
        # of what the database actually holds, and
        # tools/parallel/Start-MentatResilientIndustryReports.ps1 keys its
        # exit-code contract on that field. Failing here would turn a schema
        # gap into a pipeline outage. So the gap is surfaced at receipt level
        # instead - one stderr line per table, plus a top-level receipt key.
        # The key is OMITTED entirely when nothing is missing, so a migrated
        # database's receipt keeps exactly the shape it had before.
        missing_contract_columns = missing_columns_by_table(contract_columns)
        warn_missing_contract_columns(missing_contract_columns)
        partitions: list[dict[str, Any]] = []
        for cycle in cycles:
            cycle_label = CYCLE_LABELS[cycle]
            for company in companies:
                company_component = company_components[company]
                staged = pending_root / cycle_label / company_component
                target = (
                    industry_root
                    / cycle_label
                    / company_component
                    / snapshot_id
                )
                table_manifests: dict[str, Any] = {}
                for table in TABLE_SPECS:
                    csv_path = staged / f"{table}.csv.gz"
                    jsonl_path = staged / f"{table}.jsonl.gz"
                    ordered_columns, missing_columns = contract_columns[table]
                    table_manifests[table] = {
                        "row_count": table_counts[table][(cycle, company)],
                        "columns": list(ordered_columns),
                        "missing_contract_columns": list(missing_columns),
                        "files": {
                            "csv_gzip": file_receipt(csv_path, staged),
                            "jsonl_gzip": file_receipt(jsonl_path, staged),
                        },
                    }
                partition_manifest = {
                    "schema_version": 1,
                    "status": "succeeded",
                    "snapshot_id": snapshot_id,
                    "logical_sha256": logical_sha256,
                    "industry": industry,
                    "cycle": cycle,
                    "cycle_label": cycle_label,
                    "company": company,
                    "row_policy": (
                        "Every scoped database row; no analytical, match-"
                        "status, memo, refund, amendment, or duplicate filter."
                    ),
                    "lossless_format": "jsonl_gzip",
                    "tables": table_manifests,
                }
                atomic_write_json(staged / "manifest.json", partition_manifest)
                was_reused, quarantine = publish_partition(
                    staged,
                    target,
                    partition_manifest,
                )
                reused += int(was_reused)
                if quarantine is not None:
                    quarantined.append(quarantine)
                partitions.append(
                    {
                        "cycle": cycle,
                        "cycle_label": cycle_label,
                        "company": company,
                        "path": target.relative_to(
                            resolved_output
                        ).as_posix(),
                        "rows": {
                            table: table_counts[table][(cycle, company)]
                            for table in TABLE_SPECS
                        },
                        "manifest_sha256": sha256_file(
                            target / "manifest.json"
                        ),
                    }
                )

        snapshot_manifest = {
            "schema_version": 1,
            "status": "succeeded",
            "snapshot_id": snapshot_id,
            "logical_sha256": logical_sha256,
            "industry": industry,
            "industry_path_component": industry_component,
            "cycles": [
                {"cycle": cycle, "label": CYCLE_LABELS[cycle]}
                for cycle in cycles
            ],
            "companies": [
                {
                    "company": company,
                    "path_component": company_components[company],
                }
                for company in companies
            ],
            "partition_count": len(partitions),
            "expected_partition_count": len(companies) * len(cycles),
            "tables": {
                table: {
                    "row_count": exported_totals[table],
                    "schema": list(schemas[table]),
                }
                for table in TABLE_SPECS
            },
            "row_policy": (
                "Every scoped database row; no analytical, match-status, "
                "memo, refund, amendment, or duplicate filter."
            ),
            "lossless_format": "jsonl_gzip",
            "spreadsheet_format": "csv_gzip",
            "partitions": partitions,
        }
        manifest_path = (
            industry_root / "manifests" / f"{snapshot_id}.json"
        )
        if manifest_path.exists():
            try:
                with manifest_path.open(encoding="utf-8") as stream:
                    existing_manifest = json.load(stream)
            except (OSError, ValueError, json.JSONDecodeError):
                existing_manifest = None
            if existing_manifest != snapshot_manifest:
                quarantine = manifest_path.with_name(
                    f".corrupt-{manifest_path.name}-{uuid.uuid4().hex}"
                )
                replace_with_retry(manifest_path, quarantine)
                quarantined.append(str(quarantine))
                atomic_write_json(manifest_path, snapshot_manifest)
        else:
            atomic_write_json(manifest_path, snapshot_manifest)

        current_path = industry_root / "current.json"
        current = {
            "schema_version": 1,
            "status": "succeeded",
            "published_at": utc_now(),
            "industry": industry,
            "snapshot_id": snapshot_id,
            "logical_sha256": logical_sha256,
            "source_database": str(resolved_database),
            "source_database_sha256": source_database_sha256,
            "snapshot_manifest": str(manifest_path),
            "warehouse_root": str(resolved_output),
        }
        atomic_write_json(current_path, current)
        catalog_path = rebuild_catalog(resolved_output)
        elapsed = round(time.perf_counter() - started, 3)
        return {
            "status": "succeeded",
            "industry": industry,
            "cycles": list(cycles),
            "companies": list(companies),
            "company_count": len(companies),
            "partition_count": len(partitions),
            "snapshot_id": snapshot_id,
            "logical_sha256": logical_sha256,
            "source_database": str(resolved_database),
            "source_database_sha256": source_database_sha256,
            "warehouse_root": str(resolved_output),
            "industry_root": str(industry_root),
            "current_manifest": str(current_path),
            "snapshot_manifest": str(manifest_path),
            "catalog": str(catalog_path),
            "rows": exported_totals,
            "reused_partitions": reused,
            "new_partitions": len(partitions) - reused,
            "quarantined_corrupt_paths": quarantined,
            "sqlite_quick_check": quick_check,
            "foreign_key_failures": foreign_key_failures,
            "fast_path": False,
            "elapsed_seconds": elapsed,
            **(
                {"missing_contract_columns": missing_contract_columns}
                if missing_contract_columns
                else {}
            ),
        }
    finally:
        if pending_root.exists():
            shutil.rmtree(pending_root)


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("industry")
    parser.add_argument("cycles")
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--receipt", type=Path)
    return parser.parse_args(arguments)


def main(arguments: list[str]) -> int:
    options = parse_arguments(arguments)
    receipt_path = options.receipt
    try:
        result = export_warehouse(
            options.database,
            options.output_root,
            options.industry.strip(),
            parse_cycles(options.cycles),
        )
    except Exception as error:
        result = {
            "status": "failed",
            "industry": options.industry.strip(),
            "source_database": str(options.database.resolve()),
            "warehouse_root": str(options.output_root.resolve()),
            "error": f"{type(error).__name__}: {error}",
        }
    if receipt_path is not None:
        atomic_write_json(receipt_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

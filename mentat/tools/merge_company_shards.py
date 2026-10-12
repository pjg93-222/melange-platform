"""Safely merge isolated Mentat company shards into one SQLite database.

Usage:
    python merge_company_shards.py TARGET \
      --company-order-json '["Company", "Company"]' \
      "SHARD_DB::Company|Company" ["SHARD_DB::Company|Company" ...]

TARGET must already exist. Shards are attached read-only, schemas and
quick_check are verified before any write, and the entire merge is committed
as one transaction. A company may be assigned to exactly one shard. More than
--merge-batch-limit shards (default 10, SQLite's attached-database limit) are
merged into a staged copy of TARGET across consecutive batches; the prepared
state is published through one TARGET SQLite transaction, and TARGET is left
untouched if preparation or publication fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence


COMMIT_TABLE = "mentat_merge_commits"


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def content_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def logical_database_sha256(connection: sqlite3.Connection) -> str:
    """Hash every material schema object and typed row, excluding the ledger.

    This is independent of SQLite page layout, checkpoints and journal mode.
    Rows use a type-tagged, order-independent cryptographic multiset digest.
    This avoids unstable ties under SQLite collations or nullable composite PKs.
    """
    digest = hashlib.sha256()

    def add(value: object) -> None:
        encoded = canonical_json(value)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    schema = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE tbl_name<>? AND name<>? ORDER BY type,name",
        (COMMIT_TABLE, COMMIT_TABLE),
    ).fetchall()
    for item in schema:
        add(["schema", *item])
    for kind, name, _, _ in schema:
        if kind != "table":
            continue
        columns = [row[1] for row in connection.execute(
            f"PRAGMA table_xinfo({quoted(name)})"
        ) if row[6] != 1]  # hidden virtual-table columns are not SELECT * rows
        if not columns:
            continue
        add(["table", name, columns])
        sql = (f"SELECT {','.join(quoted(column) for column in columns)} "
               f"FROM {quoted(name)}")
        count = 0
        sum256 = 0
        xor256 = 0
        mask256 = (1 << 256) - 1
        for row in connection.execute(sql):
            typed = []
            for value in row:
                if value is None:
                    typed.append(["null"])
                elif isinstance(value, bytes):
                    typed.append(["blob", value.hex()])
                elif isinstance(value, str):
                    typed.append(["text", value])
                elif isinstance(value, int):
                    typed.append(["int", str(value)])
                elif isinstance(value, float):
                    typed.append(["real", value.hex()])
                else:
                    raise TypeError(f"Unsupported SQLite value type: {type(value)}")
            row_hash = int.from_bytes(hashlib.sha256(canonical_json(typed)).digest(), "big")
            count += 1
            sum256 = (sum256 + row_hash) & mask256
            xor256 ^= row_hash
        add(["rows", name, count, f"{sum256:064x}", f"{xor256:064x}"])
    return digest.hexdigest()


def ensure_commit_table(connection: sqlite3.Connection) -> None:
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS {quoted(COMMIT_TABLE)} (
            run_id TEXT PRIMARY KEY,
            intent_sha256 TEXT NOT NULL,
            intent_path TEXT NOT NULL,
            prepared_receipt_sha256 TEXT NOT NULL,
            output_logical_sha256 TEXT NOT NULL,
            committed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def write_commit_marker(
    connection: sqlite3.Connection, intent: dict[str, object] | None,
    intent_path: Path | None,
) -> str | None:
    if intent is None:
        return None
    if intent_path is None or not connection.in_transaction:
        raise RuntimeError("Merge marker requires a live target transaction")
    ensure_commit_table(connection)
    output_hash = logical_database_sha256(connection)
    connection.execute(
        f"INSERT INTO {quoted(COMMIT_TABLE)} "
        "(run_id,intent_sha256,intent_path,prepared_receipt_sha256,"
        "output_logical_sha256) VALUES (?,?,?,?,?)",
        (intent["run_id"], content_sha256(intent), str(intent_path),
         intent["prepared_receipt_sha256"], output_hash),
    )
    return output_hash


@contextmanager
def committed_merge_lease(target: Path, run_id: str) -> Iterator[dict[str, str] | None]:
    """Keep one target write lease across evidence checks and receipt writing."""
    with merge_writer_lock(target):
        connection = target_connection(target)
        try:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (COMMIT_TABLE,),
            ).fetchone()
            if not exists:
                yield None
            else:
                row = connection.execute(
                    f"SELECT run_id,intent_sha256,intent_path,prepared_receipt_sha256,"
                    f"output_logical_sha256 FROM {quoted(COMMIT_TABLE)} WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    yield None
                else:
                    names = ("run_id", "intent_sha256", "intent_path",
                             "prepared_receipt_sha256", "output_logical_sha256")
                    result = dict(zip(names, row))
                    if logical_database_sha256(connection) != result["output_logical_sha256"]:
                        raise RuntimeError(
                            "Committed merge source contents changed after publication"
                        )
                    yield result
        finally:
            connection.rollback()
            connection.close()


def committed_merge(target: Path, run_id: str) -> dict[str, str] | None:
    with committed_merge_lease(target, run_id) as marker:
        return marker


def load_merge_intent(
    path: Path, target: Path,
    specifications: Sequence[tuple[Path, tuple[str, ...]]],
    expected_target_sha256: str | None,
) -> dict[str, object]:
    intent = json.loads(path.read_text(encoding="utf-8"))
    expected_specs = [
        {"database": str(shard), "sha256": quiescent_file_sha256(shard),
         "companies": list(companies)}
        for shard, companies in specifications
    ]
    if (
        intent.get("version") != 1
        or not isinstance(intent.get("run_id"), str)
        or not intent["run_id"]
        or intent.get("source_database") != str(target)
        or intent.get("source_state_before") != expected_target_sha256
        or intent.get("specifications") != expected_specs
        or not isinstance(intent.get("prepared_receipt_sha256"), str)
    ):
        raise RuntimeError("Merge intent does not match target and shard inputs")
    prepared = Path(str(intent["prepared_receipt_path"]))
    if not prepared.is_file() or file_sha256(prepared) != intent["prepared_receipt_sha256"]:
        raise RuntimeError("Prepared fanout receipt changed before merge")
    return intent


# Tables populated or updated by collection. Static roster/register tables
# (companies, categories, leadership, trade, executives, etc.) remain owned by
# the initialized target database and are deliberately not copied from shards.
REQUIRED_COLLECTOR_TABLES = (
    "contributions",
    "corporate_giving",
    "scrape_progress",
    "api_work_progress",
    "api_page_checkpoints",
    "observed_employers",
    "bulk_candidates",
    "review_queue_identity",
    "attribution_conflicts",
    "canary_log",
    "run_log",
    "run_metrics",
)

COMPANY_TABLES = {
    "contributions": "company",
    "corporate_giving": "company",
    "scrape_progress": "company",
    "api_work_progress": "company",
    "bulk_candidates": "company",
    "review_queue_identity": "company",
    "canary_log": "company",
}

GLOBAL_APPEND_TABLES = (
    "observed_employers",
    "run_log",
    "run_metrics",
)

# Optional only for pre-archive databases. When a shard has evidence, the
# initialized target must have a compatible schema; silently dropping it is
# never an acceptable legacy fallback. Preserve complete response archives,
# including competing claims outside a shard's selected reporting companies.
EVIDENCE_TABLES = (
    "fec_response_archive", "fec_response_observations",
    "attribution_claims", "source_record_conflicts",
)
EVIDENCE_TIMESTAMPS = frozenset({"created_at", "observed_at", "last_observed_at"})

# These rows retain their source primary keys during the merge. Any duplicate
# key must therefore describe the exact same logical row. Progress/checkpoint
# tables are excluded because their differing versions are resolved by the
# explicit winner logic below; review/canary surrogate IDs are not copied.
EXACT_PARITY_TABLES = (
    "contributions",
    "corporate_giving",
    "bulk_candidates",
    "attribution_conflicts",
    *GLOBAL_APPEND_TABLES,
)

CORPORATE_VERSION_FIELDS = frozenset(
    {
        "transaction_id",
        "original_sub_id",
        "amendment_indicator",
        "report_year",
        "report_type",
        "file_num",
        "pdf_url",
        "load_date",
    }
)

CORPORATE_REJECTED_ATTRIBUTION_FIELDS = frozenset(
    {
        "company",
        "matched_variant",
        "scraped_at",
        "company_match_status",
        "company_match_reason",
        "excluded_reason",
    }
)

# Non-FEC provenance columns are discovered from the live contribution schema.
# The attribution prefixes include match-confidence/status/flag columns.
# `scraped_at` is the only collection stamp in the current contributions DDL;
# run IDs and lane IDs live in other tables and therefore are not allowed here.
ATTRIBUTION_COLUMN_ANCHORS = frozenset(
    {
        "company",
        "matched_variant",
        "phase",
        "excluded_reason",
    }
)
ATTRIBUTION_COLUMN_PREFIXES = ("match_", "company_match_")
COLLECTION_PROVENANCE_COLUMN_ANCHORS = frozenset({"scraped_at"})
DETERMINISTIC_CONFLICT_TIMESTAMP = "1970-01-01T00:00:00.000+0000"


@dataclass(frozen=True)
class AttributionConflict:
    sub_id: str
    stored_company: str
    incoming_company: str
    incoming_phase: str
    created_at: str


@dataclass(frozen=True)
class AttributionResolution:
    table: str
    sub_id: str
    winner: tuple[object, ...]
    conflicts: tuple[AttributionConflict, ...]


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def placeholders(values: Sequence[object]) -> str:
    if not values:
        raise ValueError("SQL placeholder list cannot be empty")
    return ",".join("?" for _ in values)


def table_info(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
) -> list[tuple[object, ...]]:
    return list(
        connection.execute(
            f"PRAGMA {quoted(schema)}.table_info({quoted(table)})"
        )
    )


def table_columns(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
) -> list[str]:
    return [str(row[1]) for row in table_info(connection, schema, table)]


def primary_key_columns(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
) -> list[str]:
    rows = table_info(connection, schema, table)
    return [
        str(row[1])
        for row in sorted(
            (row for row in rows if int(row[5]) > 0),
            key=lambda row: int(row[5]),
        )
    ]


def unique_signatures(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
) -> set[tuple[str, int, tuple[str, ...]]]:
    signatures: set[tuple[str, int, tuple[str, ...]]] = set()
    indexes = list(
        connection.execute(
            f"PRAGMA {quoted(schema)}.index_list({quoted(table)})"
        )
    )
    for row in indexes:
        # seq, name, unique, origin, partial
        if int(row[2]) != 1:
            continue
        index_name = str(row[1])
        columns = tuple(
            str(item[2])
            for item in connection.execute(
                f"PRAGMA {quoted(schema)}.index_info({quoted(index_name)})"
            )
        )
        signatures.add((str(row[3]), int(row[4]), columns))
    return signatures


def schema_signature(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
) -> tuple[tuple[tuple[object, ...], ...], frozenset[tuple[str, int, tuple[str, ...]]]]:
    columns = tuple(
        (
            str(row[1]),
            str(row[2]).upper(),
            int(row[3]),
            None if row[4] is None else str(row[4]),
            int(row[5]),
        )
        for row in table_info(connection, schema, table)
    )
    return columns, frozenset(unique_signatures(connection, schema, table))


def quick_check(connection: sqlite3.Connection, schema: str) -> None:
    rows = [
        str(row[0])
        for row in connection.execute(
            f"PRAGMA {quoted(schema)}.quick_check"
        )
    ]
    if rows != ["ok"]:
        raise RuntimeError(
            f"{schema} quick_check failed: {'; '.join(rows) or 'no result'}"
        )


def ensure_schema_compatible(
    connection: sqlite3.Connection,
    schema: str,
    shard_path: Path,
) -> None:
    for table in REQUIRED_COLLECTOR_TABLES:
        main_signature = schema_signature(connection, "main", table)
        shard_signature = schema_signature(connection, schema, table)
        if not main_signature[0]:
            raise RuntimeError(f"target is missing required table {table}")
        if not shard_signature[0]:
            raise RuntimeError(
                f"shard {shard_path} is missing required table {table}"
            )
        if shard_signature != main_signature:
            raise RuntimeError(
                f"{table} schema/uniqueness mismatch in {shard_path}"
            )
    for table in EVIDENCE_TABLES:
        shard_signature = schema_signature(connection, schema, table)
        if not shard_signature[0]:
            continue
        main_signature = schema_signature(connection, "main", table)
        if not main_signature[0]:
            raise RuntimeError(f"target is missing evidence table {table}; initialize its schema before merging")
        if shard_signature != main_signature:
            raise RuntimeError(f"{table} evidence schema/uniqueness mismatch in {shard_path}")


def null_safe_equal(
    left_alias: str,
    right_alias: str,
    columns: Iterable[str],
) -> str:
    return " AND ".join(
        f"{left_alias}.{quoted(column)} IS {right_alias}.{quoted(column)}"
        for column in columns
    )


def contribution_provenance_columns(
    connection: sqlite3.Connection,
) -> frozenset[str]:
    columns = frozenset(table_columns(connection, "main", "contributions"))
    attribution = frozenset(
        column
        for column in columns
        if column in ATTRIBUTION_COLUMN_ANCHORS
        or column in COLLECTION_PROVENANCE_COLUMN_ANCHORS
        or column.startswith(ATTRIBUTION_COLUMN_PREFIXES)
    )
    if "company" not in attribution:
        raise RuntimeError("contributions schema has no company attribution column")
    return attribution


def merge_selection(
    table: str,
    alias: str,
    companies: Sequence[str] | None,
) -> tuple[str, tuple[object, ...]]:
    if companies is None or table in GLOBAL_APPEND_TABLES:
        return "1", ()
    if table in COMPANY_TABLES:
        company_column = COMPANY_TABLES[table]
        return (
            f"{alias}.{quoted(company_column)} IN "
            f"({placeholders(companies)})",
            tuple(companies),
        )
    if table == "attribution_conflicts":
        company_sql = placeholders(companies)
        return (
            f"({alias}.stored_company IN ({company_sql}) "
            f"OR {alias}.incoming_company IN ({company_sql}))",
            tuple(companies) + tuple(companies),
        )
    raise ValueError(f"No exact-parity merge selection for {table}")


def audit_exact_pk_pair(
    connection: sqlite3.Connection,
    table: str,
    left_schema: str,
    left_label: str,
    left_companies: Sequence[str] | None,
    right_schema: str,
    right_label: str,
    right_companies: Sequence[str] | None,
    allowed_provenance_columns: frozenset[str],
) -> list[tuple[tuple[object, ...], tuple[object, ...]]]:
    columns = table_columns(connection, "main", table)
    pk_columns = primary_key_columns(connection, "main", table)
    if not pk_columns:
        raise RuntimeError(f"{table} has no primary key")
    key_match = null_safe_equal("l", "r", pk_columns)
    row_match = null_safe_equal("l", "r", columns)
    left_where, left_parameters = merge_selection(
        table,
        "l",
        left_companies,
    )
    right_where, right_parameters = merge_selection(
        table,
        "r",
        right_companies,
    )
    selected = ",".join(
        [
            *(f"l.{quoted(column)}" for column in columns),
            *(f"r.{quoted(column)}" for column in columns),
        ]
    )
    attribution_pairs: list[
        tuple[tuple[object, ...], tuple[object, ...]]
    ] = []
    limit = (
        ""
        if table in {"contributions", "corporate_giving"}
        else "LIMIT 1"
    )
    with closing(
        connection.execute(
            f"""
            SELECT {selected}
            FROM {quoted(left_schema)}.{quoted(table)} AS l
            JOIN {quoted(right_schema)}.{quoted(table)} AS r
              ON {key_match}
            WHERE {left_where}
              AND {right_where}
              AND NOT ({row_match})
            {limit}
            """,
            left_parameters + right_parameters,
        )
    ) as cursor:
        for collision in cursor:
            column_count = len(columns)
            left_values = tuple(collision[:column_count])
            right_values = tuple(collision[column_count:])
            key = {
                column: left_values[columns.index(column)]
                for column in pk_columns
            }
            differing = [
                column
                for index, column in enumerate(columns)
                if left_values[index] != right_values[index]
            ]
            if table == "corporate_giving" and set(differing).issubset(
                CORPORATE_VERSION_FIELDS
            ):
                def blank(value: object) -> bool:
                    return value is None or (
                        isinstance(value, str) and not value.strip()
                    )

                if all(
                    blank(left_values[columns.index(column)])
                    or blank(right_values[columns.index(column)])
                    for column in differing
                ):
                    # Collection deliberately backfills these fields. A blank
                    # may be represented as NULL or an empty string and may be
                    # enriched, but two conflicting nonblank values remain a
                    # hard parity failure.
                    continue
            if (
                table == "corporate_giving"
                and "company" in differing
                and set(differing).issubset(
                    CORPORATE_REJECTED_ATTRIBUTION_FIELDS
                )
                and "company_match_status" in columns
            ):
                status_index = columns.index("company_match_status")
                statuses = {
                    str(left_values[status_index]).strip().lower(),
                    str(right_values[status_index]).strip().lower(),
                }
                if statuses in ({"rejected"}, {"rejected", "review"}, {"rejected", "accepted"}):
                    # A rejected search hit cannot compete with the same
                    # filing's retained attribution. Keep its conflict receipt.
                    attribution_pairs.append((left_values, right_values))
                    continue
            if (
                table == "contributions"
                and "company" in differing
                and set(differing).issubset(allowed_provenance_columns)
            ):
                attribution_pairs.append((left_values, right_values))
                continue
            raise RuntimeError(
                f"Non-identical primary-key collision in {table}: "
                f"{left_label} vs {right_label}; key={key!r}; "
                f"differing_columns={differing!r}"
            )
    return attribution_pairs


def build_attribution_resolutions(
    connection: sqlite3.Connection,
    collision_rows: dict[str, set[tuple[object, ...]]],
    company_order: Sequence[str],
    table: str,
) -> list[AttributionResolution]:
    columns = table_columns(connection, "main", table)
    company_index = columns.index("company")
    phase_index = columns.index("phase") if "phase" in columns else None
    scraped_index = columns.index("scraped_at") if "scraped_at" in columns else None
    load_date_index = columns.index("load_date") if "load_date" in columns else None
    ranks = {company: index for index, company in enumerate(company_order)}

    def row_order(row: tuple[object, ...]) -> tuple[object, ...]:
        company = str(row[company_index])
        return (
            int(table == "corporate_giving" and "company_match_status" in columns
                and str(row[columns.index("company_match_status")]).strip().lower() == "rejected"),
            ranks.get(company, len(ranks)),
            company.upper(),
            company,
            tuple("" if value is None else str(value) for value in row),
        )

    resolutions: list[AttributionResolution] = []
    for sub_id in sorted(collision_rows, key=lambda value: (value.upper(), value)):
        rows = sorted(collision_rows[sub_id], key=row_order)
        winner = rows[0]
        stored_company = str(winner[company_index])
        conflicts: dict[
            tuple[str, str, str, str], AttributionConflict
        ] = {}
        for loser in rows[1:]:
            incoming_company = str(loser[company_index])
            if incoming_company == stored_company:
                continue
            incoming_phase = (
                str(loser[phase_index]) if phase_index is not None else ""
            )
            if table == "corporate_giving":
                incoming_phase = "corporate_giving_rejected"
            timestamp_value = None
            if scraped_index is not None:
                timestamp_value = loser[scraped_index]
            if not timestamp_value and load_date_index is not None:
                timestamp_value = loser[load_date_index]
            created_at = (
                str(timestamp_value)
                if timestamp_value
                else DETERMINISTIC_CONFLICT_TIMESTAMP
            )
            conflict = AttributionConflict(
                sub_id=sub_id,
                stored_company=stored_company,
                incoming_company=incoming_company,
                incoming_phase=incoming_phase,
                created_at=created_at,
            )
            conflicts[
                (
                    conflict.sub_id,
                    conflict.stored_company,
                    conflict.incoming_company,
                    conflict.incoming_phase,
                )
            ] = conflict
        resolutions.append(
            AttributionResolution(
                table=table,
                sub_id=sub_id,
                winner=winner,
                conflicts=tuple(
                    conflicts[key]
                    for key in sorted(
                        conflicts,
                        key=lambda item: (
                            item[0].upper(),
                            item[0],
                            ranks.get(item[2], len(ranks)),
                            item[2].upper(),
                            item[2],
                            item[3],
                        ),
                    )
                ),
            )
        )
    return resolutions


def audit_exact_pk_collisions(
    connection: sqlite3.Connection,
    specifications: Sequence[tuple[Path, tuple[str, ...]]],
    company_order: Sequence[str],
) -> list[AttributionResolution]:
    sources: list[tuple[str, str, Sequence[str] | None]] = [
        ("main", "target", None)
    ]
    sources.extend(
        (
            f"shard_{index}",
            str(shard),
            companies,
        )
        for index, (shard, companies) in enumerate(specifications)
    )
    allowed_provenance_columns = contribution_provenance_columns(connection)
    collision_rows: dict[
        str,
        dict[str, set[tuple[object, ...]]],
    ] = {
        "contributions": defaultdict(set),
        "corporate_giving": defaultdict(set),
    }
    for table in EXACT_PARITY_TABLES:
        for right_index in range(1, len(sources)):
            right_schema, right_label, right_companies = sources[right_index]
            for left_index in range(right_index):
                left_schema, left_label, left_companies = sources[left_index]
                attribution_pairs = audit_exact_pk_pair(
                    connection,
                    table,
                    left_schema,
                    left_label,
                    left_companies,
                    right_schema,
                    right_label,
                    right_companies,
                    allowed_provenance_columns,
                )
                if not attribution_pairs:
                    continue
                columns = table_columns(connection, "main", table)
                sub_id_index = columns.index("sub_id")
                for left_values, right_values in attribution_pairs:
                    sub_id = str(left_values[sub_id_index])
                    collision_rows[table][sub_id].add(left_values)
                    collision_rows[table][sub_id].add(right_values)
    resolutions: list[AttributionResolution] = []
    for table in ("contributions", "corporate_giving"):
        resolutions.extend(
            build_attribution_resolutions(
                connection,
                collision_rows[table],
                company_order,
                table,
            )
        )
    return resolutions


def insert_or_ignore(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
    where_sql: str = "1",
    parameters: Sequence[object] = (),
    columns: Sequence[str] | None = None,
) -> int:
    selected_columns = list(columns) if columns is not None else table_columns(
        connection,
        "main",
        table,
    )
    column_sql = ",".join(quoted(column) for column in selected_columns)
    connection.execute(
        f"""
        INSERT OR IGNORE INTO main.{quoted(table)} ({column_sql})
        SELECT {column_sql}
        FROM {quoted(schema)}.{quoted(table)} AS s
        WHERE {where_sql}
        """,
        tuple(parameters),
    )
    return int(connection.execute("SELECT changes()").fetchone()[0])


def merge_company_fact_table(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
    companies: Sequence[str],
) -> tuple[int, int, int]:
    company_column = COMPANY_TABLES[table]
    where = (
        f"s.{quoted(company_column)} IN ({placeholders(companies)})"
    )
    pk_columns = primary_key_columns(connection, "main", table)
    if not pk_columns:
        raise RuntimeError(f"{table} has no primary key")
    pk_match = null_safe_equal("m", "s", pk_columns)
    ignored_existing = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM {quoted(schema)}.{quoted(table)} AS s
            WHERE {where}
              AND EXISTS (
                SELECT 1 FROM main.{quoted(table)} AS m
                WHERE {pk_match}
              )
            """,
            tuple(companies),
        ).fetchone()[0]
    )
    inserted = insert_or_ignore(
        connection,
        schema,
        table,
        where,
        companies,
    )
    enriched = 0
    if table == "corporate_giving":
        columns = set(table_columns(connection, "main", table))
        for column in sorted(CORPORATE_VERSION_FIELDS.intersection(columns)):
            incoming = (
                f"(SELECT s.{quoted(column)} "
                f"FROM {quoted(schema)}.{quoted(table)} AS s "
                f"WHERE s.sub_id=main.{quoted(table)}.sub_id)"
            )
            if column in {"report_year", "file_num"}:
                replacement = (
                    f"COALESCE(main.{quoted(table)}.{quoted(column)}, "
                    f"{incoming})"
                )
                blank_target = (
                    f"main.{quoted(table)}.{quoted(column)} IS NULL"
                )
            else:
                replacement = (
                    f"CASE WHEN main.{quoted(table)}.{quoted(column)} IS NULL "
                    f"OR TRIM(main.{quoted(table)}.{quoted(column)})='' "
                    f"THEN {incoming} "
                    f"ELSE main.{quoted(table)}.{quoted(column)} END"
                )
                blank_target = (
                    f"(main.{quoted(table)}.{quoted(column)} IS NULL OR "
                    f"TRIM(main.{quoted(table)}.{quoted(column)})='')"
                )
            connection.execute(
                f"""
                UPDATE main.{quoted(table)}
                SET {quoted(column)}={replacement}
                WHERE company IN ({placeholders(companies)})
                  AND {blank_target}
                  AND EXISTS (
                    SELECT 1
                    FROM {quoted(schema)}.{quoted(table)} AS s
                    WHERE s.sub_id=main.{quoted(table)}.sub_id
                      AND s.{quoted(column)} IS NOT NULL
                      AND TRIM(CAST(s.{quoted(column)} AS TEXT))<>''
                  )
                """,
                tuple(companies),
            )
            enriched += int(connection.execute("SELECT changes()").fetchone()[0])
    return inserted, ignored_existing, enriched


def merge_scrape_progress(
    connection: sqlite3.Connection,
    schema: str,
    companies: Sequence[str],
) -> int:
    columns = table_columns(connection, "main", "scrape_progress")
    column_sql = ",".join(quoted(column) for column in columns)
    connection.execute(
        f"""
        INSERT INTO main.scrape_progress ({column_sql})
        SELECT {column_sql}
        FROM {quoted(schema)}.scrape_progress AS s
        WHERE s.company IN ({placeholders(companies)}) AND 1
        ON CONFLICT(company, cycle, variant_set) DO UPDATE SET
          last_page=CASE
            WHEN excluded.last_page > scrape_progress.last_page
              OR (
                excluded.last_page = scrape_progress.last_page
                AND excluded.updated_at > scrape_progress.updated_at
              )
            THEN excluded.last_page ELSE scrape_progress.last_page END,
          records_total=CASE
            WHEN excluded.last_page > scrape_progress.last_page
              OR (
                excluded.last_page = scrape_progress.last_page
                AND excluded.updated_at > scrape_progress.updated_at
              )
            THEN excluded.records_total ELSE scrape_progress.records_total END,
          updated_at=MAX(scrape_progress.updated_at, excluded.updated_at),
          zero_verified=MAX(
            scrape_progress.zero_verified,
            excluded.zero_verified
          )
        WHERE excluded.last_page > scrape_progress.last_page
           OR excluded.zero_verified > scrape_progress.zero_verified
           OR (
             excluded.last_page = scrape_progress.last_page
             AND excluded.updated_at > scrape_progress.updated_at
           )
        """,
        tuple(companies),
    )
    return int(connection.execute("SELECT changes()").fetchone()[0])


def merge_api_progress(
    connection: sqlite3.Connection,
    schema: str,
    shard_index: int,
    companies: Sequence[str],
) -> tuple[int, int]:
    unit_stage = f"_mentat_merge_units_{shard_index}"
    connection.execute(
        f"CREATE TEMP TABLE {quoted(unit_stage)} (unit_id TEXT PRIMARY KEY)"
    )
    try:
        connection.execute(
            f"""
            INSERT INTO temp.{quoted(unit_stage)} (unit_id)
            SELECT s.unit_id
            FROM {quoted(schema)}.api_work_progress AS s
            LEFT JOIN main.api_work_progress AS m
              ON m.unit_id=s.unit_id
            WHERE s.company IN ({placeholders(companies)})
              AND (
                m.unit_id IS NULL
                OR s.stored_complete > m.stored_complete
                OR (
                  s.stored_complete = m.stored_complete
                  AND s.fetched_complete > m.fetched_complete
                )
                OR (
                  s.stored_complete = m.stored_complete
                  AND s.fetched_complete = m.fetched_complete
                  AND s.last_page > m.last_page
                )
                OR (
                  s.stored_complete = m.stored_complete
                  AND s.fetched_complete = m.fetched_complete
                  AND s.last_page = m.last_page
                  AND s.rows_seen > m.rows_seen
                )
                OR (
                  s.stored_complete = m.stored_complete
                  AND s.fetched_complete = m.fetched_complete
                  AND s.last_page = m.last_page
                  AND s.rows_seen = m.rows_seen
                  AND s.updated_at > m.updated_at
                )
              )
            """,
            tuple(companies),
        )
        selected_units = int(
            connection.execute(
                f"SELECT COUNT(*) FROM temp.{quoted(unit_stage)}"
            ).fetchone()[0]
        )
        if not selected_units:
            return 0, 0

        connection.execute(
            f"""
            DELETE FROM main.api_page_checkpoints
            WHERE unit_id IN (
              SELECT unit_id FROM temp.{quoted(unit_stage)}
            )
            """
        )
        checkpoint_columns = table_columns(
            connection,
            "main",
            "api_page_checkpoints",
        )
        checkpoint_sql = ",".join(
            quoted(column) for column in checkpoint_columns
        )
        connection.execute(
            f"""
            INSERT INTO main.api_page_checkpoints ({checkpoint_sql})
            SELECT {checkpoint_sql}
            FROM {quoted(schema)}.api_page_checkpoints AS p
            WHERE p.unit_id IN (
              SELECT unit_id FROM temp.{quoted(unit_stage)}
            )
            """
        )
        checkpoint_rows = int(
            connection.execute("SELECT changes()").fetchone()[0]
        )

        progress_columns = table_columns(
            connection,
            "main",
            "api_work_progress",
        )
        progress_sql = ",".join(
            quoted(column) for column in progress_columns
        )
        update_columns = [
            column for column in progress_columns if column != "unit_id"
        ]
        update_sql = ",".join(
            f"{quoted(column)}=excluded.{quoted(column)}"
            for column in update_columns
        )
        connection.execute(
            f"""
            INSERT INTO main.api_work_progress ({progress_sql})
            SELECT {progress_sql}
            FROM {quoted(schema)}.api_work_progress AS s
            WHERE s.unit_id IN (
              SELECT unit_id FROM temp.{quoted(unit_stage)}
            ) AND 1
            ON CONFLICT(unit_id) DO UPDATE SET {update_sql}
            """
        )
        progress_rows = int(
            connection.execute("SELECT changes()").fetchone()[0]
        )
        return progress_rows, checkpoint_rows
    finally:
        connection.execute(f"DROP TABLE temp.{quoted(unit_stage)}")


def merge_review_queue(
    connection: sqlite3.Connection,
    schema: str,
    companies: Sequence[str],
) -> int:
    columns = [
        column
        for column in table_columns(
            connection,
            "main",
            "review_queue_identity",
        )
        if column != "review_id"
    ]
    return insert_or_ignore(
        connection,
        schema,
        "review_queue_identity",
        f"s.company IN ({placeholders(companies)})",
        companies,
        columns,
    )


def merge_canary_log(
    connection: sqlite3.Connection,
    schema: str,
    companies: Sequence[str],
) -> int:
    columns = [
        column
        for column in table_columns(connection, "main", "canary_log")
        if column != "canary_id"
    ]
    column_sql = ",".join(quoted(column) for column in columns)
    natural_match = null_safe_equal("m", "s", columns)
    connection.execute(
        f"""
        INSERT INTO main.canary_log ({column_sql})
        SELECT {column_sql}
        FROM {quoted(schema)}.canary_log AS s
        WHERE s.company IN ({placeholders(companies)})
          AND NOT EXISTS (
            SELECT 1 FROM main.canary_log AS m
            WHERE {natural_match}
          )
        ORDER BY s.company, s.cycle, s.variant_set, s.page,
                 s.check_name, s.checked_at, s.canary_id
        """,
        tuple(companies),
    )
    return int(connection.execute("SELECT changes()").fetchone()[0])


def merge_attribution_conflicts(
    connection: sqlite3.Connection,
    schema: str,
    companies: Sequence[str],
) -> int:
    company_sql = placeholders(companies)
    return insert_or_ignore(
        connection,
        schema,
        "attribution_conflicts",
        (
            f"s.stored_company IN ({company_sql}) "
            f"OR s.incoming_company IN ({company_sql})"
        ),
        tuple(companies) + tuple(companies),
    )


def merge_global_table(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
) -> int:
    # These tables have declared PK/UNIQUE constraints. INSERT OR IGNORE
    # therefore preserves the target's existing row for a cloned baseline and
    # adds only genuinely new shard records.
    return insert_or_ignore(connection, schema, table)


def merge_evidence_table(connection: sqlite3.Connection, schema: str, table: str) -> int:
    columns = table_columns(connection, "main", table)
    keys = primary_key_columns(connection, "main", table)
    if not keys:
        raise RuntimeError(f"Evidence table {table} needs a stable primary key")
    stable = [c for c in columns if c not in EVIDENCE_TIMESTAMPS]
    collision = connection.execute(f'''
        SELECT 1 FROM main.{quoted(table)} m JOIN {quoted(schema)}.{quoted(table)} s
          ON {null_safe_equal('m','s',keys)}
        WHERE NOT ({null_safe_equal('m','s',stable)}) LIMIT 1''').fetchone()
    if collision:
        raise RuntimeError(f"{table} evidence primary-key content conflict; merge refused")
    if table == "fec_response_archive":
        for fingerprint, payload in connection.execute(
                f'SELECT archive_sha256,payload_json FROM {quoted(schema)}.{quoted(table)}'):
            if hashlib.sha256(payload.encode('utf-8')).hexdigest() != fingerprint:
                raise RuntimeError("Collector response archive hash mismatch; merge refused")
    if table == "fec_response_observations":
        for fingerprint, payload in connection.execute(
                f'SELECT query_sha256,query_json FROM {quoted(schema)}.{quoted(table)}'):
            if hashlib.sha256(payload.encode('utf-8')).hexdigest() != fingerprint:
                raise RuntimeError("Collector response query hash mismatch; merge refused")
    updates = []
    for column in columns:
        if column in EVIDENCE_TIMESTAMPS:
            function = 'MAX' if column == 'last_observed_at' else 'MIN'
            updates.append(f'{quoted(column)}={function}(COALESCE({quoted(column)},excluded.{quoted(column)}),excluded.{quoted(column)})')
    action = 'DO UPDATE SET '+','.join(updates) if updates else 'DO NOTHING'
    column_sql = ','.join(quoted(c) for c in columns)
    connection.execute(f'''INSERT INTO main.{quoted(table)} ({column_sql})
        SELECT {column_sql} FROM {quoted(schema)}.{quoted(table)} WHERE 1
        ON CONFLICT({','.join(quoted(c) for c in keys)}) {action}''')
    return int(connection.execute('SELECT changes()').fetchone()[0])


def apply_attribution_resolutions(
    connection: sqlite3.Connection,
    resolutions: Sequence[AttributionResolution],
) -> int:
    if not resolutions:
        return 0
    inserted_conflicts = 0
    for resolution in resolutions:
        columns = table_columns(connection, "main", resolution.table)
        column_sql = ",".join(quoted(column) for column in columns)
        value_sql = placeholders(columns)
        current = connection.execute(
            f"""
            SELECT {column_sql}
            FROM main.{quoted(resolution.table)}
            WHERE sub_id=?
            """,
            (resolution.sub_id,),
        ).fetchone()
        if current is not None and tuple(current) != resolution.winner:
            connection.execute(
                f"DELETE FROM main.{quoted(resolution.table)} WHERE sub_id=?",
                (resolution.sub_id,),
            )
        connection.execute(
            f"""
            INSERT OR IGNORE INTO main.{quoted(resolution.table)} ({column_sql})
            VALUES ({value_sql})
            """,
            resolution.winner,
        )
        for conflict in resolution.conflicts:
            connection.execute(
                """
                INSERT OR IGNORE INTO main.attribution_conflicts (
                  sub_id, stored_company, incoming_company,
                  incoming_phase, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conflict.sub_id,
                    conflict.stored_company,
                    conflict.incoming_company,
                    conflict.incoming_phase,
                    conflict.created_at,
                ),
            )
            inserted_conflicts += int(
                connection.execute("SELECT changes()").fetchone()[0]
            )
    return inserted_conflicts


def merge_shard(
    connection: sqlite3.Connection,
    shard_index: int,
    schema: str,
    shard_path: Path,
    companies: Sequence[str],
) -> dict[str, int]:
    stats: dict[str, int] = {}
    for table in EVIDENCE_TABLES:
        if table_columns(connection, schema, table):
            stats[f"{table}_changed"] = merge_evidence_table(connection, schema, table)
        else:
            stats[f"{table}_legacy_missing"] = 1
    for table in (
        "contributions",
        "corporate_giving",
        "bulk_candidates",
    ):
        inserted, existing, enriched = merge_company_fact_table(
            connection,
            schema,
            table,
            companies,
        )
        stats[f"{table}_inserted"] = inserted
        stats[f"{table}_pk_existing"] = existing
        if table == "corporate_giving":
            stats["corporate_giving_version_fields_enriched"] = enriched

    stats["scrape_progress_changed"] = merge_scrape_progress(
        connection,
        schema,
        companies,
    )
    (
        stats["api_work_progress_changed"],
        stats["api_page_checkpoints_replaced"],
    ) = merge_api_progress(
        connection,
        schema,
        shard_index,
        companies,
    )
    stats["review_queue_identity_inserted"] = merge_review_queue(
        connection,
        schema,
        companies,
    )
    stats["attribution_conflicts_inserted"] = merge_attribution_conflicts(
        connection,
        schema,
        companies,
    )
    stats["canary_log_inserted"] = merge_canary_log(
        connection,
        schema,
        companies,
    )
    for table in GLOBAL_APPEND_TABLES:
        stats[f"{table}_inserted"] = merge_global_table(
            connection,
            schema,
            table,
        )
    return stats


def parse_specifications(
    values: Sequence[str],
    target: Path,
) -> list[tuple[Path, tuple[str, ...]]]:
    grouped: dict[Path, set[str]] = defaultdict(set)
    company_owner: dict[str, Path] = {}
    for value in values:
        try:
            path_value, companies_value = value.split("::", 1)
        except ValueError as error:
            raise SystemExit(f"Invalid shard specification: {value}") from error
        shard = Path(path_value).resolve()
        companies = {
            company.strip()
            for company in companies_value.split("|")
            if company.strip()
        }
        if not shard.is_file() or not companies:
            raise SystemExit(f"Invalid shard path or company list: {value}")
        if shard == target:
            raise SystemExit("A shard path cannot be the target database")
        for company in companies:
            previous = company_owner.get(company)
            if previous is not None and previous != shard:
                raise SystemExit(
                    f"Company {company!r} is assigned to both "
                    f"{previous} and {shard}"
                )
            company_owner[company] = shard
        grouped[shard].update(companies)

    specifications = [
        (path, tuple(sorted(companies, key=lambda value: (value.upper(), value))))
        for path, companies in grouped.items()
    ]
    specifications.sort(key=lambda item: str(item[0]).lower())
    return specifications


def parse_company_order(
    encoded: str | None,
    specifications: Sequence[tuple[Path, tuple[str, ...]]],
) -> tuple[str, ...]:
    assigned = {
        company
        for _, companies in specifications
        for company in companies
    }
    if encoded is None:
        return tuple(sorted(assigned, key=lambda value: (value.upper(), value)))
    try:
        parsed = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise SystemExit(f"Invalid --company-order-json: {error}") from error
    if (
        not isinstance(parsed, list)
        or not all(isinstance(company, str) and company.strip() for company in parsed)
    ):
        raise SystemExit("--company-order-json must be a JSON array of company names")
    order = tuple(company.strip() for company in parsed)
    if len(set(order)) != len(order):
        raise SystemExit("--company-order-json contains duplicate company names")
    if set(order) != assigned:
        missing = sorted(assigned.difference(order))
        extra = sorted(set(order).difference(assigned))
        raise SystemExit(
            "--company-order-json must name every assigned company exactly once; "
            f"missing={missing!r}; extra={extra!r}"
        )
    return order


def target_connection(target: Path) -> sqlite3.Connection:
    if not target.is_file():
        raise SystemExit(f"Target database does not exist: {target}")
    connection = sqlite3.connect(
        target.as_uri() + "?mode=rw",
        timeout=60,
        isolation_level=None,
        uri=True,
    )
    connection.execute("PRAGMA busy_timeout=60000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quiescent_file_sha256(path: Path) -> str:
    wal = Path(f"{path}-wal")
    if wal.is_file() and wal.stat().st_size:
        raise RuntimeError(f"Merge shard has uncheckpointed WAL data: {wal}")
    return file_sha256(path)


def merge_lock_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.merge-lock.sqlite")


@contextmanager
def merge_writer_lock(target: Path) -> Iterator[None]:
    """Serialize merge children even if their Python supervisors die."""
    connection = sqlite3.connect(
        merge_lock_path(target), timeout=0, isolation_level=None
    )
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower():
                raise RuntimeError(
                    f"Another merge child owns the target database: {target}"
                ) from error
            raise
        try:
            yield
        finally:
            connection.rollback()
    finally:
        connection.close()


def assert_supervisor_owner(owner_path: Path | None, token: str | None) -> None:
    if owner_path is None:
        return
    try:
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("Merge supervisor ownership receipt is unavailable") from error
    if owner.get("token") != token or owner.get("active") is not True:
        raise RuntimeError("Merge child belongs to a superseded supervisor")


def assert_expected_target_state(
    connection: sqlite3.Connection,
    target: Path,
    expected_sha256: str | None,
) -> None:
    if expected_sha256 is None:
        return
    if not connection.in_transaction:
        raise RuntimeError("Target-state verification requires a write lease.")
    wal = Path(f"{target}-wal")
    if wal.is_file() and wal.stat().st_size > 0:
        raise RuntimeError(
            "Target database gained a nonempty WAL before merge; refusing "
            f"moving-target merge: {wal}"
        )
    observed = file_sha256(target)
    if observed.lower() != expected_sha256.lower():
        raise RuntimeError(
            "Target database changed before the merge write lease was "
            "acquired; refusing moving-target merge."
        )


def snapshot_target_for_batched_merge(
    target: Path,
    expected_target_sha256: str | None,
) -> Path:
    # Prepare consecutive batches in a private copy. Final publication later
    # uses one target SQLite transaction, so the real target stays atomic.
    connection = target_connection(target)
    try:
        quick_check(connection, "main")
        connection.execute("BEGIN IMMEDIATE")
        try:
            assert_expected_target_state(
                connection,
                target,
                expected_target_sha256,
            )
            descriptor, temp_name = tempfile.mkstemp(
                dir=str(target.parent),
                prefix=f".{target.name}.merge-staging.",
                suffix=".tmp",
            )
            os.close(descriptor)
            staged = Path(temp_name)
            try:
                shutil.copy2(target, staged)
            except BaseException:
                staged.unlink(missing_ok=True)
                raise
        finally:
            connection.execute("ROLLBACK")
    finally:
        connection.close()
    return staged


def run_batched_merge(
    target: Path,
    specifications: Sequence[tuple[Path, tuple[str, ...]]],
    company_order: Sequence[str],
    expected_target_sha256: str | None,
    merge_batch_limit: int,
    assert_owner: Callable[[], None] = lambda: None,
    intent: dict[str, object] | None = None,
    intent_path: Path | None = None,
) -> int:
    staged = snapshot_target_for_batched_merge(target, expected_target_sha256)
    all_stats: list[tuple[Path, tuple[str, ...], dict[str, int]]] = []
    attribution_resolutions: list[AttributionResolution] = []
    attribution_conflicts_recorded = 0
    try:
        connection = target_connection(staged)
        attached: list[str] = []
        try:
            batches = [
                specifications[start : start + merge_batch_limit]
                for start in range(0, len(specifications), merge_batch_limit)
            ]
            for batch in batches:
                quick_check(connection, "main")
                for index, (shard, companies) in enumerate(batch):
                    schema = f"shard_{index}"
                    shard_uri = shard.as_uri() + "?mode=ro"
                    connection.execute(
                        f"ATTACH DATABASE ? AS {quoted(schema)}",
                        (shard_uri,),
                    )
                    attached.append(schema)
                    quick_check(connection, schema)
                    ensure_schema_compatible(connection, schema, shard)

                connection.execute("BEGIN IMMEDIATE")
                try:
                    # Auditing this batch against "main" also catches
                    # cross-batch collisions: every prior batch's rows are
                    # already committed into "main" by the time this batch's
                    # shards are compared against it.
                    batch_resolutions = audit_exact_pk_collisions(
                        connection,
                        batch,
                        company_order,
                    )
                    batch_conflicts_recorded = apply_attribution_resolutions(
                        connection,
                        batch_resolutions,
                    )
                    for index, (shard, companies) in enumerate(batch):
                        schema = f"shard_{index}"
                        stats = merge_shard(
                            connection,
                            index,
                            schema,
                            shard,
                            companies,
                        )
                        all_stats.append((shard, companies, stats))
                    quick_check(connection, "main")
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise

                # Validate committed bytes, not just the transaction view.
                quick_check(connection, "main")
                attribution_resolutions.extend(batch_resolutions)
                attribution_conflicts_recorded += batch_conflicts_recorded

                for schema in reversed(attached):
                    connection.execute(f"DETACH DATABASE {quoted(schema)}")
                attached = []
        finally:
            for schema in reversed(attached):
                try:
                    connection.execute(f"DETACH DATABASE {quoted(schema)}")
                except sqlite3.Error:
                    pass
            connection.close()
    except BaseException:
        staged.unlink(missing_ok=True)
        raise

    # Publish the fully merged staged state through SQLite while holding the
    # real target write lease. File replacement would allow an external writer
    # to commit after a SHA check but before os.replace.
    try:
        assert_owner()
        with closing(sqlite3.connect(staged.as_uri() + "?mode=ro", uri=True)) as staged_read:
            staged_hash = logical_database_sha256(staged_read)
        target_guard = target_connection(target)
        try:
            target_guard.execute(
                "ATTACH DATABASE ? AS shard_0",
                (staged.as_uri() + "?mode=ro",),
            )
            ensure_schema_compatible(target_guard, "shard_0", staged)
            target_guard.execute("BEGIN IMMEDIATE")
            try:
                assert_expected_target_state(
                    target_guard, target, expected_target_sha256
                )
                publish_spec = [(staged, tuple(company_order))]
                resolutions = audit_exact_pk_collisions(
                    target_guard, publish_spec, company_order
                )
                apply_attribution_resolutions(target_guard, resolutions)
                merge_shard(
                    target_guard, 0, "shard_0", staged, tuple(company_order)
                )
                if logical_database_sha256(target_guard) != staged_hash:
                    raise RuntimeError(
                        "Transactional staged publication differs from its "
                        "prepared database; refusing partial merge."
                    )
                write_commit_marker(target_guard, intent, intent_path)
                quick_check(target_guard, "main")
                assert_owner()
                target_guard.execute("COMMIT")
            except BaseException:
                target_guard.execute("ROLLBACK")
                raise
        finally:
            target_guard.close()
    except BaseException:
        raise
    finally:
        staged.unlink(missing_ok=True)

    print("quick_check=ok")
    for resolution in attribution_resolutions:
        for conflict in resolution.conflicts:
            print(
                "attribution_conflict "
                f"key={{'sub_id': {conflict.sub_id!r}}} "
                f"stored_company={conflict.stored_company!r} "
                f"incoming_company={conflict.incoming_company!r} "
                f"incoming_phase={conflict.incoming_phase!r}"
            )
    print(
        "attribution_conflicts_recorded="
        f"{attribution_conflicts_recorded}"
    )
    for shard, companies, stats in all_stats:
        print(
            "shard="
            + str(shard)
            + " companies="
            + "|".join(companies)
        )
        for name in sorted(stats):
            print(f"  {name}={stats[name]}")
    return 0


def main(arguments: Sequence[str]) -> int:
    if len(arguments) < 2:
        raise SystemExit(__doc__)
    target = Path(arguments[0]).resolve()
    remaining = list(arguments[1:])
    encoded_company_order: str | None = None
    expected_target_sha256: str | None = None
    supervisor_owner_path: Path | None = None
    supervisor_token: str | None = None
    intent_path: Path | None = None
    merge_batch_limit = 10
    while remaining and remaining[0].startswith("--"):
        option = remaining.pop(0)
        if not remaining:
            raise SystemExit(__doc__)
        value = remaining.pop(0)
        if option == "--company-order-json":
            encoded_company_order = value
        elif option == "--expected-target-sha256":
            expected_target_sha256 = value
        elif option == "--supervisor-owner-path":
            supervisor_owner_path = Path(value).resolve()
        elif option == "--supervisor-token":
            supervisor_token = value
        elif option == "--merge-intent-json":
            intent_path = Path(value).resolve(strict=True)
        elif option == "--merge-batch-limit":
            try:
                merge_batch_limit = int(value)
            except ValueError:
                raise SystemExit("--merge-batch-limit must be an integer")
            if merge_batch_limit < 2:
                raise SystemExit("--merge-batch-limit must be >= 2")
        else:
            raise SystemExit(f"Unknown option: {option}")
    if (supervisor_owner_path is None) != (supervisor_token is None):
        raise SystemExit("Merge supervisor path and token must be supplied together")
    specifications = parse_specifications(remaining, target)
    company_order = parse_company_order(encoded_company_order, specifications)
    intent = (
        load_merge_intent(intent_path, target, specifications, expected_target_sha256)
        if intent_path else None
    )
    assert_owner = lambda: assert_supervisor_owner(
        supervisor_owner_path, supervisor_token
    )
    with merge_writer_lock(target):
        assert_owner()
        return merge_locked(
            target, specifications, company_order, expected_target_sha256,
            merge_batch_limit, assert_owner, intent, intent_path,
        )


def merge_locked(
    target: Path,
    specifications: Sequence[tuple[Path, tuple[str, ...]]],
    company_order: Sequence[str],
    expected_target_sha256: str | None,
    merge_batch_limit: int,
    assert_owner: Callable[[], None],
    intent: dict[str, object] | None,
    intent_path: Path | None,
) -> int:
    if len(specifications) > merge_batch_limit:
        return run_batched_merge(
            target, specifications, company_order, expected_target_sha256,
            merge_batch_limit, assert_owner, intent, intent_path,
        )
    connection = target_connection(target)
    attached: list[str] = []
    all_stats: list[tuple[Path, tuple[str, ...], dict[str, int]]] = []
    attribution_resolutions: list[AttributionResolution] = []
    attribution_conflicts_recorded = 0
    try:
        quick_check(connection, "main")
        for index, (shard, companies) in enumerate(specifications):
            schema = f"shard_{index}"
            shard_uri = shard.as_uri() + "?mode=ro"
            connection.execute(
                f"ATTACH DATABASE ? AS {quoted(schema)}",
                (shard_uri,),
            )
            attached.append(schema)
            quick_check(connection, schema)
            ensure_schema_compatible(connection, schema, shard)

        connection.execute("BEGIN IMMEDIATE")
        try:
            assert_expected_target_state(
                connection,
                target,
                expected_target_sha256,
            )
            # Audit while holding the target write lease. Auditing before
            # BEGIN IMMEDIATE leaves a TOCTOU gap in which another writer can
            # introduce a conflicting PK that INSERT OR IGNORE would hide.
            attribution_resolutions = audit_exact_pk_collisions(
                connection,
                specifications,
                company_order,
            )
            attribution_conflicts_recorded = apply_attribution_resolutions(
                connection,
                attribution_resolutions,
            )
            for index, (shard, companies) in enumerate(specifications):
                schema = f"shard_{index}"
                stats = merge_shard(
                    connection,
                    index,
                    schema,
                    shard,
                    companies,
                )
                all_stats.append((shard, companies, stats))
            quick_check(connection, "main")
            write_commit_marker(connection, intent, intent_path)
            assert_owner()
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

        # Validate committed bytes, not just the transaction view.
        quick_check(connection, "main")
        print("quick_check=ok")
        for resolution in attribution_resolutions:
            for conflict in resolution.conflicts:
                print(
                    "attribution_conflict "
                    f"key={{'sub_id': {conflict.sub_id!r}}} "
                    f"stored_company={conflict.stored_company!r} "
                    f"incoming_company={conflict.incoming_company!r} "
                    f"incoming_phase={conflict.incoming_phase!r}"
                )
        print(
            "attribution_conflicts_recorded="
            f"{attribution_conflicts_recorded}"
        )
        for shard, companies, stats in all_stats:
            print(
                "shard="
                + str(shard)
                + " companies="
                + "|".join(companies)
            )
            for name in sorted(stats):
                print(f"  {name}={stats[name]}")
    finally:
        for schema in reversed(attached):
            try:
                connection.execute(f"DETACH DATABASE {quoted(schema)}")
            except sqlite3.Error:
                pass
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

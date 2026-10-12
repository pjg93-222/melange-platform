"""Recover contributor_street_1 / contributor_street_2 from stored FEC evidence.

The FEC API returns both street fields on every receipt, and Mentat already
keeps the raw response bodies verbatim, so street can be restored for rows that
were collected before the address block reached the schema WITHOUT re-fetching
anything.  This tool never opens a collector, a console or the network: it only
reads two evidence tables and writes two columns.

Evidence, in precedence order
-----------------------------
1. ``fec_response_archive`` rows whose ``payload_kind`` is
   ``fec_api_response_body``, ordered by ``(created_at, archive_sha256)``.  The
   records live under ``payload_json["results"]``.
2. ``api_page_checkpoints``, ordered by ``(unit_id, page)``.  The records live
   under ``payload_json["rows"]``.

Within a payload, records are read in the order they were stored.  A record is
evidence only when it names a ``sub_id`` and carries a non-empty
``contributor_street_1``; the FIRST such record seen for a ``sub_id`` wins.  A
``sub_id`` whose evidence holds two or more different ``(street_1, street_2)``
pairs is counted in ``evidence.conflicts`` - the first-seen pair is still the
one written, so the result does not depend on how many times a page was
re-archived.

Both street columns are taken from the SAME winning record, so a JSON ``null``
``contributor_street_2`` lands as SQL ``NULL`` and an empty string lands as
``''``.  That keeps a stored address internally consistent instead of splicing
two halves of different filings together.

Idempotence and safety
----------------------
* Only rows whose ``contributor_street_1`` is ``NULL`` or ``''`` are updated, so
  a second run is a no-op and a street that is already stored is never
  overwritten - whatever ``contributor_street_2`` holds.
* The default is a DRY RUN.  ``--apply`` is required to write, and the database
  is opened read-only until then.
* Every write happens inside one transaction: the run lands whole or not at all.
* A target table that lacks the street columns is REFUSED, not skipped, because
  a silent skip would be indistinguishable from "no evidence found".  Run
  ``init_schema()`` (mentat/R/schema.R) against the database first.
* Missing evidence tables are treated as zero evidence, not as an error: a
  database collected by the browser lane has no API archive to read.

Usage
-----
    python tools/backfill_contributor_street.py <db> [--apply]
                                                     [--table NAME ...]
                                                     [--receipt path]

The receipt dictionary is printed to stdout as JSON and, with ``--receipt``,
also written to that path.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Iterator, Sequence

DEFAULT_TABLES: tuple[str, ...] = ("corporate_giving", "contributions")
STREET_COLUMNS: tuple[str, ...] = (
    "contributor_street_1",
    "contributor_street_2",
)
ARCHIVE_TABLE = "fec_response_archive"
ARCHIVE_PAYLOAD_KIND = "fec_api_response_body"
CHECKPOINT_TABLE = "api_page_checkpoints"
# Archive bodies key their records under "results"; page checkpoints use
# "rows". Both are checked, in this order, so one reader serves both tables.
RECORD_KEYS: tuple[str, ...] = ("results", "rows")


def quote_identifier(name: str) -> str:
    """Quote a SQL identifier. PRAGMA and DDL cannot take a bound parameter."""
    return '"' + str(name).replace('"', '""') + '"'


def text_value(value: Any) -> str:
    """The trimmed text of a JSON or SQLite scalar; '' for NULL/None."""
    if value is None:
        return ""
    return str(value).strip()


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in connection.execute(
            f"PRAGMA table_info({quote_identifier(table)})"
        )
    ]


def iter_payload_records(payload: Any) -> Iterator[dict[str, Any]]:
    """Yield the record dictionaries inside one decoded payload."""
    if not isinstance(payload, dict):
        return
    for key in RECORD_KEYS:
        records = payload.get(key)
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict):
                yield record


def decode_payload(raw: Any) -> Any:
    """Decode a payload_json cell; an undecodable cell carries no evidence."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def parse_evidence(
    connection: sqlite3.Connection,
) -> tuple[dict[str, tuple[Any, Any]], dict[str, int]]:
    """Build the sub_id -> (street_1, street_2) map and its evidence counts."""
    winners: dict[str, tuple[Any, Any]] = {}
    observed: dict[str, set[tuple[Any, Any]]] = {}
    statistics = {
        "archive_records": 0,
        "checkpoint_records": 0,
        "distinct_sub_ids": 0,
        "conflicts": 0,
    }

    def absorb(record: dict[str, Any]) -> None:
        sub_id = text_value(record.get("sub_id"))
        if not sub_id:
            return
        street_1 = record.get("contributor_street_1")
        if not text_value(street_1):
            return
        # Both halves come from this record, so a null street_2 stays null.
        pair = (street_1, record.get("contributor_street_2"))
        observed.setdefault(sub_id, set()).add(pair)
        winners.setdefault(sub_id, pair)

    if table_exists(connection, ARCHIVE_TABLE):
        rows = connection.execute(
            f"SELECT payload_json FROM {quote_identifier(ARCHIVE_TABLE)}"
            " WHERE payload_kind = ?"
            " ORDER BY created_at, archive_sha256",
            (ARCHIVE_PAYLOAD_KIND,),
        )
        for (payload_json,) in rows:
            for record in iter_payload_records(decode_payload(payload_json)):
                statistics["archive_records"] += 1
                absorb(record)

    if table_exists(connection, CHECKPOINT_TABLE):
        rows = connection.execute(
            f"SELECT payload_json FROM {quote_identifier(CHECKPOINT_TABLE)}"
            " ORDER BY unit_id, page"
        )
        for (payload_json,) in rows:
            for record in iter_payload_records(decode_payload(payload_json)):
                statistics["checkpoint_records"] += 1
                absorb(record)

    statistics["distinct_sub_ids"] = len(winners)
    statistics["conflicts"] = sum(
        1 for pairs in observed.values() if len(pairs) > 1
    )
    return winners, statistics


def ensure_street_columns(
    connection: sqlite3.Connection,
    tables: Sequence[str],
) -> None:
    """Refuse the whole run when any target table cannot hold a street."""
    problems: list[str] = []
    for table in tables:
        if not table_exists(connection, table):
            problems.append(f"{table}: table is absent")
            continue
        present = set(table_columns(connection, table))
        missing = [name for name in STREET_COLUMNS if name not in present]
        if missing:
            problems.append(f"{table}: missing {', '.join(missing)}")
    if problems:
        raise RuntimeError(
            "backfill_contributor_street refuses to run -- "
            + "; ".join(problems)
            + ". Run init_schema() from mentat/R/schema.R against this database"
            " first; it adds the street columns without touching the rows."
        )


def candidate_sub_ids(
    connection: sqlite3.Connection,
    table: str,
) -> list[Any]:
    """Sub-IDs of rows with no street_1. street_2 alone never qualifies."""
    quoted = quote_identifier(table)
    return [
        row[0]
        for row in connection.execute(
            f"SELECT {quote_identifier('sub_id')} FROM {quoted}"
            f" WHERE {quote_identifier(STREET_COLUMNS[0])} IS NULL"
            f" OR {quote_identifier(STREET_COLUMNS[0])} = ''"
        )
    ]


def run(
    database: Path | str,
    apply: bool = False,
    tables: Sequence[str] = DEFAULT_TABLES,
    receipt: Path | str | None = None,
) -> dict[str, Any]:
    """Report, and optionally apply, the street backfill for `tables`."""
    path = Path(database)
    targets = tuple(dict.fromkeys(str(table) for table in tables))
    uri = path.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    report: dict[str, Any] = {
        "database": str(path),
        "applied": bool(apply),
        "evidence": {},
        "tables": {},
    }
    with closing(
        sqlite3.connect(uri, uri=True, timeout=60, isolation_level=None)
    ) as connection:
        ensure_street_columns(connection, targets)
        winners, statistics = parse_evidence(connection)
        report["evidence"] = statistics

        pending: list[tuple[str, list[tuple[Any, Any, Any]]]] = []
        for table in targets:
            quoted = quote_identifier(table)
            rows = connection.execute(
                f"SELECT COUNT(*) FROM {quoted}"
            ).fetchone()[0]
            candidates = candidate_sub_ids(connection, table)
            updates: list[tuple[Any, Any, Any]] = []
            for sub_id in candidates:
                pair = winners.get(text_value(sub_id))
                if pair is None:
                    continue
                updates.append((pair[0], pair[1], sub_id))
            pending.append((table, updates))
            report["tables"][table] = {
                "rows": int(rows),
                "candidates": len(candidates),
                "matched": len(updates),
                "updated": 0,
                "unmatched": len(candidates) - len(updates),
            }

        if apply:
            # One transaction for every table: the backfill lands whole or the
            # database is left exactly as it was found.
            connection.execute("BEGIN IMMEDIATE")
            try:
                for table, updates in pending:
                    quoted = quote_identifier(table)
                    street_1 = quote_identifier(STREET_COLUMNS[0])
                    street_2 = quote_identifier(STREET_COLUMNS[1])
                    before = connection.total_changes
                    connection.executemany(
                        f"UPDATE {quoted} SET {street_1} = ?, {street_2} = ?"
                        f" WHERE {quote_identifier('sub_id')} = ?"
                        f" AND ({street_1} IS NULL OR {street_1} = '')",
                        updates,
                    )
                    report["tables"][table]["updated"] = (
                        connection.total_changes - before
                    )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    if receipt is not None:
        receipt_path = Path(receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    return report


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Restore contributor_street_1/contributor_street_2 from stored FEC"
            " response evidence. Dry run unless --apply is given."
        )
    )
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the recovered street values (default: report only)",
    )
    parser.add_argument(
        "--table",
        action="append",
        dest="tables",
        choices=DEFAULT_TABLES,
        help="limit the run to this table; repeatable",
    )
    parser.add_argument("--receipt", type=Path)
    return parser.parse_args(arguments)


def main(arguments: list[str]) -> int:
    parsed = parse_arguments(arguments)
    tables = tuple(parsed.tables) if parsed.tables else DEFAULT_TABLES
    report = run(
        parsed.database,
        apply=parsed.apply,
        tables=tables,
        receipt=parsed.receipt,
    )
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Assert every raw-contribution producer against the column-order contract.

The contract lives in audits/mentat_failings_20260915/contracts/
raw_contribution_columns.json and is the single source of truth for where the
address block (employer, occupation, street_1, street_2, city, state, zip)
appears on each surface.  Producers keep their own constants; these tests import
the contract and compare.

S2 has no shared test utility module, so assert_block_adjacent lives here and
sibling suites import it from this module.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPO_ROOT
    / "audits"
    / "mentat_failings_20260915"
    / "contracts"
    / "raw_contribution_columns.json"
)
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import build_tidy_workbook  # noqa: E402
import export_raw_contribution_warehouse as warehouse  # noqa: E402
from prepare_workbook_payload import RAW_FIELDS  # noqa: E402


STREET_COLUMNS = ("contributor_street_1", "contributor_street_2")
RSCRIPT = shutil.which("Rscript") or r"C:\Program Files\R\R-4.6.0\bin\Rscript.exe"
INIT_SCHEMA_R = """
arguments <- commandArgs(trailingOnly = TRUE)
project_root <- arguments[[1L]]
database <- arguments[[2L]]
for (file in c("paths.R", "schema.R")) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}
connection <- DBI::dbConnect(RSQLite::SQLite(), database)
init_schema(connection)
DBI::dbDisconnect(connection)
"""


def assert_block_adjacent(
    names: Iterable[str],
    block: Sequence[str],
    require: Sequence[str] | None = None,
) -> None:
    """Raise AssertionError unless the block sits contiguous and in order.

    Members of ``block`` that appear in ``names`` must occupy consecutive
    positions in ``block``'s own order.  Every member of ``require`` must be
    present; ``require`` defaults to the whole block.  Pass ``require=()`` when
    the surface is allowed to omit members (an un-migrated snapshot, or a
    partial test fixture) and only adjacency is under test.
    """
    sequence = list(names)
    required = tuple(block) if require is None else tuple(require)
    missing = [name for name in required if name not in sequence]
    if missing:
        raise AssertionError(
            f"missing mandated column(s) {missing} in {sequence}"
        )
    positions = [sequence.index(name) for name in block if name in sequence]
    if not positions:
        return
    expected = list(range(positions[0], positions[0] + len(positions)))
    if positions != expected:
        present = [name for name in block if name in sequence]
        raise AssertionError(
            "block is not contiguous and in contract order: expected "
            f"{present} at positions {expected}, found {positions} in "
            f"{sequence[positions[0]: positions[0] + len(positions) + 2]}"
        )


def run_init_schema(database: Path) -> None:
    """Create the real production schema by calling init_schema() from R."""
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "init_schema_fixture.R"
        script.write_text(INIT_SCHEMA_R, encoding="utf-8", newline="")
        completed = subprocess.run(
            [RSCRIPT, "--vanilla", str(script), str(PROJECT_ROOT), str(database)],
            capture_output=True,
            text=True,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            "init_schema fixture failed:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )


def rebuild_without_street(connection: sqlite3.Connection, table: str) -> None:
    """Recreate ``table`` with the street columns removed, order preserved."""
    info = connection.execute(f"PRAGMA table_info({table})").fetchall()
    kept = [row for row in info if str(row[1]) not in STREET_COLUMNS]
    definitions = ['"{0}" {1}'.format(row[1], row[2] or "TEXT") for row in kept]
    primary = [
        str(row[1])
        for row in sorted(info, key=lambda item: int(item[5]))
        if int(row[5]) > 0 and str(row[1]) not in STREET_COLUMNS
    ]
    if primary:
        keys = ", ".join('"{0}"'.format(name) for name in primary)
        definitions.append(f"PRIMARY KEY ({keys})")
    connection.execute(f"DROP TABLE {table}")
    connection.execute("CREATE TABLE {0} ({1})".format(table, ", ".join(definitions)))


def append_street_columns(connection: sqlite3.Connection, table: str) -> None:
    """Add the street columns at the END, the shape an ALTER migration leaves."""
    existing = {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for column in STREET_COLUMNS:
        if column not in existing:
            connection.execute(
                'ALTER TABLE {0} ADD COLUMN "{1}" TEXT'.format(table, column)
            )


def insert_example_row(
    connection: sqlite3.Connection,
    table: str,
    record: dict[str, Any],
) -> None:
    """Insert the contract's example row, skipping columns the table lacks."""
    existing = [
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    ]
    usable = [name for name in existing if name in record]
    columns = ", ".join('"{0}"'.format(name) for name in usable)
    markers = ", ".join("?" for _ in usable)
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({markers})",
        [record[name] for name in usable],
    )


def build_fixture_database(
    root: Path,
    *,
    strip_street: bool = False,
    append_street: bool = False,
) -> Path:
    record = CONTRACT["example_record_db"]
    database = root / "mentat.sqlite"
    run_init_schema(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        for table in warehouse.TABLE_SPECS:
            if strip_street:
                rebuild_without_street(connection, table)
            if append_street:
                append_street_columns(connection, table)
        connection.execute(
            "INSERT INTO companies (industry, company, parent, variant,"
            " variant_order, confidence, source_sheet, source_row)"
            " VALUES (?, ?, ?, ?, 1, 'high', 'contract_fixture', 1)",
            (
                record["industry"],
                record["company"],
                record["company"],
                record["matched_variant"],
            ),
        )
        for table in warehouse.TABLE_SPECS:
            insert_example_row(connection, table, record)
    return database


def export_partition(root: Path, database: Path) -> tuple[Path, dict[str, Any]]:
    """Export and return (partition directory, the export receipt).

    The receipt is returned because a schema gap is reported there - the
    exporter keeps `status` "succeeded" and adds a top-level
    `missing_contract_columns` key instead of failing.
    """
    record = CONTRACT["example_record_db"]
    output = root / "raw"
    result = warehouse.export_warehouse(
        database,
        output,
        record["industry"],
        (record["cycle"],),
    )
    if result.get("status") != "succeeded":
        raise RuntimeError(f"warehouse export failed: {result!r}")
    manifest = json.loads(
        Path(result["snapshot_manifest"]).read_text(encoding="utf-8")
    )
    return output / manifest["partitions"][0]["path"], result


def read_csv_header(partition: Path, table: str) -> list[str]:
    with gzip.open(
        partition / f"{table}.csv.gz",
        "rt",
        encoding="utf-8",
        newline="",
    ) as stream:
        return next(csv.reader(stream))


def read_partition_manifest(partition: Path) -> dict[str, Any]:
    return json.loads((partition / "manifest.json").read_text(encoding="utf-8"))


SURFACE_TABLES = {
    "corporate_giving": "raw_warehouse_corporate_giving",
    "contributions": "raw_warehouse_contributions",
}


class ContractSelfConsistencyTests(unittest.TestCase):
    def test_contract_self_consistency(self) -> None:
        surfaces = CONTRACT["surfaces"]
        block_db = CONTRACT["block_db"]
        self.assertTrue(
            set(CONTRACT["mandated_db"]).issubset(set(block_db)),
            "mandated_db must be a subset of block_db",
        )
        for name in ("raw_warehouse_corporate_giving", "raw_warehouse_contributions"):
            assert_block_adjacent(surfaces[name]["header"], block_db)
        assert_block_adjacent(
            surfaces["workbook_r_stage"]["columns"],
            surfaces["workbook_r_stage"]["block"],
        )
        assert_block_adjacent(
            surfaces["contributions_sheet"]["headers"],
            surfaces["contributions_sheet"]["header_block"],
        )
        assert_block_adjacent(
            surfaces["tidy_contributions"]["columns"],
            surfaces["tidy_contributions"]["block"],
        )
        self.assertEqual(
            len(surfaces["contributions_sheet"]["headers"]),
            len(surfaces["contributions_sheet"]["row_fields"]),
            "contributions_sheet headers and row_fields must stay cell aligned",
        )
        for surface in ("compact_contributions_sheet", "formula_facts_projection"):
            self.assertTrue(surfaces[surface]["must_not_contain"])

    def test_negative_control_fails_adjacency(self) -> None:
        with self.assertRaises(AssertionError):
            assert_block_adjacent(
                CONTRACT["negative_control"],
                CONTRACT["block_db"],
            )


@unittest.skipUnless(
    os.path.exists(RSCRIPT),
    f"Rscript not available at {RSCRIPT}",
)
class RawWarehouseContractTests(unittest.TestCase):
    def test_raw_warehouse_header_matches_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = build_fixture_database(root)
            partition, receipt = export_partition(root, database)
            manifest = read_partition_manifest(partition)
            self.assertNotIn(
                "missing_contract_columns",
                receipt,
                "a schema with the full block must not carry the gap key",
            )
            for table, surface in SURFACE_TABLES.items():
                expected = list(CONTRACT["surfaces"][surface]["header"])
                with self.subTest(table=table):
                    self.assertEqual(read_csv_header(partition, table), expected)
                    self.assertEqual(
                        list(manifest["tables"][table]["columns"]), expected
                    )

    def test_raw_warehouse_migrated_shape_same_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = build_fixture_database(
                root, strip_street=True, append_street=True
            )
            with closing(sqlite3.connect(database)) as connection:
                stored = [
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(corporate_giving)"
                    ).fetchall()
                ]
            self.assertEqual(
                stored[-2:],
                list(STREET_COLUMNS),
                "fixture must place street at the END, as ALTER TABLE does",
            )
            partition, receipt = export_partition(root, database)
            self.assertNotIn(
                "missing_contract_columns",
                receipt,
                "an ALTER-migrated database is complete; no gap key",
            )
            for table, surface in SURFACE_TABLES.items():
                with self.subTest(table=table):
                    self.assertEqual(
                        read_csv_header(partition, table),
                        list(CONTRACT["surfaces"][surface]["header"]),
                    )

    def test_raw_warehouse_unmigrated_db_omits_and_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = build_fixture_database(root, strip_street=True)
            partition, receipt = export_partition(root, database)
            manifest = read_partition_manifest(partition)
            # The gap is reported at receipt level; `status` stays "succeeded"
            # so the orchestrator's exit-code contract is untouched.
            self.assertEqual(receipt["status"], "succeeded")
            self.assertEqual(
                receipt["missing_contract_columns"],
                {table: list(STREET_COLUMNS) for table in SURFACE_TABLES},
            )
            for table, surface in SURFACE_TABLES.items():
                expected = [
                    name
                    for name in CONTRACT["surfaces"][surface]["header"]
                    if name not in STREET_COLUMNS
                ]
                with self.subTest(table=table):
                    self.assertEqual(read_csv_header(partition, table), expected)
                    self.assertEqual(
                        manifest["tables"][table].get("missing_contract_columns"),
                        list(STREET_COLUMNS),
                    )


class TidyWorkbookSheetTests(unittest.TestCase):
    def test_tidy_workbook_sheet_carries_block(self) -> None:
        """The companion Data workbook Contributions sheet carries the block."""
        block = list(CONTRACT["surfaces"]["tidy_contributions"]["block"])
        keys = [entry[0] for entry in build_tidy_workbook.CONTRIBUTIONS]
        assert_block_adjacent(keys, block)
        labels = {entry[0]: entry[1] for entry in build_tidy_workbook.CONTRIBUTIONS}
        self.assertEqual(
            [labels[key] for key in block],
            [
                "Employer",
                "Occupation",
                "Street 1",
                "Street 2",
                "City",
                "State",
                "Contributor ZIP",
                "Contributor ZIP Raw",
            ],
            "sheet headers for the block must stay verbatim",
        )


class FormulaFactsProjectionTests(unittest.TestCase):
    def test_formula_facts_projection_excludes_block(self) -> None:
        barred = CONTRACT["surfaces"]["formula_facts_projection"]["must_not_contain"]
        for field in barred:
            with self.subTest(field=field):
                self.assertNotIn(field, RAW_FIELDS)


if __name__ == "__main__":
    unittest.main()

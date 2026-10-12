from __future__ import annotations

import csv
import gzip
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import export_raw_contribution_warehouse as warehouse  # noqa: E402

CONTRACT = json.loads(
    (
        PROJECT_ROOT.parent
        / "audits"
        / "mentat_failings_20260915"
        / "contracts"
        / "raw_contribution_columns.json"
    ).read_text(encoding="utf-8")
)
try:
    from tests.test_raw_contribution_columns import (  # noqa: E402
        assert_block_adjacent,
    )
except ImportError:  # invoked as a bare file rather than a package module
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_raw_contribution_columns import (  # noqa: E402
        assert_block_adjacent,
    )


SCHEMA = """
CREATE TABLE companies (
  industry TEXT NOT NULL,
  company TEXT NOT NULL,
  variant TEXT NOT NULL,
  PRIMARY KEY (industry, company, variant)
);
CREATE TABLE contributions (
  sub_id TEXT PRIMARY KEY,
  contributor_name TEXT,
  contributor_city TEXT,
  contributor_state TEXT,
  contributor_zip TEXT,
  contributor_employer TEXT,
  contributor_occupation TEXT,
  contribution_receipt_amount REAL,
  industry TEXT NOT NULL,
  company TEXT NOT NULL,
  two_year_transaction_period INTEGER NOT NULL,
  company_match_status TEXT,
  excluded_reason TEXT,
  contributor_street_1 TEXT,
  contributor_street_2 TEXT
);
CREATE TABLE corporate_giving (
  sub_id TEXT PRIMARY KEY,
  contributor_name TEXT,
  contributor_city TEXT,
  contributor_state TEXT,
  contributor_zip TEXT,
  contributor_employer TEXT,
  contributor_occupation TEXT,
  contribution_receipt_amount REAL,
  industry TEXT NOT NULL,
  company TEXT NOT NULL,
  cycle INTEGER NOT NULL,
  company_match_status TEXT,
  excluded_reason TEXT,
  contributor_street_1 TEXT,
  contributor_street_2 TEXT
);
"""


def create_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO companies VALUES ('Fintech', ?, ?)",
            [("Alpha", "Alpha"), ("Beta/Pay", "Beta Pay")],
        )
        connection.executemany(
            """
            INSERT INTO contributions (
                sub_id, contributor_name, contribution_receipt_amount,
                industry, company, two_year_transaction_period,
                company_match_status, excluded_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "c-1",
                    "Person, One",
                    12.5,
                    "Fintech",
                    "Alpha",
                    2022,
                    "accepted",
                    None,
                ),
                (
                    "c-2",
                    "=UNTRUSTED()",
                    -4.0,
                    "Fintech",
                    "Alpha",
                    2022,
                    "rejected",
                    "refund\nmemo",
                ),
            ],
        )
        connection.execute(
            """
            INSERT INTO corporate_giving (
                sub_id, contributor_name, contribution_receipt_amount,
                industry, company, cycle, company_match_status,
                excluded_reason
            )
            VALUES ('g-1', NULL, 50, 'Fintech', 'Beta/Pay', 2024,
                    'review', NULL)
            """
        )


class RawWarehouseTests(unittest.TestCase):
    def test_atomic_replace_retries_transient_windows_denial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            source.write_text("value", encoding="utf-8")
            real_replace = warehouse.os.replace
            attempts = 0

            def flaky_replace(
                observed_source: Path,
                observed_target: Path,
            ) -> None:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("transient lock")
                real_replace(observed_source, observed_target)

            with (
                mock.patch.object(
                    warehouse.os,
                    "replace",
                    side_effect=flaky_replace,
                ),
                mock.patch.object(warehouse.time, "sleep"),
            ):
                warehouse.replace_with_retry(source, target)
            self.assertEqual(attempts, 3)
            self.assertEqual(target.read_text(encoding="utf-8"), "value")

    def test_full_lossless_partition_export_and_deterministic_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mentat.db"
            output = root / "raw"
            create_database(database)

            first = warehouse.export_warehouse(
                database,
                output,
                "Fintech",
                (2022, 2024),
            )
            self.assertEqual(first["status"], "succeeded")
            self.assertEqual(first["partition_count"], 4)
            self.assertEqual(first["rows"]["contributions"], 2)
            self.assertEqual(first["rows"]["corporate_giving"], 1)
            self.assertEqual(first["new_partitions"], 4)
            self.assertEqual(first["reused_partitions"], 0)
            self.assertIn(
                "authoritative and lossless",
                (output / "README.txt").read_text(encoding="utf-8"),
            )
            with (output / "catalog.json").open(encoding="utf-8") as stream:
                catalog = json.load(stream)
            self.assertEqual(catalog["industry_count"], 1)
            self.assertEqual(catalog["industries"][0]["industry"], "Fintech")
            self.assertEqual(
                catalog["industries"][0]["rows"],
                {"contributions": 2, "corporate_giving": 1},
            )

            with Path(first["snapshot_manifest"]).open(
                encoding="utf-8"
            ) as stream:
                manifest = json.load(stream)
            self.assertEqual(manifest["expected_partition_count"], 4)
            alpha_partition = next(
                item
                for item in manifest["partitions"]
                if item["cycle"] == 2022 and item["company"] == "Alpha"
            )
            alpha_path = output / alpha_partition["path"]
            with gzip.open(
                alpha_path / "contributions.jsonl.gz",
                "rt",
                encoding="utf-8",
            ) as stream:
                rows = [json.loads(line) for line in stream]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["contribution_receipt_amount"], 12.5)
            self.assertIsNone(rows[0]["excluded_reason"])
            self.assertEqual(rows[1]["company_match_status"], "rejected")
            self.assertEqual(rows[1]["contributor_name"], "=UNTRUSTED()")

            empty_partition = next(
                item
                for item in manifest["partitions"]
                if item["cycle"] == 2024 and item["company"] == "Alpha"
            )
            empty_path = output / empty_partition["path"]
            with gzip.open(
                empty_path / "contributions.csv.gz",
                "rt",
                encoding="utf-8",
                newline="",
            ) as stream:
                csv_rows = list(csv.reader(stream))
            self.assertEqual(len(csv_rows), 1)
            self.assertEqual(csv_rows[0][0], "sub_id")
            # This fixture's contributions table now carries the full
            # address block in migrated shape (contributor_street_1/_2
            # appended at the end, the shape an ALTER TABLE migration
            # leaves), so require defaults to the whole block and the
            # placement rule is exercised on a migrated shape right here,
            # not only against the real schema in
            # tests/test_raw_contribution_columns.py.
            assert_block_adjacent(csv_rows[0], CONTRACT["block_db"])
            with closing(sqlite3.connect(database)) as fixture_connection:
                fixture_pragma_names = tuple(
                    str(row[1])
                    for row in fixture_connection.execute(
                        "PRAGMA table_info(contributions)"
                    ).fetchall()
                )
            expected_header, _missing = warehouse.contract_column_order(
                fixture_pragma_names
            )
            self.assertEqual(csv_rows[0], list(expected_header))

            second = warehouse.export_warehouse(
                database,
                output,
                "Fintech",
                (2022, 2024),
            )
            self.assertEqual(second["logical_sha256"], first["logical_sha256"])
            self.assertEqual(second["reused_partitions"], 4)
            self.assertEqual(second["new_partitions"], 0)
            self.assertTrue(second["fast_path"])

    def test_corrupt_existing_partition_is_quarantined_and_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mentat.db"
            output = root / "raw"
            create_database(database)
            first = warehouse.export_warehouse(
                database,
                output,
                "Fintech",
                (2022,),
            )
            with Path(first["snapshot_manifest"]).open(
                encoding="utf-8"
            ) as stream:
                manifest = json.load(stream)
            target = output / manifest["partitions"][0]["path"]
            (target / "contributions.jsonl.gz").write_bytes(b"corrupt")

            second = warehouse.export_warehouse(
                database,
                output,
                "Fintech",
                (2022,),
            )
            self.assertEqual(second["status"], "succeeded")
            self.assertFalse(second["fast_path"])
            self.assertEqual(len(second["quarantined_corrupt_paths"]), 1)
            self.assertTrue(
                Path(second["quarantined_corrupt_paths"][0]).is_dir()
            )
            with gzip.open(
                target / "contributions.jsonl.gz",
                "rt",
                encoding="utf-8",
            ) as stream:
                list(stream)

    def test_wrong_industry_tag_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mentat.db"
            create_database(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO contributions (
                        sub_id, contributor_name, contribution_receipt_amount,
                        industry, company, two_year_transaction_period,
                        company_match_status, excluded_reason
                    )
                    VALUES ('wrong', 'Wrong', 1, 'Crypto', 'Alpha',
                            2022, 'accepted', NULL)
                    """
                )
            with self.assertRaisesRegex(RuntimeError, "another industry"):
                warehouse.export_warehouse(
                    database,
                    root / "raw",
                    "Fintech",
                    (2022,),
                )

    def test_cycle_parser_uses_two_year_cycles(self) -> None:
        self.assertEqual(
            warehouse.parse_cycles("2021-2022|2024|2025-2026"),
            (2022, 2024, 2026),
        )


if __name__ == "__main__":
    unittest.main()

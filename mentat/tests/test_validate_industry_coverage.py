from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import validate_industry_coverage as gate  # noqa: E402


SCHEMA = """
CREATE TABLE companies (
  industry TEXT NOT NULL,
  company TEXT NOT NULL,
  variant TEXT NOT NULL,
  PRIMARY KEY (industry, company, variant)
);
CREATE TABLE api_work_progress (
  unit_id TEXT PRIMARY KEY,
  company TEXT NOT NULL,
  cycle INTEGER NOT NULL,
  kind TEXT NOT NULL,
  rows_seen INTEGER NOT NULL,
  fetched_complete INTEGER NOT NULL,
  stored_complete INTEGER NOT NULL
);
CREATE TABLE canary_log (
  canary_id INTEGER PRIMARY KEY,
  company TEXT NOT NULL,
  cycle INTEGER NOT NULL,
  passed INTEGER NOT NULL
);
CREATE TABLE contributions (
  sub_id TEXT PRIMARY KEY,
  industry TEXT NOT NULL,
  company TEXT NOT NULL,
  two_year_transaction_period INTEGER NOT NULL
);
CREATE TABLE corporate_giving (
  sub_id TEXT PRIMARY KEY,
  industry TEXT NOT NULL,
  company TEXT NOT NULL,
  cycle INTEGER NOT NULL
);
"""


def create_complete_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(SCHEMA)
        for company in ("Alpha", "Beta"):
            connection.execute(
                "INSERT INTO companies VALUES ('Fintech', ?, ?)",
                (company, company),
            )
            for cycle in (2022, 2024, 2026):
                for kind in ("employer", "corporate"):
                    connection.execute(
                        """
                        INSERT INTO api_work_progress
                        VALUES (?, ?, ?, ?, 0, 1, 1)
                        """,
                        (f"{kind}:{company}:{cycle}", company, cycle, kind),
                    )
                connection.execute(
                    "INSERT INTO canary_log(company,cycle,passed) VALUES (?,?,1)",
                    (company, cycle),
                )
        connection.execute('ALTER TABLE api_work_progress ADD COLUMN records_total INTEGER DEFAULT 0')
        connection.execute('ALTER TABLE api_work_progress ADD COLUMN query_contract TEXT')
        for uid, company, cycle, kind in connection.execute('SELECT unit_id,company,cycle,kind FROM api_work_progress').fetchall():
            field = 'contributor_employer' if kind == 'employer' else 'contributor_name'
            contract = {'version':2, 'company':company, 'request':{'id':uid, 'params':{field:[company], 'two_year_transaction_period':str(cycle)}}}
            connection.execute('UPDATE api_work_progress SET query_contract=? WHERE unit_id=?',(json.dumps(contract),uid))


def write_receipt(path: Path, database: Path, **updates: object) -> None:
    value: dict[str, object] = {
        "status": "succeeded",
        "source_database": str(database),
        "merged": True,
        "task_count": 6,
        "succeeded": 6,
        "failed": 0,
    }
    value.update(updates)
    path.write_text(json.dumps(value), encoding="utf-8")


class CoverageGateTests(unittest.TestCase):
    def test_window_fetch_checkpoints_require_reconciled_stored_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.db'
            create_complete_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                parent = 'employer:Alpha:2026'
                connection.execute('UPDATE api_work_progress SET rows_seen=10 WHERE unit_id=?', (parent,))
                for suffix, count in (('01', 4), ('02', 6)):
                    connection.execute('INSERT INTO api_work_progress (unit_id,company,cycle,kind,rows_seen,fetched_complete,stored_complete) VALUES (?, ?, ?, ?, ?, 1, 0)', (parent+'::window::'+suffix, 'Alpha', 2026, 'employer', count))
                grid, missing, incomplete = gate.api_progress_grid(connection, ('Alpha',), (2026,))
                self.assertEqual((missing, incomplete), (0, 0))
                self.assertEqual(grid[0]['rows_seen'], 10)
                self.assertEqual(grid[0]['total_units'], 2)
                for field, value in (('stored_complete', 0), ('fetched_complete', 0), ('rows_seen', 11)):
                    connection.execute(f'UPDATE api_work_progress SET {field}=? WHERE unit_id=?', (value, parent))
                    self.assertEqual(gate.api_progress_grid(connection, ('Alpha',), (2026,))[2], 1)
                    connection.execute('UPDATE api_work_progress SET stored_complete=1, fetched_complete=1, rows_seen=10 WHERE unit_id=?', (parent,))
                connection.execute('UPDATE api_work_progress SET fetched_complete=0 WHERE unit_id=?', (parent+'::window::01',))
                self.assertEqual(gate.api_progress_grid(connection, ('Alpha',), (2026,))[2], 1)
                connection.execute('DELETE FROM api_work_progress WHERE unit_id=?', (parent,))
                self.assertEqual(gate.api_progress_grid(connection, ('Alpha',), (2026,))[2], 1)

    def test_roster_validation_discovers_industries_and_exclusion_coverage(
        self,
    ) -> None:
        import openpyxl

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "Company Name.xlsx"
            book = openpyxl.Workbook()
            crypto = book.active
            crypto.title = "Crypto"
            crypto.append(["CryptoCo", "Crypto Co"])
            fintech = book.create_sheet("Fintech")
            fintech.append(["Alpha"])
            fintech.append(["Alpha Inc / Alpha LLC"])
            fintech.append(["NOT ALPHA SCHOOL"])
            fintech.append([])
            fintech.append(["Beta"])
            book.save(workbook)
            book.close()

            result = gate.workbook_roster(workbook)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["industries"], ["Crypto", "Fintech"])
        fintech_result = next(
            item
            for item in result["readiness"]
            if item["industry"] == "Fintech"
        )
        self.assertTrue(fintech_result["ready"])
        self.assertEqual(fintech_result["company_count"], 2)
        self.assertEqual(fintech_result["variant_count"], 4)
        self.assertEqual(fintech_result["exclusion_pattern_count"], 1)
        self.assertEqual(fintech_result["companies_with_exclusions"], 1)
        self.assertEqual(fintech_result["companies_without_exclusions"], 1)

    def test_complete_company_cycle_grid_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mentat.db"
            receipt = root / "fanout.json"
            create_complete_database(database)
            write_receipt(receipt, database)
            result = gate.validate(
                database,
                "Fintech",
                (2022, 2024, 2026),
                receipt,
            )
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["expected_task_count"], 6)
            self.assertEqual(result["missing_company_cycles"], 0)
            self.assertTrue(all(result["checks"].values()))

    def test_missing_cycle_fails_closed_even_with_success_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mentat.db"
            receipt = root / "fanout.json"
            create_complete_database(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """
                    DELETE FROM api_work_progress
                    WHERE company='Beta' AND cycle=2026
                    """
                )
            write_receipt(receipt, database)
            result = gate.validate(
                database,
                "Fintech",
                (2022, 2024, 2026),
                receipt,
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["missing_company_cycles"], 1)
            self.assertFalse(result["checks"]["company_cycle_grid"])

    def test_incomplete_unit_and_canary_failure_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mentat.db"
            receipt = root / "fanout.json"
            create_complete_database(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """
                    UPDATE api_work_progress
                    SET stored_complete=0
                    WHERE unit_id='corporate:Alpha:2024'
                    """
                )
                connection.execute(
                    """
                    UPDATE canary_log
                    SET passed=0
                    WHERE company='Alpha' AND cycle=2024
                    """
                )
            write_receipt(receipt, database)
            result = gate.validate(
                database,
                "Fintech",
                (2022, 2024, 2026),
                receipt,
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["incomplete_company_cycles"], 1)
            self.assertEqual(result["canary_failures"], 1)

    def test_receipt_counts_must_match_roster_grid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mentat.db"
            receipt = root / "fanout.json"
            create_complete_database(database)
            write_receipt(receipt, database, task_count=5, succeeded=5)
            result = gate.validate(
                database,
                "Fintech",
                (2022, 2024, 2026),
                receipt,
            )
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["checks"]["fanout_receipt"])

    def test_out_of_scope_fact_row_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mentat.db"
            receipt = root / "fanout.json"
            create_complete_database(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO contributions
                    VALUES ('wrong', 'Fintech', 'Other', 2026)
                    """
                )
            write_receipt(receipt, database)
            result = gate.validate(
                database,
                "Fintech",
                (2022, 2024, 2026),
                receipt,
            )
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["checks"]["contribution_scope"])


if __name__ == "__main__":
    unittest.main()

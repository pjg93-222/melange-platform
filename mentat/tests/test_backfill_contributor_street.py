"""Pin the contract for tools/backfill_contributor_street.py before it exists.

The FEC API returns contributor_street_1 / contributor_street_2 on every
receipt, and the raw evidence tables already hold those payloads verbatim:

  * fec_response_archive(archive_sha256, schema_version, payload_kind,
    payload_json, created_at) - payload_json for payload_kind
    'fec_api_response_body' is {"api_version": .., "pagination": .., "results":
    [<records>]}
  * api_page_checkpoints(unit_id, page, payload_json) - payload_json is
    {"rows": [<records>], ...}

so street can be recovered for already-collected rows without re-fetching.

Expected interface (Phase 2 implements it; this suite ERRORs on ImportError
until then):

  backfill_contributor_street.run(
      database: Path, apply: bool, tables=("corporate_giving", "contributions")
  ) -> {
      "tables": {name: {"rows": n, "candidates": n, "matched": n,
                        "updated": n, "unmatched": n}},
      "evidence": {"archive_records": n, "checkpoint_records": n,
                   "distinct_sub_ids": n, "conflicts": n},
      "applied": bool,
  }

  python tools/backfill_contributor_street.py <db> [--apply] [--table ...]
                                                   [--receipt path]

Definitions the counts must honour:
  * rows        - every row in the table.
  * candidates  - rows whose contributor_street_1 is NULL or ''. A row with a
                  street_1 is never a candidate, whatever street_2 holds.
  * matched     - candidates whose sub_id appears in the evidence with a
                  non-empty contributor_street_1.
  * updated     - rows actually written. Always 0 when apply is False.
  * unmatched   - candidates with no such evidence.
  * conflicts   - sub_ids seen with two or more DIFFERENT non-empty street
                  values. The FIRST-SEEN non-empty value wins, where first-seen
                  means fec_response_archive ordered by (created_at,
                  archive_sha256) before api_page_checkpoints ordered by
                  (unit_id, page), and payload order within a payload.
  * Only fec_response_archive rows with payload_kind 'fec_api_response_body'
    are evidence.
  * When a record wins, BOTH street columns are taken from that same record, so
    a JSON null street_2 lands as SQL NULL.
  * A target table without the street columns is refused, not skipped.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import backfill_contributor_street as backfill  # noqa: E402


EVIDENCE_SCHEMA = """
CREATE TABLE fec_response_archive (
  archive_sha256 TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL,
  payload_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE api_page_checkpoints (
  unit_id TEXT NOT NULL,
  page INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY (unit_id, page)
);
"""
CORPORATE_WITH_STREET = """
CREATE TABLE corporate_giving (
  sub_id TEXT PRIMARY KEY,
  contributor_street_1 TEXT,
  contributor_street_2 TEXT,
  contributor_city TEXT
);
"""
CONTRIBUTIONS_WITH_STREET = """
CREATE TABLE contributions (
  sub_id TEXT PRIMARY KEY,
  contributor_street_1 TEXT,
  contributor_street_2 TEXT
);
"""
CONTRIBUTIONS_WITHOUT_STREET = """
CREATE TABLE contributions (
  sub_id TEXT PRIMARY KEY,
  contributor_city TEXT
);
"""

ARCHIVE_ROWS = (
    (
        "sha-a",
        1,
        "fec_api_response_body",
        {
            "api_version": "1.0",
            "pagination": {"page": 1, "pages": 1},
            "results": [
                {
                    "sub_id": "c-empty-null",
                    "contributor_street_1": "123 MAIN ST",
                    "contributor_street_2": "APT 4",
                },
                {
                    "sub_id": "g-empty",
                    "contributor_street_1": "500 CORPORATE PKWY",
                    "contributor_street_2": None,
                },
                {
                    "sub_id": "g-conflict",
                    "contributor_street_1": "1 FIRST WINS RD",
                    "contributor_street_2": None,
                },
            ],
        },
        "2026-01-01T00:00:00Z",
    ),
    (
        "sha-b",
        1,
        "fec_api_response_body",
        {
            "results": [
                {
                    "sub_id": "g-conflict",
                    "contributor_street_1": "2 SECOND LOSES RD",
                    "contributor_street_2": "SUITE 9",
                }
            ]
        },
        "2026-01-02T00:00:00Z",
    ),
    # A different payload_kind is NOT evidence, even though it names a sub_id
    # the tool would otherwise match.
    (
        "sha-other",
        1,
        "fec_api_request_metadata",
        {
            "results": [
                {
                    "sub_id": "c-unmatched",
                    "contributor_street_1": "404 SHOULD NOT BE USED ST",
                    "contributor_street_2": None,
                }
            ]
        },
        "2026-01-03T00:00:00Z",
    ),
)
CHECKPOINT_ROWS = (
    (
        "unit-1",
        1,
        {
            "rows": [
                {
                    "sub_id": "c-empty-blank",
                    "contributor_street_1": "77 CHECKPOINT WAY",
                    "contributor_street_2": "",
                }
            ],
            "records_total": 1,
        },
    ),
)
CONTRIBUTION_ROWS = (
    ("c-empty-null", None, None),
    ("c-empty-blank", "", ""),
    ("c-populated", "999 EXISTING AVE", "STE 5"),
    ("c-unmatched", None, None),
)
CORPORATE_ROWS = (
    ("g-empty", None, None, "CAMBRIDGE"),
    ("g-conflict", None, None, "BOSTON"),
)


def build_database(path: Path, *, contributions_has_street: bool = True) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(EVIDENCE_SCHEMA)
        connection.executescript(CORPORATE_WITH_STREET)
        connection.executescript(
            CONTRIBUTIONS_WITH_STREET
            if contributions_has_street
            else CONTRIBUTIONS_WITHOUT_STREET
        )
        connection.executemany(
            "INSERT INTO fec_response_archive VALUES (?, ?, ?, ?, ?)",
            [
                (sha, version, kind, json.dumps(payload), created)
                for sha, version, kind, payload, created in ARCHIVE_ROWS
            ],
        )
        connection.executemany(
            "INSERT INTO api_page_checkpoints VALUES (?, ?, ?)",
            [
                (unit, page, json.dumps(payload))
                for unit, page, payload in CHECKPOINT_ROWS
            ],
        )
        connection.executemany(
            "INSERT INTO corporate_giving VALUES (?, ?, ?, ?)", CORPORATE_ROWS
        )
        if contributions_has_street:
            connection.executemany(
                "INSERT INTO contributions VALUES (?, ?, ?)", CONTRIBUTION_ROWS
            )
        else:
            connection.executemany(
                "INSERT INTO contributions VALUES (?, ?)",
                [(row[0], "CAMBRIDGE") for row in CONTRIBUTION_ROWS],
            )


def street_values(database: Path, table: str) -> dict[str, tuple[Any, Any]]:
    with closing(sqlite3.connect(database)) as connection:
        return {
            str(row[0]): (row[1], row[2])
            for row in connection.execute(
                f"SELECT sub_id, contributor_street_1, contributor_street_2"
                f" FROM {table}"
            )
        }


EXPECTED_EVIDENCE = {
    "archive_records": 4,
    "checkpoint_records": 1,
    "distinct_sub_ids": 4,
    "conflicts": 1,
}
EXPECTED_CONTRIBUTIONS_DRY = {
    "rows": 4,
    "candidates": 3,
    "matched": 2,
    "updated": 0,
    "unmatched": 1,
}
EXPECTED_CORPORATE_DRY = {
    "rows": 2,
    "candidates": 2,
    "matched": 2,
    "updated": 0,
    "unmatched": 0,
}


class BackfillContributorStreetTests(unittest.TestCase):
    def test_dry_run_reports_matches_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mentat.sqlite"
            build_database(database)
            before = {
                table: street_values(database, table)
                for table in ("contributions", "corporate_giving")
            }
            result = backfill.run(database, apply=False)
            self.assertFalse(result["applied"])
            self.assertEqual(result["evidence"], EXPECTED_EVIDENCE)
            self.assertEqual(
                result["tables"]["contributions"], EXPECTED_CONTRIBUTIONS_DRY
            )
            self.assertEqual(
                result["tables"]["corporate_giving"], EXPECTED_CORPORATE_DRY
            )
            for table, rows in before.items():
                self.assertEqual(street_values(database, table), rows)

    def test_apply_fills_only_empty_street_and_leaves_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mentat.sqlite"
            build_database(database)
            result = backfill.run(database, apply=True)
            self.assertTrue(result["applied"])
            self.assertEqual(result["tables"]["contributions"]["updated"], 2)
            self.assertEqual(result["tables"]["corporate_giving"]["updated"], 2)
            contributions = street_values(database, "contributions")
            self.assertEqual(
                contributions["c-empty-null"], ("123 MAIN ST", "APT 4")
            )
            self.assertEqual(
                contributions["c-empty-blank"], ("77 CHECKPOINT WAY", "")
            )
            corporate = street_values(database, "corporate_giving")
            self.assertEqual(corporate["g-empty"], ("500 CORPORATE PKWY", None))

    def test_already_populated_street_is_never_touched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mentat.sqlite"
            build_database(database)
            backfill.run(database, apply=True)
            contributions = street_values(database, "contributions")
            self.assertEqual(
                contributions["c-populated"], ("999 EXISTING AVE", "STE 5")
            )

    def test_missing_street_columns_refuses_and_names_the_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mentat.sqlite"
            build_database(database, contributions_has_street=False)
            with self.assertRaises(RuntimeError) as caught:
                backfill.run(database, apply=False)
            message = str(caught.exception)
            self.assertIn("contributions", message)
            self.assertIn("contributor_street_1", message)
            self.assertIn("init_schema", message)

    def test_conflicting_values_are_counted_and_first_seen_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mentat.sqlite"
            build_database(database)
            result = backfill.run(database, apply=True)
            self.assertEqual(result["evidence"]["conflicts"], 1)
            corporate = street_values(database, "corporate_giving")
            self.assertEqual(corporate["g-conflict"], ("1 FIRST WINS RD", None))

    def test_sub_ids_absent_from_evidence_are_counted_unmatched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mentat.sqlite"
            build_database(database)
            result = backfill.run(database, apply=True)
            self.assertEqual(result["tables"]["contributions"]["unmatched"], 1)
            contributions = street_values(database, "contributions")
            self.assertEqual(contributions["c-unmatched"], (None, None))

    def test_table_selection_limits_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mentat.sqlite"
            build_database(database)
            result = backfill.run(
                database, apply=True, tables=("corporate_giving",)
            )
            self.assertEqual(list(result["tables"]), ["corporate_giving"])
            self.assertEqual(
                street_values(database, "contributions")["c-empty-null"],
                (None, None),
            )

    def test_cli_defaults_to_a_dry_run_and_writes_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mentat.sqlite"
            receipt = root / "receipt.json"
            build_database(database)
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "tools" / "backfill_contributor_street.py"),
                    str(database),
                    "--receipt",
                    str(receipt),
                ],
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertFalse(payload["applied"])
            self.assertEqual(payload["evidence"], EXPECTED_EVIDENCE)
            self.assertEqual(
                street_values(database, "contributions")["c-empty-null"],
                (None, None),
            )


if __name__ == "__main__":
    unittest.main()

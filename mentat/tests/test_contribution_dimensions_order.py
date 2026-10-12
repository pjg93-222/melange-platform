"""S4: assert the reordered chart_studio DIMENSIONS block against the
raw_contribution_columns contract, in-process, with a real _insert() write and
a negative control proving the by-name read assertion has teeth.

The contract lives in audits/mentat_failings_20260915/contracts/
raw_contribution_columns.json (README.md documents the loader convention this
module follows). Its chart_studio_dimensions surface was "deferred to S4"; S4
implements it here and flips that surface (and contributor_detail_sheet, the
sibling Task 3 covers) off the deferred status in the contract file itself.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

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

sys.path.insert(0, str(PROJECT_ROOT / "app"))
import contribution_data as c  # noqa: E402

try:
    from tests.test_raw_contribution_columns import (  # noqa: E402
        assert_block_adjacent,
    )
except ImportError:  # invoked as a bare file rather than a package module
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_raw_contribution_columns import (  # noqa: E402
        assert_block_adjacent,
    )


# The contiguous contributor block, in the order the HANDOFF and the contract
# both mandate: employer, occupation, street_1, street_2, city, state, zip5.
REQUIRED_BLOCK = (
    "employer",
    "occupation",
    "street_1",
    "street_2",
    "city",
    "state",
    "zip5",
)

SID = "SENTINEL-SOURCE-ID"

RAW = {
    "sub_id": SID,
    "industry": "SENTINEL-INDUSTRY",
    "contribution_receipt_date": "2025-06-01",
    "company": "SENTINEL-COMPANY",
    "contributor_employer": "SENTINEL-EMPLOYER",
    "contributor_occupation": "SENTINEL-OCCUPATION",
    "contributor_street_1": "SENTINEL-STREET-1",
    "contributor_street_2": "SENTINEL-STREET-2",
    "contributor_city": "SENTINEL-CITY",
    "contributor_state": "SENTINEL-STATE",
    "contributor_zip": "02134",
    "contributor_name": "SENTINEL CONTRIBUTOR NAME",
    "committee_name": "SENTINEL-RECIPIENT",
    "committee_id": "SENTINEL-COMMITTEE-ID",
    "contribution_receipt_amount": 123.45,
    "pdf_url": "http://example.invalid/sentinel.pdf",
    "source_table": "contributions",
    "baseline_party": "SENTINEL-BASELINE-PARTY",
    "baseline_category": "SENTINEL-BASELINE-CATEGORY",
}

# A receipt is supplied so every DESTINATIONS dimension also gets a distinct,
# real (not fallback) sentinel; its financial/date/contributor fields are kept
# consistent with RAW so _record()'s cross-check does not raise.
RECEIPT = {
    "amount": 123.45,
    "committee_id": "SENTINEL-COMMITTEE-ID",
    "date": "2025-06-01",
    "contributor": "SENTINEL CONTRIBUTOR NAME",
    "include_in_analysis": "Yes",
    "person_key": "SENTINEL-PERSON-KEY",
    "party": "SENTINEL-PARTY",
    "recipient_category": "SENTINEL-RECIPIENT-CATEGORY",
    "recipient_state": "SENTINEL-RECIPIENT-STATE",
    "represented_state": "SENTINEL-REPRESENTED-STATE",
    "recipient_location_status": "SENTINEL-RECIPIENT-LOCATION-STATUS",
    "represented_location_status": "SENTINEL-REPRESENTED-LOCATION-STATUS",
    "recipient_location_evidence": "SENTINEL-RECIPIENT-LOCATION-EVIDENCE",
    "represented_location_evidence": "SENTINEL-REPRESENTED-LOCATION-EVIDENCE",
}

DECISION = {
    "source_id": SID,
    "counted_cents": 11101,
    "supported_unique_cents": 22202,
    "pac_activity_cents": 33303,
    "status": "SENTINEL-DECISION-STATUS",
    "reasons": ["sentinel-reason"],
    "evidence_ids": ["sentinel-evidence-1"],
    "bucket": "SENTINEL-FUNDING-SOURCE",
    "receipt_role": "SENTINEL-RECEIPT-ROLE",
}


def expected_dimension_values():
    """The real _record() output, restricted to DIMENSIONS columns.

    Using the real function to compute expectations (rather than hand-typing
    a second copy of the derivation) keeps this test honest about what
    _record() actually does with RAW/DECISION/RECEIPT; what is under test
    here is whether _insert()'s column-name/value wiring survives the
    DIMENSIONS reorder, not whether _record()'s own logic is correct.
    """
    row = c._record(RAW, DECISION, RECEIPT)
    values = {k: row[k] for k in c.DIMENSIONS}
    assert len(set(values.values())) == len(values), (
        "fixture bug: sentinels collided, so a misassignment would go "
        f"undetected: {values}"
    )
    return values


class DimensionsOrderTests(unittest.TestCase):
    def test_dimensions_contains_the_contiguous_contributor_block(self):
        assert_block_adjacent(list(c.DIMENSIONS), REQUIRED_BLOCK)

    def test_dimensions_block_matches_contract_required_key_order(self):
        """The contract is the single source of truth (README.md); DIMENSIONS
        must reproduce its chart_studio_dimensions.required_key_order exactly,
        contiguous, wherever that block currently sits."""
        contract_block = tuple(
            CONTRACT["surfaces"]["chart_studio_dimensions"]["required_key_order"]
        )
        self.assertEqual(contract_block, REQUIRED_BLOCK)
        names = list(c.DIMENSIONS)
        present = [name for name in names if name in contract_block]
        self.assertEqual(
            present,
            list(contract_block),
            "DIMENSIONS' contiguous block must match the contract's order exactly",
        )


class InsertColumnAlignmentTests(unittest.TestCase):
    def test_insert_writes_each_dimension_under_its_own_column_name(self):
        expected = expected_dimension_values()
        with closing(sqlite3.connect(":memory:")) as db:
            c._schema(db)
            c._insert(db, {SID: RAW}, [DECISION], {SID: RECEIPT})
            db.commit()
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM facts WHERE source_id=?", (SID,)
            ).fetchone()
            self.assertIsNotNone(row, "the real _insert() must have written a row")
            for name, value in expected.items():
                with self.subTest(dimension=name):
                    self.assertEqual(
                        row[name],
                        value,
                        f"column {name!r} did not receive its own sentinel; "
                        "a DIMENSIONS reorder likely misaligned column names "
                        "and values",
                    )

    def test_negative_control_positional_insert_into_shuffled_schema_mismatches(self):
        """Prove the by-name assertion above has teeth: build a second table
        whose CREATE TABLE column order is a shuffle of DIMENSIONS, insert the
        same values positionally in the ORIGINAL DIMENSIONS order (the bug the
        real _insert() avoids by naming columns explicitly), and confirm the
        by-name read now disagrees with the expected sentinels."""
        expected = expected_dimension_values()
        original_order = list(c.DIMENSIONS)
        shuffled_order = list(reversed(original_order))
        self.assertNotEqual(
            shuffled_order,
            original_order,
            "fixture bug: reversing DIMENSIONS produced the same order",
        )
        with closing(sqlite3.connect(":memory:")) as db:
            columns_sql = ",".join(f'"{name}" TEXT' for name in shuffled_order)
            db.execute(f"CREATE TABLE shuffled_facts ({columns_sql})")
            values = [expected[name] for name in original_order]
            placeholders = ",".join("?" for _ in original_order)
            db.execute(
                f"INSERT INTO shuffled_facts VALUES ({placeholders})", values
            )
            db.commit()
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM shuffled_facts").fetchone()
            mismatches = [
                name for name in original_order if row[name] != expected[name]
            ]
            self.assertTrue(
                mismatches,
                "a positional insert into a reordered schema was expected to "
                "misalign at least one column; if it did not, the by-name "
                "assertion in test_insert_writes_each_dimension_under_its_own_"
                "column_name is not discriminating and would pass even on a "
                "broken _insert()",
            )


class ContractStatusTests(unittest.TestCase):
    """The contract loader step itself (README.md's documented convention),
    plus a guard that the two S4-owned surfaces are no longer marked
    deferred."""

    def test_contract_loads_and_is_the_expected_shape(self):
        self.assertEqual(CONTRACT["contract"], "raw_contribution_columns")
        self.assertIn("chart_studio_dimensions", CONTRACT["surfaces"])
        self.assertIn("contributor_detail_sheet", CONTRACT["surfaces"])

    def test_chart_studio_dimensions_no_longer_deferred(self):
        surface = CONTRACT["surfaces"]["chart_studio_dimensions"]
        self.assertNotEqual(surface.get("status"), "deferred to S4")

    def test_contributor_detail_sheet_no_longer_deferred(self):
        surface = CONTRACT["surfaces"]["contributor_detail_sheet"]
        self.assertNotEqual(surface.get("status"), "deferred to S4")


class SourcesSkipsExcelLockFilesTests(unittest.TestCase):
    """Excel leaves a `~$<name>.xlsx` lock file beside an open workbook while
    it is being edited/viewed. _sources() globs `* Industry Spending
    Overview.xlsx`, which also matches that lock file, and previously listed
    it as a phantom extra snapshot (observed live: '~$Credit Union Industry
    Spending Overview.xlsx' surfaced as industry '~$Credit Union', colliding
    with the real Credit Union book's slug). server.py's
    Console.published_reports() already skips any name starting with '~$';
    _sources() now does the same.
    """

    def test_sources_skips_an_excel_lock_file_and_returns_only_the_real_book(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Banking Industry Spending Overview.xlsx").write_bytes(b"")
            (root / "~$Banking Industry Spending Overview.xlsx").write_bytes(b"")
            sources = c._sources(root)
            self.assertEqual(len(sources), 1, f"expected exactly one source, got {sources!r}")
            self.assertEqual(sources[0]["industry"], "Banking")
            # Both are "unavailable" (empty files, no real workbook contents/
            # cache manifest behind them); what is under test is that the
            # lock file never becomes a second, phantom source at all.
            self.assertFalse(sources[0]["available"])


if __name__ == "__main__":
    unittest.main()

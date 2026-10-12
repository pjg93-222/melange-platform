"""Dated coverage must reject incomplete FEC pages before issuing a receipt."""

from __future__ import annotations

import json
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))
import verify_fec_date_coverage as dated  # noqa: E402


def response(rows, count, exact=True, cursor=None):
    return {"status": 200, "headers": {}, "body": json.dumps({
        "results": rows,
        "pagination": {"count": count, "is_count_exact": exact,
                       "last_indexes": cursor or {}},
    })}


class FakePool:
    replies = None
    calls = None

    def __init__(self, keys, budget, workers):
        self.calls = []
        FakePool.calls = self.calls

    def batch(self, specs, lane):
        if len(specs) > 128:
            raise AssertionError("pool batches must not exceed 128")
        self.calls.append([dict(spec["params"]) for spec in specs])
        return [FakePool.replies(spec) for spec in specs]

    def snapshot(self):
        return {"requested_workers": 2, "peak_active_workers": 2}

    def close(self):
        pass


class DatedCoverageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mentat-dated-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "source.db"
        self.fanout = self.root / "fanout.json"
        self.fanout.write_text('{"status":"succeeded"}', encoding="utf-8")
        self.output = self.root / "dated"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE api_work_progress (unit_id TEXT, company TEXT, "
                       "cycle INTEGER, kind TEXT, query_contract TEXT)")
            db.execute("CREATE TABLE contributions (sub_id TEXT, industry TEXT, "
                       "company TEXT, committee_id TEXT, contributor_name TEXT, "
                       "contribution_receipt_date TEXT, contribution_receipt_amount REAL)")
            db.execute("CREATE TABLE corporate_giving (sub_id TEXT, industry TEXT, "
                       "company TEXT, committee_id TEXT, contributor_name TEXT, "
                       "contribution_receipt_date TEXT, contribution_receipt_amount REAL)")
        self.addCleanup(patch.stopall)
        patch.object(dated, "discover_keys", return_value=["fixture-a", "fixture-b"]).start()
        patch.object(dated, "RequestPool", FakePool).start()
        patch.object(dated, "validate", side_effect=lambda *args: {
            "status": "succeeded", "companies": ["Fixture"], "fec_coverage": []
        }).start()

    def add_query(self, unit="fixture-unit"):
        contract = {"request": {"url": dated.ENDPOINT,
                                "params": {"contributor_employer": "FIXTURE"}}}
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO api_work_progress VALUES (?,?,?,?,?)",
                       (unit, "Fixture", 2024, "employer", json.dumps(contract)))

    def add_row(self, sub_id):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO contributions VALUES (?,?,?,?,?,?,?)",
                       (sub_id, "Fixture Industry", "Fixture", "C1", "PERSON",
                        "2024-01-01", 10.0))

    def run_verify(self):
        return dated.verify(self.database, self.fanout, "Fixture Industry",
                            (2024,), self.output, self.root / "budget.db")

    def test_empty_eligible_query_set_cannot_certify_coverage(self):
        FakePool.replies = lambda spec: response([], 0)
        with self.assertRaisesRegex(ValueError, "No eligible original FEC query"):
            self.run_verify()
        self.assertFalse((self.output / "coverage-receipt.json").exists())

    def test_approximate_count_cannot_certify_one_page(self):
        self.add_query()
        FakePool.replies = lambda spec: response([self.row("1")], 1, exact=False)
        with self.assertRaisesRegex(ValueError, "exact dated result count"):
            self.run_verify()
        self.assertFalse((self.output / "coverage-receipt.json").exists())

    def test_count_change_across_pages_is_rejected(self):
        self.add_query()
        self.add_row("1")
        FakePool.replies = lambda spec: (
            response([self.row("1")], 2, cursor={"last_index": "1"})
            if "last_index" not in spec["params"] else response([], 1)
        )
        with self.assertRaisesRegex(ValueError, "count changed"):
            self.run_verify()

    def test_exact_two_pages_reconcile_to_unchanged_database(self):
        self.add_query()
        self.add_row("1")
        self.add_row("2")
        FakePool.replies = lambda spec: (
            response([self.row("1")], 2, cursor={"last_index": "1"})
            if "last_index" not in spec["params"] else response([self.row("2")], 2)
        )
        before = dated.digest(self.database)
        result = self.run_verify()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(before, dated.digest(self.database))
        self.assertEqual(len(FakePool.calls), 2)
        self.assertEqual(FakePool.calls[1][0]["last_index"], "1")
        self.assertEqual(len(list(self.output.glob("query-1-page-*.json"))), 2)
        self.assertEqual(len(json.loads((self.output / "fanout-with-dated-evidence.json").read_text())
                             ["fec_date_evidence"]), 1)

    def test_initial_queries_are_chunked_at_pool_limit(self):
        for index in range(129):
            self.add_query(f"fixture-{index}")
        FakePool.replies = lambda spec: response([], 0)
        self.run_verify()
        self.assertEqual([len(batch) for batch in FakePool.calls], [128, 1])

    @staticmethod
    def row(sub_id):
        return {"sub_id": sub_id, "committee_id": "C1",
                "contributor_name": "PERSON", "contribution_receipt_date": "2024-01-01",
                "contribution_receipt_amount": 10.0}


if __name__ == "__main__":
    unittest.main()

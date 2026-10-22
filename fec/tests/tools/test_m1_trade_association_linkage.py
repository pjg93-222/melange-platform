# -*- coding: utf-8 -*-
"""Contract tests for M1 trade-association record linkage."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fec.tools import m1_trade_association_linkage as m1


EXPECTED_COLUMNS = list(m1.REVIEW_COLUMNS)
EXPECTED_TOP_IDS = {"q-exact": "c-exact", "q-fuzzy": "c-tie-a"}


class M1TradeAssociationLinkageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.directory = Path(self.scratch.name)
        self.queries = self.directory / "queries.csv"
        self.candidates = self.directory / "candidates.csv"
        self.output = self.directory / "review.csv"
        self._write_csv(
            self.queries,
            ["source_record_id", "source_name", "ignored"],
            [
                ["q-exact", "  ACME & Co.  ", "x"],
                ["q-fuzzy", "Natl Builders Assn", "y"],
            ],
        )
        self._write_csv(
            self.candidates,
            ["candidate_id", "candidate_name", "ignored"],
            [
                ["c-exact", "Acme and Co", "x"],
                ["c-tie-b", "National Builders Association", "x"],
                ["c-tie-a", "National Builders Association", "x"],
                ["c-other", "Maritime Trades Council", "x"],
            ],
        )

    @staticmethod
    def _write_csv(path: Path, columns: list[str], rows: list[list[str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)

    def _run(self, *extra: str) -> int:
        arguments = [
            "--queries",
            str(self.queries),
            "--candidates",
            str(self.candidates),
            *extra,
        ]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            return m1.main(arguments)

    def _review_rows(self) -> list[dict[str, str]]:
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_01_no_emit_review_creates_no_file(self) -> None:
        self.assertEqual(self._run("--output", str(self.output)), 0)
        self.assertFalse(self.output.exists())

    def test_02_required_headers_nonblank_and_unique_ids_fail_closed(self) -> None:
        bad = self.directory / "bad.csv"
        cases = [
            (["source_record_id", "wrong"], [["q", "name"]]),
            (["source_record_id", "source_name"], [["", "name"]]),
            (["source_record_id", "source_name"], [["q", "one"], ["q", "two"]]),
        ]
        for columns, rows in cases:
            with self.subTest(columns=columns, rows=rows):
                self._write_csv(bad, columns, rows)
                with self.assertRaises(m1.LinkageError):
                    m1.load_queries(bad)
        empty_candidates = self.directory / "empty.csv"
        self._write_csv(empty_candidates, list(m1.CANDIDATE_COLUMNS), [])
        with self.assertRaises(m1.LinkageError):
            m1.load_candidates(empty_candidates)

    def test_03_normalization_is_exact(self) -> None:
        self.assertEqual(
            m1.normalize_name("  ＡＣＭＥ\t&\nCo.—North! "),
            "acme and co north",
        )

    def test_04_default_backend_is_sklearn_char_wb_3_5_tfidf(self) -> None:
        backend = m1.CharNgramTfidfBackend()
        self.assertEqual(backend.vectorizer.analyzer, "char_wb")
        self.assertEqual(backend.vectorizer.ngram_range, (3, 5))
        self.assertFalse(backend.vectorizer.lowercase)
        self.assertEqual(backend.vectorizer.norm, "l2")
        result = backend(["acme and co"], ["acme and co", "other group"])
        self.assertAlmostEqual(result[0][0], 1.0)

    def test_05_exact_and_fuzzy_fixtures_rank_correctly(self) -> None:
        self.assertEqual(
            self._run(
                "--emit-review",
                "--output",
                str(self.output),
                "--top-k",
                "1",
            ),
            0,
        )
        actual = {row["source_record_id"]: row["candidate_id"] for row in self._review_rows()}
        self.assertEqual(actual, EXPECTED_TOP_IDS)

    def test_06_ties_resolve_by_normalized_name_then_id(self) -> None:
        queries, q_hash = m1.load_queries(self.queries)
        candidates, c_hash = m1.load_candidates(self.candidates)
        scores = [[0.0, 0.5, 0.5, 0.0], [0.0, 0.5, 0.5, 0.0]]
        rows = m1.build_review_rows(
            queries, candidates, scores, "fixture", 4, 0.35, q_hash, c_hash
        )
        fuzzy_ids = [
            row["candidate_id"] for row in rows if row["source_record_id"] == "q-fuzzy"
        ]
        self.assertEqual(fuzzy_ids[:2], ["c-tie-a", "c-tie-b"])

    def test_07_every_query_emits_top_k_even_below_floor(self) -> None:
        self.assertEqual(
            self._run(
                "--emit-review",
                "--output",
                str(self.output),
                "--top-k",
                "3",
                "--review-floor",
                "1",
            ),
            0,
        )
        rows = self._review_rows()
        self.assertEqual(len(rows), 6)
        fuzzy = [row for row in rows if row["source_record_id"] == "q-fuzzy"]
        self.assertEqual(len(fuzzy), 3)
        self.assertTrue(all(row["queue_reason"] == "BELOW_REVIEW_FLOOR" for row in fuzzy))

    def test_08_columns_format_reasons_blanks_and_hashes_exact(self) -> None:
        self.assertEqual(
            self._run("--emit-review", "--output", str(self.output), "--top-k", "2"),
            0,
        )
        with self.output.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(reader.fieldnames, EXPECTED_COLUMNS)
            rows = list(reader)
        q_hash = hashlib.sha256(self.queries.read_bytes()).hexdigest()
        c_hash = hashlib.sha256(self.candidates.read_bytes()).hexdigest()
        for row in rows:
            self.assertRegex(row["similarity_score"], r"^\d\.\d{8}$")
            self.assertIn(row["queue_reason"], m1.QUEUE_REASONS)
            self.assertEqual(
                [row[field] for field in ("review_decision", "reviewed_candidate_id", "reviewer", "review_notes")],
                ["", "", "", ""],
            )
            self.assertEqual(row["queries_sha256"], q_hash)
            self.assertEqual(row["candidates_sha256"], c_hash)
        self.assertEqual(rows[0]["schema_version"], m1.SCHEMA_VERSION)

    def test_09_inputs_byte_identical_and_no_extra_file(self) -> None:
        before_queries = self.queries.read_bytes()
        before_candidates = self.candidates.read_bytes()
        before_names = {path.name for path in self.directory.iterdir()}
        self.assertEqual(
            self._run("--emit-review", "--output", str(self.output)), 0
        )
        self.assertEqual(self.queries.read_bytes(), before_queries)
        self.assertEqual(self.candidates.read_bytes(), before_candidates)
        self.assertEqual(
            {path.name for path in self.directory.iterdir()} - before_names,
            {"review.csv"},
        )

    def test_10_atomic_failure_preserves_prior_without_fixed_scratch(self) -> None:
        self.output.write_bytes(b"prior\n")
        with mock.patch.object(m1.os, "replace", side_effect=OSError("fixture failure")):
            self.assertEqual(
                self._run("--emit-review", "--output", str(self.output)), 2
            )
        self.assertEqual(self.output.read_bytes(), b"prior\n")
        self.assertEqual(
            sorted(path.name for path in self.directory.iterdir()),
            ["candidates.csv", "queries.csv", "review.csv"],
        )

    def test_11_plugin_never_imported_by_default_and_malformed_fails(self) -> None:
        queries, _ = m1.load_queries(self.queries)
        candidates, _ = m1.load_candidates(self.candidates)
        with mock.patch.object(
            m1.importlib, "import_module", side_effect=AssertionError("plug-in imported")
        ):
            backend_name, scores = m1.score_queries(queries, candidates)
        self.assertEqual(backend_name, m1.DEFAULT_BACKEND_NAME)
        self.assertEqual((len(scores), len(scores[0])), (2, 4))
        fake_module = type("FakeModule", (), {"backend": staticmethod(lambda q, c: [[2.0]])})
        with mock.patch.object(m1.importlib, "import_module", return_value=fake_module):
            with self.assertRaises(m1.LinkageError):
                m1.score_queries(queries, candidates, "fake:backend")

    def test_12_non_csv_alias_and_missing_parent_fail_before_write(self) -> None:
        not_csv = self.directory / "queries.txt"
        not_csv.write_bytes(self.queries.read_bytes())
        original_queries = self.queries
        self.queries = not_csv
        try:
            self.assertEqual(self._run(), 2)
        finally:
            self.queries = original_queries
        self.assertEqual(
            self._run("--emit-review", "--output", str(self.queries)), 2
        )
        missing = self.directory / "missing" / "review.csv"
        self.assertEqual(
            self._run("--emit-review", "--output", str(missing)), 2
        )
        self.assertFalse(missing.exists())

    def test_13_each_rank_uses_its_own_queue_reason(self) -> None:
        queries, q_hash = m1.load_queries(self.queries)
        candidates, c_hash = m1.load_candidates(self.candidates)
        rows = m1.build_review_rows(
            queries[:1],
            candidates,
            [[1.0, 0.8, 0.4, 0.1]],
            "fixture",
            4,
            0.35,
            q_hash,
            c_hash,
        )
        self.assertEqual(
            [row["queue_reason"] for row in rows],
            [
                "NORMALIZED_EXACT",
                "FUZZY_CANDIDATE",
                "FUZZY_CANDIDATE",
                "BELOW_REVIEW_FLOOR",
            ],
        )

    def test_14_normalization_strips_unicode_format_controls(self) -> None:
        self.assertEqual(
            m1.normalize_name("National\u200b Builders\ufeff Association"),
            "national builders association",
        )

    def test_15_default_backend_self_match_scores_stay_within_unit_interval(self) -> None:
        names = [
            m1.normalize_name(n)
            for n in (
                "Credit Union National Association",
                "Securities Industry and Financial Markets Association",
                "American Bankers Association",
                "National Association of Realtors",
                "National Rural Electric Cooperative Association",
                "Independent Community Bankers of America",
                "American Petroleum Institute",
                "Pharmaceutical Research and Manufacturers of America",
                "National Association of Home Builders",
                "American Council of Life Insurers",
            )
        ]
        scores = m1.CharNgramTfidfBackend()(names, names)
        self.assertTrue(all(0.0 <= v <= 1.0 for row in scores for v in row))
        m1.validate_score_matrix(scores, len(names), len(names))


if __name__ == "__main__":
    unittest.main()

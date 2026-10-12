"""The dwarfed digest: refunds, contradictions and large counted receipts.

These tests fix the shape the console now reads. The engine and the runner keep
their accounting decisions; the digest only decides which of those decisions a
reader is shown, so every case here is stated as a row the digest must list and
a neighbouring row it must not.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from cross_reference_digest import (LARGE_MEDIAN_MULTIPLE, LARGE_PERCENTILE, MINIMUM_CLASS_RECORDS,
                                    build_digest, main, nearest_rank)
from cross_reference_data import latest_cross_reference

LEDGER_SCHEMA = """
  CREATE TABLE source_records(id INTEGER PRIMARY KEY,source_id TEXT,source_table TEXT,payload_json TEXT);
  CREATE INDEX source_record_lookup ON source_records(source_id);
  CREATE TABLE decisions(id INTEGER PRIMARY KEY,source_id TEXT,payload_json TEXT);
  CREATE INDEX decision_lookup ON decisions(source_id);
  CREATE TABLE links(id INTEGER PRIMARY KEY,payload_json TEXT);
  CREATE TABLE changes(id INTEGER PRIMARY KEY,source_id TEXT,payload_json TEXT);
"""


def decision(source_id, amount_cents, counted_cents, baseline_cents, **updates):
    value = {"source_id": source_id, "company": "A Bank", "cycle": 2024, "amount_cents": amount_cents,
             "baseline_cents": baseline_cents, "counted_cents": counted_cents, "bucket": "employee_original",
             "receipt_role": "contribution", "reasons": [], "status": "source_verified",
             "evidence_ids": [source_id]}
    value.update(updates)
    return value


def change(record_id, before, after, **updates):
    value = {"record_id": record_id, "company": "A Bank", "cycle": 2024, "reason": "",
             "before": before, "after": after, "delta": after - before,
             "source_description": "", "source_url": ""}
    value.update(updates)
    return value


# Twenty ordinary individual gifts, so the individual class is big enough to
# have a distribution worth cutting at all.
FILLER = [decision(f"filler{index:02d}", 1_000, 1_000, 1_000) for index in range(MINIMUM_CLASS_RECORDS)]

# One refund the engine linked, one negative adjustment it could not link, one
# reclassification that took money out of the accepted baseline and one that
# took nothing, one outsized individual receipt and one larger organisation
# receipt whose own class is too small to cut, and one ordinary gift.
DECISIONS = [
    decision("orig", 5_000_000, 5_000_000, 5_000_000),
    decision("refund1", -200_000, -200_000, -200_000, bucket="linked_refund",
             reasons=["explicit_observed_refund"]),
    decision("refund2", -750_000, -750_000, 0, reasons=["negative_amount_is_not_refund_proof"]),
    decision("nongiving_counted", 1_500_000, 0, 1_500_000, bucket="non_giving", receipt_role="other_receipt",
             reasons=["non_giving_receipt_role:other_receipt"]),
    decision("nongiving_zero", 900_000, 0, 0, bucket="non_giving", receipt_role="asset_liquidation",
             reasons=["non_giving_receipt_role:asset_liquidation"]),
    decision("large_ind", 12_000_000, 12_000_000, 12_000_000),
    decision("large_org", 50_000_000, 50_000_000, 50_000_000),
    decision("ordinary", 100_000, 100_000, 100_000),
    *FILLER,
]
LINKS = [
    {"kind": "refund", "source_id": "refund1", "counterpart_id": "orig", "amount_cents": -200_000,
     "evidence_ids": ["orig", "proof-1"]},
    {"kind": "memo", "source_id": "memo1", "counterpart_id": "orig", "amount_cents": 5_000_000,
     "evidence_ids": ["orig"]},
]
CHANGES = [
    change("nongiving_counted", 15_000.0, 0.0, source_description="INTEREST ON DEPOSIT",
           source_url="https://docquery.fec.gov/pdf/111/one.pdf"),
    change("refund2", 0.0, -7_500.0, source_description="RETURN OF ERRONEOUS DEBIT",
           source_url="https://docquery.fec.gov/pdf/222/two.pdf"),
]
ENTITIES = {"large_ind": "IND", "large_org": "ORG", "ordinary": "IND", "orig": "IND",
            **{row["source_id"]: "IND" for row in FILLER}}
URLS = {"orig": "https://docquery.fec.gov/pdf/000/orig.pdf",
        "refund1": "https://docquery.fec.gov/pdf/333/three.pdf",
        "large_ind": "https://docquery.fec.gov/pdf/444/four.pdf"}
# A saved ledger has no change prose to fall back from, so its source rows are
# the only place the listed rows can find a filing link.
LEDGER_URLS = {**URLS, "nongiving_counted": "https://docquery.fec.gov/pdf/111/one.pdf",
               "refund2": "https://docquery.fec.gov/pdf/222/two.pdf"}


class DigestTests(unittest.TestCase):
    def digest(self, **updates):
        arguments = {"run_id": "20260917-fixture", "generated_at": "2026-09-17T00:00:00+00:00",
                     "entities": ENTITIES, "source_urls": URLS}
        arguments.update(updates)
        return build_digest(iter(DECISIONS), iter(LINKS), iter(CHANGES), **arguments)

    def test_clear_refunds_are_listed_with_both_sides_of_the_pair(self):
        refunds = self.digest()["refunds"]
        self.assertEqual(refunds["linked_count"], 1)
        self.assertEqual(refunds["linked_amount"], -2_000.0)
        row = refunds["linked"][0]
        self.assertEqual(row["refund_record_id"], "refund1")
        self.assertEqual(row["original_record_id"], "orig")
        self.assertEqual(row["refund_amount"], -2_000.0)
        self.assertEqual(row["original_amount"], 50_000.0)
        self.assertEqual(row["refund_source_url"], URLS["refund1"])
        self.assertEqual(row["original_source_url"], URLS["orig"])
        self.assertEqual(row["company"], "A Bank")
        self.assertEqual(row["cycle"], 2024)

    def test_a_negative_adjustment_without_a_matched_original_is_listed_separately(self):
        refunds = self.digest()["refunds"]
        self.assertEqual(refunds["unlinked_count"], 1)
        self.assertEqual(refunds["unlinked_amount"], -7_500.0)
        row = refunds["unlinked"][0]
        self.assertEqual(row["record_id"], "refund2")
        self.assertEqual(row["amount"], -7_500.0)
        self.assertEqual(row["description"], "RETURN OF ERRONEOUS DEBIT")
        self.assertEqual(row["source_url"], "https://docquery.fec.gov/pdf/222/two.pdf")
        self.assertNotIn("refund2", [item["refund_record_id"] for item in refunds["linked"]])

    def test_the_review_queue_name_for_the_same_case_is_also_accepted(self):
        rows = [decision("adjust", -100_000, -100_000, 0,
                         reasons=["signed_adjustment_not_linked_refund"])]
        refunds = build_digest(rows, [], [])["refunds"]
        self.assertEqual(refunds["unlinked_count"], 1)
        self.assertEqual(refunds["unlinked"][0]["record_id"], "adjust")

    def test_only_an_evidence_backed_reclassification_is_listed_as_a_contradiction(self):
        outliers = self.digest()["outliers"]
        self.assertEqual(outliers["non_giving_count"], 1)
        self.assertEqual(outliers["non_giving_amount"], 15_000.0)
        row = outliers["non_giving"][0]
        self.assertEqual(row["record_id"], "nongiving_counted")
        self.assertEqual(row["amount"], 15_000.0)
        self.assertEqual(row["role"], "other_receipt")
        self.assertEqual(row["description"], "INTEREST ON DEPOSIT")
        self.assertEqual(row["source_url"], "https://docquery.fec.gov/pdf/111/one.pdf")
        # A baseline of zero means the accepted report never counted the money.
        self.assertNotIn("nongiving_zero", [item["record_id"] for item in outliers["non_giving"]])

    def test_the_size_cut_is_relative_to_the_row_s_own_class(self):
        outliers = self.digest()["outliers"]
        self.assertEqual(outliers["large_count"], 1)
        self.assertEqual(outliers["large_amount"], 120_000.0)
        row = outliers["large"][0]
        self.assertEqual(row["record_id"], "large_ind")
        self.assertEqual(row["entity"], "IND")
        self.assertEqual(row["amount"], 120_000.0)
        self.assertEqual(row["source_url"], URLS["large_ind"])
        # It is listed for inspection but remains fully counted; size alone is
        # never exclusion evidence.
        self.assertEqual(next(item for item in DECISIONS if item["source_id"] == "large_ind")["counted_cents"],
                         12_000_000)
        # $500,000 is the only receipt in its class, which is far too small to
        # cut, so the bigger row is the one that is not listed.
        self.assertNotIn("large_org", [item["record_id"] for item in outliers["large"]])
        self.assertNotIn("ordinary", [item["record_id"] for item in outliers["large"]])

    def test_the_class_median_and_percentile_are_nearest_rank_over_counted_rows(self):
        # Nearest rank: the value at 1-based position ceil(percentile/100 * n)
        # counting up from the smallest, so both statistics are real receipts.
        sample = [1_000] * 18 + [5_000, 1_000_000]
        self.assertEqual(nearest_rank(sample, 50), 1_000)
        self.assertEqual(nearest_rank(sample, 90), 1_000)
        self.assertEqual(nearest_rank(sample, 99.9), 1_000_000)
        rows = [decision(f"i{index:02d}", value, value, value) for index, value in enumerate(sample)]
        entities = {row["source_id"]: "IND" for row in rows}
        digest = build_digest(rows, [], [], entities=entities, percentile=90, median_multiple=20)
        individual = digest["basis"]["classes"]["individual"]
        self.assertEqual(individual["records"], 20)
        self.assertEqual(individual["median_cents"], 1_000)
        self.assertEqual(individual["percentile_cents"], 1_000)
        # 20 x the $10.00 median beats the 90th percentile, so it is the cut.
        self.assertEqual(individual["threshold_cents"], 20_000)
        self.assertEqual([row["record_id"] for row in digest["outliers"]["large"]], ["i19"])
        self.assertEqual(digest["outliers"]["large_count"], 1)
        self.assertEqual(digest["outliers"]["large_amount"], 10_000.0)

    def test_a_row_over_the_percentile_but_under_the_median_multiple_is_not_listed(self):
        # i18 is $50, above the 90th percentile of $10, and still nowhere near
        # 20 x the median. One arm of the cut is not enough on its own.
        sample = [1_000] * 18 + [5_000, 1_000_000]
        rows = [decision(f"i{index:02d}", value, value, value) for index, value in enumerate(sample)]
        entities = {row["source_id"]: "IND" for row in rows}
        listed = build_digest(rows, [], [], entities=entities,
                              percentile=90, median_multiple=20)["outliers"]["large"]
        self.assertNotIn("i18", [row["record_id"] for row in listed])
        # Drop the multiple to 1 and the percentile alone decides.
        listed = build_digest(rows, [], [], entities=entities,
                              percentile=90, median_multiple=1)["outliers"]["large"]
        self.assertEqual(len(listed), 20)

    def test_a_class_under_the_minimum_size_lists_nothing_and_says_so(self):
        sample = [1_000] * (MINIMUM_CLASS_RECORDS - 2) + [900_000_000]
        rows = [decision(f"i{index:02d}", value, value, value) for index, value in enumerate(sample)]
        entities = {row["source_id"]: "IND" for row in rows}
        digest = build_digest(rows, [], [], entities=entities)
        individual = digest["basis"]["classes"]["individual"]
        self.assertEqual(individual["records"], MINIMUM_CLASS_RECORDS - 1)
        self.assertIsNone(individual["threshold_cents"])
        self.assertEqual(digest["outliers"]["large_count"], 0)
        self.assertEqual(digest["outliers"]["large_amount"], 0)
        self.assertIn(f"fewer than {MINIMUM_CLASS_RECORDS}", digest["basis"]["text"])
        self.assertIn("individuals", digest["basis"]["text"])

    def test_an_unknown_entity_type_joins_the_non_individual_class(self):
        sample = [1_000] * 19 + [1_000_000]
        rows = [decision(f"o{index:02d}", value, value, value) for index, value in enumerate(sample)]
        digest = build_digest(rows, [], [], percentile=90, median_multiple=20)
        self.assertEqual(digest["basis"]["classes"]["other"]["records"], 20)
        self.assertEqual(digest["basis"]["classes"]["individual"]["records"], 0)
        self.assertEqual([row["record_id"] for row in digest["outliers"]["large"]], ["o19"])
        self.assertEqual(digest["outliers"]["large"][0]["entity"], "")

    def test_a_zeroed_row_is_never_a_large_receipt(self):
        rows = [*FILLER, decision("zeroed", 900_000_000, 0, 900_000_000,
                                  reasons=["company_attribution_not_accepted"])]
        entities = {row["source_id"]: "IND" for row in rows}
        digest = build_digest(rows, [], [], entities=entities)
        self.assertEqual(digest["basis"]["classes"]["individual"]["records"], MINIMUM_CLASS_RECORDS)
        self.assertEqual(digest["outliers"]["large_count"], 0)

    def test_rows_are_ordered_by_size_and_capped_without_losing_the_count(self):
        rows, changes = [], []
        for index in range(250):
            rows.append(decision(f"n{index:03d}", (index + 1) * 1_000, 0, (index + 1) * 1_000,
                                 receipt_role="other_receipt",
                                 reasons=["non_giving_receipt_role:other_receipt"]))
            rows.append(decision(f"r{index:03d}", -(index + 1) * 1_000, -(index + 1) * 1_000, 0,
                                 reasons=["negative_amount_is_not_refund_proof"]))
        digest = build_digest(rows, [], changes)
        listed = digest["outliers"]["non_giving"]
        self.assertEqual(len(listed), 200)
        self.assertEqual(digest["outliers"]["non_giving_count"], 250)
        self.assertEqual([row["record_id"] for row in listed[:3]], ["n249", "n248", "n247"])
        self.assertEqual([row["amount"] for row in listed[:2]], [2_500.0, 2_490.0])
        self.assertEqual(digest["outliers"]["non_giving_amount"], sum(
            (index + 1) * 10 for index in range(250)))
        negatives = digest["refunds"]["unlinked"]
        self.assertEqual([row["record_id"] for row in negatives[:2]], ["r249", "r248"])
        self.assertEqual(digest["refunds"]["unlinked_count"], 250)

    def test_identical_inputs_produce_identical_bytes(self):
        first, second = self.digest(), self.digest()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(first["counts"]["source_records"], len(DECISIONS))

    def test_the_stated_basis_prints_both_computed_thresholds_and_asks_for_ratification(self):
        digest = self.digest()
        basis = digest["basis"]
        self.assertEqual(basis["percentile"], LARGE_PERCENTILE)
        self.assertEqual(basis["median_multiple"], LARGE_MEDIAN_MULTIPLE)
        self.assertEqual(basis["minimum_class_records"], MINIMUM_CLASS_RECORDS)
        individual = basis["classes"]["individual"]
        self.assertEqual(individual["records"], MINIMUM_CLASS_RECORDS + 3)
        self.assertEqual(individual["median_cents"], 1_000)
        self.assertEqual(individual["percentile_cents"], 12_000_000)
        self.assertEqual(individual["threshold_cents"], 12_000_000)
        self.assertEqual(individual["listed_count"], 1)
        self.assertEqual(individual["listed_cents"], 12_000_000)
        self.assertEqual(basis["classes"]["other"]["records"], 1)
        self.assertIsNone(basis["classes"]["other"]["threshold_cents"])
        self.assertIn("top 0.1%", basis["text"])
        self.assertIn("20x", basis["text"])
        self.assertIn("$120,000.00", basis["text"])
        self.assertIn("ratification", basis["text"])
        self.assertEqual(digest["schema_version"], 1)
        self.assertEqual(digest["run_id"], "20260917-fixture")
        self.assertEqual(digest["generated_at"], "2026-09-17T00:00:00+00:00")

    def test_a_percentile_or_multiple_outside_its_range_is_refused(self):
        for value in (0, -1, 89.9, 100, 100.1, True, "99.9"):
            with self.subTest(percentile=value):
                with self.assertRaises(ValueError):
                    build_digest([], [], [], percentile=value)
        for value in (0, -1, 1.5, True, "20"):
            with self.subTest(median_multiple=value):
                with self.assertRaises(ValueError):
                    build_digest([], [], [], median_multiple=value)

    def test_the_digest_publishes_no_total_of_giving(self):
        text = json.dumps(self.digest())
        for forbidden in ("observed_original_giving", "provisional_original_giving", "baseline_amount",
                          "pac_outgoing", "employee_giving", "partisan"):
            self.assertNotIn(forbidden, text)


class DigestCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "20260917T000000-fixture"
        self.run.mkdir()
        self.ledger = self.run / "ledger.sqlite"
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.executescript(LEDGER_SCHEMA)
            db.executemany("INSERT INTO decisions(source_id,payload_json) VALUES(?,?)",
                           [(row["source_id"], json.dumps(row)) for row in DECISIONS])
            db.executemany("INSERT INTO links(payload_json) VALUES(?)",
                           [(json.dumps(row),) for row in LINKS])
            # The ledger stores the engine's change rows, which carry no prose.
            db.executemany("INSERT INTO changes(source_id,payload_json) VALUES(?,?)",
                           [(row["record_id"], json.dumps({"source_id": row["record_id"],
                                                           "baseline_cents": 0, "counted_cents": 0,
                                                           "delta_cents": 0, "reasons": []}))
                            for row in CHANGES])
            db.executemany("INSERT INTO source_records(source_id,source_table,payload_json) VALUES(?,?,?)",
                           [(row["source_id"], "contributions", json.dumps({
                               "sub_id": row["source_id"], "entity_type": ENTITIES.get(row["source_id"], ""),
                               "pdf_url": LEDGER_URLS.get(row["source_id"], ""),
                               "receipt_type_full": "INTEREST" if row["source_id"] == "nongiving_counted" else "",
                               "memo_text": "ON DEPOSIT" if row["source_id"] == "nongiving_counted" else "",
                               "contributor_name": "PERSON, ONE"}))
                            for row in DECISIONS])
        (self.run / "report.json").write_text(json.dumps(
            {"schema_version": 1, "run_id": self.run.name, "status": "completed"}), encoding="utf-8")

    def test_the_command_writes_only_digest_json_and_reads_the_ledger_read_only(self):
        before = {path: path.read_bytes() for path in self.run.iterdir()}
        self.assertEqual(main([str(self.run)]), 0)
        digest = json.loads((self.run / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(digest["run_id"], self.run.name)
        self.assertEqual(digest["counts"]["source_records"], len(DECISIONS))
        self.assertEqual(digest["refunds"]["linked_count"], 1)
        self.assertEqual(digest["refunds"]["linked"][0]["original_record_id"], "orig")
        self.assertEqual(digest["refunds"]["unlinked_count"], 1)
        self.assertEqual(digest["outliers"]["non_giving_count"], 1)
        self.assertEqual(digest["outliers"]["large"][0]["record_id"], "large_ind")
        # The prose the ledger's change rows do not carry is read from the source row.
        self.assertEqual(digest["outliers"]["non_giving"][0]["description"], "INTEREST; ON DEPOSIT")
        self.assertEqual(digest["outliers"]["non_giving"][0]["source_url"],
                         "https://docquery.fec.gov/pdf/111/one.pdf")
        self.assertNotIn("PERSON, ONE", json.dumps(digest))
        self.assertEqual(before, {path: path.read_bytes() for path in self.run.iterdir()
                                  if path.name != "digest.json"})
        stamp = datetime.fromisoformat(digest["generated_at"])
        self.assertIsNotNone(stamp.tzinfo)

    def test_a_missing_ledger_is_a_refusal_not_an_empty_digest(self):
        self.ledger.unlink()
        with self.assertRaises(SystemExit):
            main([str(self.run)])
        self.assertFalse((self.run / "digest.json").exists())

    def test_the_console_payload_carries_the_digest_and_no_report_total(self):
        self.assertEqual(main([str(self.run)]), 0)
        root = self.run.parent
        (root / "latest.json").write_text(json.dumps(
            {"schema_version": 1, "run_id": self.run.name}), encoding="utf-8")
        (self.run / "report.json").write_text(json.dumps(
            {"schema_version": 1, "run_id": self.run.name, "status": "completed",
             "generated_at": datetime.now(timezone.utc).isoformat(),
             "scope": {"industries": ["Banking"], "companies": ["A Bank"], "cycles": [2024]},
             "summary": {"observed_original_giving": 987_654.32, "pac_outgoing": 123_456.78},
             "partisan_breakdowns": [{"view": "original_giving", "democratic": 1.0}],
             "review_queue": [{"record_id": "orig"}], "changes": [{"record_id": "orig"}],
             "limitations": ["Collection coverage remains unverified."]}), encoding="utf-8")
        payload = latest_cross_reference(root)
        self.assertTrue(payload["available"], payload)
        report = payload["report"]
        self.assertTrue(report["digest_available"])
        self.assertEqual(report["digest"]["refunds"]["linked"][0]["original_record_id"], "orig")
        text = json.dumps(payload)
        self.assertNotIn("observed_original_giving", text)
        self.assertNotIn("partisan", text)
        # The report's own totals cannot reach the browser through any key.
        self.assertNotIn("987654", text)
        self.assertNotIn("123456", text)
        for absent in ("summary", "partisan_breakdowns", "unresolved_reasons", "coverage_reasons",
                       "review_queue", "changes"):
            self.assertNotIn(absent, report)


if __name__ == "__main__":
    unittest.main()

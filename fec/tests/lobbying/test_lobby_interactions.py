# -*- coding: utf-8 -*-
"""Contracts for the posture badge, the surface tabs, and the interaction ledger.

FEATURE REQUEST (product owner, 2026-08-03):
  1. a tab back to the main dashboard from the industry tab
  2. per (industry, member) and (organization, member), whether the industry is
     cultivating, maintaining, or disengaging that relationship
  3. clicking a member opens every interaction, sorted by year, carrying the
     date, the filing lobbyist, the bill, the position taken, and the notes

The load-bearing test in this file is the one that proves the ledger AGREES WITH
THE SHIPPED NETWORK.  A drill-down that quietly describes a different corpus than
the counts above it is worse than no drill-down, so ``verify_against_network``
compares every dyad and every n_bills and the build refuses to write on any
disagreement.

The position tests pin the honest answer rather than a convenient one: the LD-2
form has no position field, so the column is wired end to end and deliberately
empty, and the page says why on its face.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from lobbying import lobby_posture as LP
from lobbying import lobby_interactions as LI
from lobbying import lobby_position_join as LPJ
from surfaces import make_lobby_interactions_html as MLI
from surfaces import make_lobby_network_html as MLN


def _csv(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class PostureRule(unittest.TestCase):
    """The classification itself, independent of any page."""

    def test_rising_falling_and_steady_split_on_the_band(self):
        # prior window 2022+2023 = 8; the band is 25%, so 10 rises and 6 falls
        self.assertEqual(LP.classify_values([0, 4, 4, 5, 5, 0])["state"], "rising")
        self.assertEqual(LP.classify_values([0, 4, 4, 3, 3, 0])["state"], "falling")
        self.assertEqual(LP.classify_values([0, 4, 4, 4, 4, 0])["state"], "steady")

    def test_every_state_maps_to_exactly_one_posture_bucket(self):
        for state, (bucket, posture_label, trend_label, meaning) in LP.STATES.items():
            self.assertIn(
                bucket,
                {
                    LP.POSTURE_CULTIVATING,
                    LP.POSTURE_MAINTAINING,
                    LP.POSTURE_DISENGAGING,
                    LP.POSTURE_UNKNOWN,
                },
                state,
            )
            # both vocabularies must exist for the toggle, and neither may be blank
            self.assertTrue(posture_label and trend_label and meaning, state)

    def test_the_two_vocabularies_never_disagree_about_the_bucket(self):
        """The toggle is WORDING. Switching it must not move a pair between buckets."""
        for values in ([0, 4, 4, 5, 5, 0], [1, 6, 6, 1, 1, 0], [2, 4, 4, 0, 0, 3]):
            verdict = LP.classify_values(values)
            self.assertEqual(
                LP.STATES[verdict["state"]][0], verdict["posture"], values
            )

    def test_partial_year_is_excluded_from_the_trend(self):
        """2026 is half a year. Letting it into the comparison would manufacture a
        decline for every pair on the board, so only complete years are compared."""
        without = LP.classify_values([0, 4, 4, 4, 4, 0])
        with_partial = LP.classify_values([0, 4, 4, 4, 4, 99])
        self.assertEqual(without["state"], with_partial["state"])
        self.assertEqual(without["recent"], with_partial["recent"])
        self.assertEqual(with_partial["partial"], 99)

    def test_partial_year_rescues_dormant_into_returning(self):
        """An organization filing on a member right now is not disengaging."""
        self.assertEqual(LP.classify_values([2, 4, 4, 0, 0, 0])["state"], "dormant")
        returning = LP.classify_values([2, 4, 4, 0, 0, 3])
        self.assertEqual(returning["state"], "returning")
        self.assertEqual(returning["posture"], LP.POSTURE_CULTIVATING)
        # the complete-year delta for a returning pair is always -100%, which reads
        # as the opposite of what happened; the badge shows the partial year instead
        self.assertNotIn("-100%", returning["delta"])
        self.assertIn("2026", returning["delta"])

    def test_thin_evidence_is_badged_not_given_a_direction(self):
        thin = LP.classify_values([0, 0, 1, 0, 1, 0])
        self.assertEqual(thin["state"], "insufficient")
        self.assertEqual(thin["posture"], LP.POSTURE_UNKNOWN)
        self.assertEqual(thin["posture_label"], thin["trend_label"])

    def test_pre_tenure_years_produce_new_member_not_a_percentage(self):
        active = LP.classify_values(
            [99, 99, 1, 6, 6, 0], first_service_year=2023
        )
        self.assertEqual(active["state"], "new_member")
        self.assertEqual(active["posture"], LP.POSTURE_CULTIVATING)
        self.assertEqual(active["prior"], 1)
        self.assertEqual(active["total"], 13)
        self.assertEqual(active["delta"], "—")
        self.assertNotIn("%", active["detail"])

        quiet = LP.classify_values(
            [99, 99, 1, 0, 0, 3], first_service_year=2023
        )
        self.assertEqual(quiet["state"], "new_member")
        self.assertEqual(quiet["posture"], LP.POSTURE_UNKNOWN)
        self.assertEqual(quiet["delta"], "—")

    def test_fully_tenured_series_are_byte_identical(self):
        for values in (
            [0, 4, 4, 5, 5, 0],
            [0, 4, 4, 3, 3, 0],
            [0, 4, 4, 4, 4, 0],
            [2, 4, 4, 0, 0, 3],
            [0, 0, 1, 0, 1, 0],
        ):
            self.assertEqual(
                LP.classify_values(values),
                LP.classify_values(values, first_service_year=2021),
                values,
            )

        # Service beginning at the first year of the prior window is also a
        # complete comparison; a zero pre-service context year changes nothing.
        values = [0, 4, 4, 5, 5, 0]
        self.assertEqual(
            LP.classify_values(values),
            LP.classify_values(values, first_service_year=2022),
        )

    def test_display_delta_is_capped_without_changing_the_state(self):
        exact = LP.classify_values([0, 50, 50, 550, 549, 0])
        capped = LP.classify_values([0, 50, 50, 550, 550, 0])
        self.assertEqual(exact["state"], "rising")
        self.assertEqual(exact["delta"], "+999%")
        self.assertEqual(capped["state"], "rising")
        self.assertEqual(capped["delta"], ">+999%")

    def test_band_and_floor_are_dials(self):
        previous = dict(os.environ)
        try:
            os.environ["LOBBY_POSTURE_BAND"] = "0.9"
            os.environ["LOBBY_POSTURE_MIN"] = "1"
            # +25% no longer clears a 90% band
            self.assertEqual(LP.classify_values([0, 4, 4, 5, 5, 0])["state"], "steady")
            self.assertEqual(LP.band(), 0.9)
            self.assertEqual(LP.min_evidence(), 1)
        finally:
            os.environ.clear()
            os.environ.update(previous)
        self.assertEqual(LP.band(), LP.DEFAULT_BAND)
        self.assertEqual(LP.classify_values([0, 4, 4, 5, 5, 0])["state"], "rising")


class PostureEncoding(unittest.TestCase):
    """The wire format both pages share."""

    def test_encoding_is_numbers_plus_a_shared_state_table(self):
        table = MLN.PostureTable()
        encoded = table.encode([0, 4, 4, 5, 5, 1])
        self.assertEqual(len(encoded), len(MLN.PostureTable.LAYOUT))
        state_index, recent, prior, partial, _delta = encoded
        self.assertEqual((recent, prior, partial), (10, 8, 1))
        payload = table.payload()
        self.assertEqual(payload[state_index]["n"], "rising")
        self.assertEqual(payload[state_index]["b"], LP.POSTURE_CULTIVATING)
        self.assertEqual(payload[state_index]["p"], "Cultivating")
        self.assertEqual(payload[state_index]["t"], "Rising")

    def test_repeated_states_are_interned_once(self):
        table = MLN.PostureTable()
        for _ in range(50):
            table.encode([0, 4, 4, 5, 5, 0])
        self.assertEqual(len(table.payload()), 1)
        self.assertEqual(table.receipt()["by_state"], {"rising": 50})

    def test_new_member_state_and_dynamic_bucket_share_the_wire_table(self):
        table = MLN.PostureTable()
        active = table.encode(
            [0, 0, 1, 6, 6, 0], first_service_year=2023
        )
        quiet = table.encode(
            [0, 0, 1, 0, 0, 3], first_service_year=2023
        )
        payload = table.payload()
        self.assertEqual(payload[active[0]]["n"], "new_member")
        self.assertEqual(payload[active[0]]["b"], LP.POSTURE_CULTIVATING)
        self.assertEqual(payload[active[0]]["p"], "New member")
        self.assertEqual(payload[active[0]]["t"], "New member")
        self.assertEqual(payload[quiet[0]]["n"], "new_member")
        self.assertEqual(payload[quiet[0]]["b"], LP.POSTURE_UNKNOWN)
        self.assertEqual(table.receipt()["by_state"], {"new_member": 2})
        self.assertEqual(
            table.receipt()["by_posture"][LP.POSTURE_CULTIVATING], 1
        )
        self.assertEqual(table.receipt()["by_posture"][LP.POSTURE_UNKNOWN], 1)

    def test_capped_delta_is_the_value_on_the_wire(self):
        table = MLN.PostureTable()
        encoded = table.encode([0, 1, 0, 6, 6, 0])
        self.assertEqual(encoded[4], ">+999%")


class LedgerContracts(unittest.TestCase):
    def test_position_ships_wired_and_empty_with_a_stated_basis(self):
        """The LD-2 form has no position field. The ledger must say so in data,
        not leave a blank column that reads like a collection failure."""
        self.assertIn("position", LI.LEDGER_FIELDS)
        self.assertIn("position_basis", LI.LEDGER_FIELDS)
        self.assertEqual(LI.POSITION_NOT_DISCLOSED, "not_disclosed")

    def test_ledger_carries_every_field_the_drill_down_renders(self):
        for field in (
            "filing_date",       # date
            "registrant_name",   # who filed it
            "lobbyists",         # the lobbyists named on it
            "bill_key",          # the bill discussed
            "position",          # the position taken
            "note_id",           # the notes the lobbyist left
        ):
            self.assertIn(field, LI.LEDGER_FIELDS)

    def test_network_disagreement_fails_the_build_rather_than_shipping(self):
        """A ledger describing a different corpus than the counts above it is
        worse than no ledger, so any dyad disagreement must be fatal."""
        with tempfile.TemporaryDirectory() as directory:
            paths = {"derived_dir": directory}
            for year in LI.YEARS:
                _csv(
                    os.path.join(
                        directory, f"our_network_fullscope_{year}_strict.csv"
                    ),
                    ["lob_id", "bioguide_id", "year", "n_bills"],
                    [{
                        "lob_id": "org-1",
                        "bioguide_id": "M000001",
                        "year": year,
                        "n_bills": 3,
                    }],
                )
            built = {
                "receipts": {"activities_parsed": {}},
                # deliberately wrong: 2 bills where the network says 3
                "dyads": {
                    ("org-1", "M000001", year): {"117:hr:1", "117:hr:2"}
                    for year in LI.YEARS
                },
            }
            result = LI.verify_against_network(paths, built)
            self.assertFalse(result["agrees"])
            self.assertEqual(result["dyads_mismatched"], len(LI.YEARS))
            self.assertTrue(result["problems"])

    def test_network_agreement_passes_when_the_dyads_match(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {"derived_dir": directory}
            for year in LI.YEARS:
                _csv(
                    os.path.join(
                        directory, f"our_network_fullscope_{year}_strict.csv"
                    ),
                    ["lob_id", "bioguide_id", "year", "n_bills"],
                    [{
                        "lob_id": "org-1",
                        "bioguide_id": "M000001",
                        "year": year,
                        "n_bills": 2,
                    }],
                )
            built = {
                "receipts": {"activities_parsed": {}},
                "dyads": {
                    ("org-1", "M000001", year): {"117:hr:1", "117:hr:2"}
                    for year in LI.YEARS
                },
            }
            result = LI.verify_against_network(paths, built)
            self.assertTrue(result["agrees"], result["problems"])
            self.assertEqual(result["dyads_missing"], 0)
            self.assertEqual(result["dyads_extra"], 0)


class PositionJoin(unittest.TestCase):
    """An inference must never be able to masquerade as a disclosure."""

    def _ledger(self, directory, rows):
        path = os.path.join(directory, "interaction_ledger.csv")
        _csv(path, LI.LEDGER_FIELDS, rows)
        return path

    def _row(self, note_id, bill_key):
        row = {field: "" for field in LI.LEDGER_FIELDS}
        row.update({
            "filing_year": 2024,
            "filing_uuid": "33333333-3333-4333-8333-333333333333",
            "activity_index": 0,
            "filing_date": "2024-05-05",
            "lob_id": "org-1",
            "client_name": "FIXTURE",
            "note_id": note_id,
            "bill_key": bill_key,
            "position": "",
            "position_basis": LI.POSITION_NOT_DISCLOSED,
        })
        return row

    def _classifications(self, directory, records):
        path = os.path.join(directory, "bill_positions.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return path

    def test_join_fills_position_and_relabels_the_basis(self):
        paragraph = "We strongly support H.R. 1181, the fixture bill."
        note_id = LPJ._note_id(paragraph)
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = self._ledger(directory, [self._row(note_id, "117:hr:1181")])
            classifications = self._classifications(directory, [{
                "classification_status": "ok",
                "our_position": "Support",
                "paragraph": paragraph,
                "bill_key": {"type": "HR", "number": 1181, "congress": 117},
            }])
            labels, load_receipt = LPJ.load_classifications(classifications, "zeroshot")
            receipt = LPJ.join(ledger_path, labels, "zeroshot")
            with open(ledger_path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(load_receipt["usable"], 1)
        self.assertEqual(rows[0]["position"], "Support")
        # the basis must announce the inference, never stay 'not_disclosed'
        self.assertEqual(rows[0]["position_basis"], "inferred_zeroshot")
        self.assertEqual(receipt["rows_with_an_inferred_position"], 1)
        self.assertEqual(receipt["directional_rows"], 1)

    def test_unclassified_rows_keep_the_honest_default(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = self._ledger(
                directory,
                [self._row("aaaaaaaaaaaaaaaa", "117:hr:9999")],
            )
            classifications = self._classifications(directory, [])
            labels, _receipt = LPJ.load_classifications(classifications, "zeroshot")
            receipt = LPJ.join(ledger_path, labels, "zeroshot")
            with open(ledger_path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["position"], "")
        self.assertEqual(rows[0]["position_basis"], LI.POSITION_NOT_DISCLOSED)
        self.assertEqual(receipt["rows_with_an_inferred_position"], 0)
        self.assertEqual(receipt["rows_left_not_disclosed"], 1)

    def test_failed_and_unknown_labels_are_dropped_not_guessed(self):
        paragraph = "Ambiguous text."
        with tempfile.TemporaryDirectory() as directory:
            classifications = self._classifications(directory, [
                {"classification_status": "error", "our_position": "Support",
                 "paragraph": paragraph,
                 "bill_key": {"type": "HR", "number": 1, "congress": 117}},
                {"classification_status": "ok", "our_position": "Banana",
                 "paragraph": paragraph,
                 "bill_key": {"type": "HR", "number": 2, "congress": 117}},
            ])
            labels, receipt = LPJ.load_classifications(classifications, "zeroshot")
        self.assertEqual(labels, {})
        self.assertEqual(receipt["unusable"], 2)

    def test_three_class_collapse_matches_the_validated_mapping(self):
        self.assertEqual(LPJ.THREE_CLASS["Support"], "Support")
        self.assertEqual(LPJ.THREE_CLASS["Oppose"], "Oppose")
        for label in ("Amend", "Monitor", "Engage", "Mention"):
            self.assertEqual(LPJ.THREE_CLASS[label], "Engage")

    def test_every_emittable_label_survives_the_join(self):
        """An unmapped label is dropped as unusable, not flagged. Kim's own
        'Engage' was missing from the map, which silently discarded every
        Engage row -- the failure mode this pins shut."""
        from lobbying import lobby_position_prompt_ab as prompts
        emittable = set(prompts.KIM_LABELS) | set(prompts.KIM_LLM4_LABELS)
        emittable |= set(prompts.SHIPPED_LABELS)
        missing = sorted(emittable - set(LPJ.THREE_CLASS))
        self.assertEqual(missing, [], f"labels that would be silently dropped: {missing}")

    def test_the_note_id_is_the_same_hash_the_ledger_writes(self):
        """The join key. If these two ever drift, every position silently misses."""
        paragraph = "Interchange fee legislation; H.R.1181 and related matters."
        self.assertEqual(LPJ._note_id(paragraph), LI._note_id(paragraph))

    def test_the_accuracy_caveat_names_the_weak_classes(self):
        """A headline number must never travel without what undercuts it. The
        five-class score is good; Support/Oppose are weak and Amend is absent,
        and the note has to say all three."""
        note = LPJ.ACCURACY_NOTE
        self.assertIn("INFERRED", note)
        self.assertIn("0.49", note)          # Support recall
        self.assertIn("AMEND", note)         # the class we do not recover
        self.assertIn("never as a fact", note)
        self.assertEqual(LPJ.VALIDATION["variant"], "kim_llm4")

    def test_validation_is_against_kims_published_results(self):
        """Selection was benchmarked against Kim's end results, not a curated
        sample, and the record has to carry the comparison that justifies it."""
        v = LPJ.VALIDATION
        self.assertIn("Kim published end results", v["benchmark"])
        # the five-class gain over the shipped prompt is the reason this variant won
        self.assertGreater(
            v["five_class_accuracy_llm_pairs"],
            v["shipped_prompt_five_class_accuracy"],
        )
        # and the count match against Kim, which is the distribution test
        ours, kim = v["support_count_ours_vs_kim"]
        self.assertEqual(ours, kim)

    def test_the_accuracy_trap_is_recorded_not_just_avoided(self):
        """Three-class accuracy on a 90%-one-class set is nearly meaningless, and
        the shipped prompt scored +0.001 over guessing. If that is not written
        down, the next person selects on it again."""
        v = LPJ.VALIDATION
        self.assertLess(
            v["three_class_accuracy_all_pairs"] - v["all_engage_baseline"], 0.02
        )
        self.assertIn("near-useless", v["accuracy_caveat"])


def _interactions_fixture(
    directory, through_quarter=2, sweep="2026-10-07T11:57:13-04:00"
):
    """Smallest corpus that exercises every rendered field.

    ``through_quarter`` and ``sweep`` shape the partial-year inputs the way the producer
    writes them: only Q1..Q<through_quarter> of 2026 carry filings, and the sweep date is
    the corpus's own as-of date (the default is the real data's era: Q2's LD-2 deadline,
    Jul 20, has passed and Q3's, Oct 20, has not)."""
    data_root = os.path.join(directory, "derived")
    ledger_dir = os.path.join(data_root, "interactions")
    os.makedirs(ledger_dir, exist_ok=True)
    os.makedirs(os.path.join(data_root, "data", "engine"), exist_ok=True)

    member = "M000001"
    organization = "org-1"
    note_id = "note0000000000aa"

    _csv(
        os.path.join(data_root, "stance_priors", "dyad_signs.csv"),
        ["lob_id", "bioguide_id", "support_n", "oppose_n"],
        [
            {
                "lob_id": organization,
                "bioguide_id": member,
                "support_n": 1,
                "oppose_n": 3,
            }
        ],
    )

    _csv(
        os.path.join(data_root, "fullscope_org_roster.csv"),
        ["lob_id_or_client_id", "client_name", "source_flags",
         "first_year_seen", "last_year_seen"],
        [{
            "lob_id_or_client_id": organization,
            "client_name": "FIXTURE CREDIT UNION",
            "source_flags": "classified_finance",
            "first_year_seen": "",
            "last_year_seen": "",
        }],
    )
    _csv(
        os.path.join(data_root, "sector_spend", "sector_roster.csv"),
        # Keep this in sync with _load_sector_roster's required field tuple in
        # make_lobby_network_html.py; it drifted when bank-tier segregation made
        # bank_tiers required.
        ["sector", "canonical_family", "bank_tiers", "raw_name_variants",
         "normalized_name_variants", "matched_pattern", "client_ids",
         "collapse_rules", "first_year_seen", "last_year_seen"],
        [{
            "sector": "credit_union",
            "canonical_family": "FIXTURE CREDIT UNION",
            "bank_tiers": "",
            "raw_name_variants": "FIXTURE CREDIT UNION",
            "normalized_name_variants": "fixture credit union",
            "matched_pattern": "",
            "client_ids": organization,
            "collapse_rules": "",
            # Blank no longer parses: _load_sector_roster requires these as integers.
            # Same span the network fixture uses, and the full corpus coverage.
            "first_year_seen": 2021,
            "last_year_seen": 2026,
        }],
    )
    quarter_base = {
        "sector": "credit_union", "is_partial_year": "true",
        "distinct_organizations": 1, "income_total": 0, "expenses_total": 0,
        "distinct_registrants": 1, "distinct_named_lobbyists": 1,
        "top_1_income_share": 0, "top_3_income_share": 0, "top_10_income_share": 0,
        "income_hhi": 0,
    }
    spend_rows = [
        dict(quarter_base, year=year, period="annual", filings=2,
             is_partial_year=str(year == 2026).lower())
        for year in MLI.YEARS
    ] + [
        dict(quarter_base, year=2026, period=f"Q{number}",
             filings=1 if number <= through_quarter else 0)
        for number in (1, 2, 3, 4)
    ]
    _csv(
        os.path.join(data_root, "sector_spend", "sector_spend.csv"),
        ["sector", "year", "period", "is_partial_year", "distinct_organizations",
         "filings", "income_total", "expenses_total", "distinct_registrants",
         "distinct_named_lobbyists", "top_1_income_share", "top_3_income_share",
         "top_10_income_share", "income_hhi"],
        spend_rows,
    )
    with open(
        os.path.join(data_root, "sector_spend", "sector_spend.json"), "w", encoding="utf-8"
    ) as handle:
        json.dump({"selected_sectors": ["credit_union"]}, handle)
    with open(
        os.path.join(data_root, "fullscope_receipts.json"), "w", encoding="utf-8"
    ) as handle:
        json.dump({"scope_sweeps": {"2026": {"sweep_through_dt_posted": sweep}}}, handle)
    for year in MLI.YEARS:
        _csv(
            os.path.join(data_root, f"our_network_fullscope_{year}_strict.csv"),
            ["lob_id", "bioguide_id", "year", "n_bills"],
            [{
                "lob_id": organization,
                "bioguide_id": member,
                "year": year,
                "n_bills": 1 if year < 2024 else 4,
            }],
        )
    _csv(
        os.path.join(data_root, "champions_fullscope", "champions.csv"),
        ["lob_id", "org_name", "org_side", "covered_years", "controls_used",
         "bioguide_id", "member_name", "party", "chamber", "champion_rank",
         "champion_score", "sector_relative_score", "sector_rank",
         "raw_n_bills", "years_present", "direction_of_travel", "r2",
         "n_members_modeled"],
        [{
            "lob_id": organization,
            "org_name": "FIXTURE CREDIT UNION",
            "org_side": "credit_union",
            "covered_years": "2021|2026",
            "controls_used": "party",
            "bioguide_id": member,
            "member_name": "Fixture Member",
            "party": "Independent",
            "chamber": "House",
            "champion_rank": 1,
            "champion_score": 1.5,
            "sector_relative_score": 0.5,
            "sector_rank": 1,
            "raw_n_bills": 10,
            "years_present": 6,
            "direction_of_travel": "rising",
            "r2": 0.4,
            "n_members_modeled": 5,
        }],
    )
    _csv(
        os.path.join(data_root, "bills_audit.csv"),
        ["section", "bill", "title", "n_cosponsors", "congresses", "flagged"],
        [{
            "section": "Fixture",
            "bill": "H.R.1181",
            "title": "Fixture Interchange Act",
            "n_cosponsors": 3,
            "congresses": "117",
            "flagged": "",
        }],
    )
    # 2026-08-06: titles now come from the shared BILLSTATUS-backed cache
    # (industry family split); the fixture supplies the cache directly.
    os.makedirs(os.path.join(data_root, "bill_titles"), exist_ok=True)
    with open(
        os.path.join(data_root, "bill_titles", "titles.json"), "w", encoding="utf-8"
    ) as handle:
        json.dump(
            {
                "schema": "bill-titles-v1",
                "titles": {"117:hr:1181": "Fixture Interchange Act"},
            },
            handle,
        )
    # Two years, so the year grouping has something to group.
    _csv(
        os.path.join(ledger_dir, "interaction_ledger.csv"),
        LI.LEDGER_FIELDS,
        [
            {
                "filing_year": 2021,
                "filing_uuid": "11111111-1111-4111-8111-111111111111",
                "activity_index": 0,
                "dt_posted": "2021-04-20T10:00:00-04:00",
                "filing_date": "2021-04-20",
                "filing_period": "first_quarter",
                "lob_id": organization,
                "client_name": "FIXTURE CREDIT UNION",
                "registrant_name": "FIXTURE GOVERNMENT AFFAIRS LLC",
                "lobbyists": "JANE DOE|JOHN ROE",
                "n_lobbyists": 2,
                "covered_positions": "Legislative Director, US House",
                "general_issue_code": "FIN",
                "general_issue_display": "Financial Institutions",
                "government_entities": "HOUSE OF REPRESENTATIVES|SENATE",
                "bill_key": "117:hr:1181",
                "bill_display": "H.R.1181",
                "channel": "formal_strict",
                "note_id": note_id,
                "position": "",
                "position_basis": LI.POSITION_NOT_DISCLOSED,
            },
            {
                "filing_year": 2025,
                "filing_uuid": "22222222-2222-4222-8222-222222222222",
                "activity_index": 1,
                "dt_posted": "2025-07-15T09:30:00-04:00",
                "filing_date": "2025-07-15",
                "filing_period": "third_quarter",
                "lob_id": organization,
                "client_name": "FIXTURE CREDIT UNION",
                "registrant_name": "FIXTURE GOVERNMENT AFFAIRS LLC",
                "lobbyists": "JANE DOE",
                "n_lobbyists": 1,
                "covered_positions": "",
                "general_issue_code": "BAN",
                "general_issue_display": "Banking",
                "government_entities": "SENATE",
                "bill_key": "117:hr:1181",
                "bill_display": "H.R.1181",
                "channel": "title_strict",
                "note_id": note_id,
                "position": "",
                "position_basis": LI.POSITION_NOT_DISCLOSED,
            },
        ],
    )
    _csv(
        os.path.join(ledger_dir, "interaction_notes.csv"),
        LI.NOTE_FIELDS,
        [{
            "note_id": note_id,
            "n_activities": 2,
            "n_characters": 63,
            "description": "Interchange fee legislation; H.R.1181 and related matters.",
        }],
    )
    _csv(
        os.path.join(ledger_dir, "interaction_bill_members.csv"),
        LI.BILL_MEMBER_FIELDS,
        [{
            "bill_key": "117:hr:1181",
            "bioguide_id": member,
            "role": "sponsor",
            "sponsorship_date": "2021-02-18",
        }],
    )
    with open(
        os.path.join(ledger_dir, "interaction_receipts.json"), "w", encoding="utf-8"
    ) as handle:
        json.dump({"ledger_rows": 2}, handle)

    # identity sources
    with open(
        os.path.join(data_root, "data", "engine", "congestion.json"), "w", encoding="utf-8"
    ) as handle:
        json.dump([{"member": "Fixture Member", "bioguide": member}], handle)
    _csv(
        os.path.join(data_root, "legislators.csv"),
        ["bioguide_id", "full_name", "party", "type"],
        [{
            "bioguide_id": member,
            "full_name": "Fixture Member",
            "party": "Independent",
            "type": "rep",
        }],
    )
    _csv(
        os.path.join(data_root, "HSall_members.csv"),
        ["congress", "chamber", "icpsr", "state_abbrev", "party_code",
         "bioname", "bioguide_id"],
        [{
            "congress": 117,
            "chamber": "House",
            "icpsr": 20301,
            "state_abbrev": "NC",
            "party_code": 328,
            "bioname": "FIXTURE, Member",
            "bioguide_id": member,
        }],
    )
    return data_root, ledger_dir


def _build_interactions(directory, **fixture):
    data_root, ledger_dir = _interactions_fixture(directory, **fixture)
    output = os.path.join(directory, "lobby-interactions.html")
    document = MLI.build(
        data_root,
        output,
        ledger_dir=ledger_dir,
        bills_audit_path=os.path.join(data_root, "bills_audit.csv"),
        congestion_path=os.path.join(data_root, "data", "engine", "congestion.json"),
        legislators_path=os.path.join(data_root, "legislators.csv"),
        voteview_path=os.path.join(data_root, "HSall_members.csv"),
    )
    return document, data_root


class InteractionsPage(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.document, self.data_root = _build_interactions(self._directory.name)
        payload = self.document.split(
            "<script id=page-data type=application/json>", 1
        )[1].split("</script>", 1)[0]
        self.payload = json.loads(payload)

    def tearDown(self):
        self._directory.cleanup()

    def test_it_is_self_contained(self):
        # Mentat favicon allowance (2026-08-06 retheme): the SVG xmlns inside
        # the data:-URI icon is a namespace identifier, not a network fetch.
        self.assertEqual(
            self.document.count("http://"),
            self.document.count("http://www.w3.org/2000/svg"),
        )
        self.assertNotIn("https://", self.document)
        self.assertIn("<script", self.document)
        self.assertIn("<style>", self.document)

    def test_every_surface_is_reachable_and_this_one_is_marked_current(self):
        header = self.document.split("<header class=product-header>", 1)[1].split(
            "</header>", 1
        )[0]
        self.assertIn('href="Moneyball Dashboard.html"', header)
        self.assertIn('href="cu-member-desk.html"', header)
        self.assertIn('href="lobby-network.html"', header)
        self.assertIn("Main dashboard", header)
        self.assertIn("aria-current=page", header)
        self.assertIn("Melange — Industries", header)
        self.assertIn('aria-label="Melange surfaces"', header)
        self.assertIn(
            '<span class=tabicon aria-hidden=true>◧</span>Main dashboard', header
        )
        self.assertEqual(header.count("<svg class=brand-glyph"), 1)
        self.assertNotIn("Moneyball · Industry intelligence", header)
        self.assertNotIn('aria-label="Moneyball surfaces"', header)
        # four surfaces: main dashboard, member desk, industry, and this one
        self.assertEqual(header.count("<a class=tab"), 4)
        self.assertEqual(header.count("aria-current=page"), 1)

    def test_the_drill_down_carries_every_field_that_was_asked_for(self):
        act = self.payload["acts"][0]
        self.assertEqual(act[1], "2021-04-20")                       # date
        self.assertIn(
            "FIXTURE GOVERNMENT AFFAIRS LLC", self.payload["regs"]
        )                                                            # who filed
        self.assertIn("JANE DOE", self.payload["lobs"])               # the lobbyist
        self.assertEqual(self.payload["bills"][act[6]]["d"], "H.R.1181")  # the bill
        self.assertIn(
            "Interchange fee legislation", self.payload["notes"][act[7]]
        )                                                            # the notes
        # position: wired, empty, and the basis travels with it
        position_text, position_basis = self.payload["positions"][act[12]]
        self.assertEqual(position_text, "")
        self.assertEqual(position_basis, LI.POSITION_NOT_DISCLOSED)

    def test_position_reads_not_disclosed_and_the_page_says_why(self):
        self.assertIn("Not disclosed", self.document)
        # Bind to the full methodology sentence; shorter fragments also occur
        # in the cell tooltip and would not prove the method note survived.
        self.assertIn(
            "The lobbying filing has no place to state a position. No filing in "
            "this corpus states whether the filer supported or opposed the bill",
            self.document,
        )
        self.assertIn("not_disclosed", self.document)
        # never claim a position was collected
        self.assertNotIn("position: Support", self.document)

    def test_interactions_are_grouped_by_year_newest_first(self):
        self.assertIn("yearblock", self.document)
        self.assertIn("byYear", self.document)
        self.assertIn("years=[...byYear.keys()].sort((a,b)=>b-a)", self.document)
        years = {act[0] for act in self.payload["acts"]}
        self.assertEqual(years, {2021, 2025})

    def test_bill_title_and_sponsorship_role_reach_the_page(self):
        bill = self.payload["bills"][0]
        self.assertEqual(bill["t"], "Fixture Interchange Act")
        # sponsor is 1, cosponsor is 0 -- the word ships once, not per row
        self.assertEqual(list(self.payload["memberRoles"].values())[0][0], 1)
        self.assertIn("Sponsor", self.document)

    def test_member_carries_a_posture_over_the_same_series(self):
        key = f"credit_union:0"
        self.assertIn(key, self.payload["sectorPosture"])
        verdict = self.payload["sectorPosture"][key]
        state = self.payload["postureStates"][verdict[0]]
        # 2024+2025 = 8 against 2022+2023 = 2 -> rising
        self.assertEqual(state["n"], "rising")
        self.assertEqual(state["p"], "Cultivating")
        self.assertEqual(state["t"], "Rising")

    def test_adversarial_org_dyad_renders_badge_note_and_receipt(self):
        verdict = self.payload["orgPosture"]["0:0"]
        state = self.payload["postureStates"][verdict[0]]
        self.assertEqual(state["n"], LP.ADVERSARIAL)
        self.assertEqual(state["p"], "Adversarial")
        self.assertEqual(state["t"], "Adversarial")
        self.assertIn("postbadge.adversarial{border-width:2px;border-style:double}", self.document)
        self.assertIn(html.escape(LP.POSTURE_RULE_NOTE), self.document)
        self.assertIn("signed-evidence override was available", self.document)
        self.assertIn("<code>dyad_signs.csv</code><span>SHA-256 ", self.document)
        override = self.payload["receipts"]["posture"]["sign_override"]
        self.assertEqual(override["dyads"], 1)
        self.assertEqual(override["signed_dyads"], 1)
        self.assertEqual(override["adversarial_dyads"], 1)
        self.assertEqual(override["corroboration_floor"], 2)

    def test_both_wordings_ship_so_the_toggle_cannot_change_the_bucket(self):
        self.assertIn("data-posture-mode=posture", self.document)
        self.assertIn("data-posture-mode=trend", self.document)
        for state in self.payload["postureStates"]:
            self.assertTrue(state["p"] and state["t"] and state["b"])

    def test_honesty_language_is_preserved_in_the_methodology_fold(self):
        for statement in (
            MLI.INTERACTION_NOTE,
            MLI.POSITION_NOTE,
            MLI.COVERAGE_NOTE,
            MLI.NOTE_AUTHORSHIP_NOTE,
        ):
            # rendered escaped, so assert on what actually lands in the document
            self.assertIn(html.escape(statement), self.document)

    def test_methodology_fold_keeps_interaction_warning_visible(self):
        visible = f"<div class=honesty><p>{html.escape(MLI.INTERACTION_NOTE)}</p></div>"
        self.assertIn(visible, self.document)
        self.assertIn("<details class=methnote>", self.document)
        self.assertIn(
            "<summary>How to read this (methodology)</summary>", self.document
        )
        self.assertIn(".methnote>summary", self.document)
        self.assertIn(".methbody{", self.document)
        visible_end = self.document.index(visible) + len(visible)
        details_start = self.document.index("<details class=methnote>", visible_end)
        self.assertEqual(visible_end, details_start)
        self.assertIn(
            html.escape(MLI.POSITION_NOTE),
            self.document[details_start:self.document.index("</details>", details_start)],
        )

    def test_receipts_account_for_every_ledger_row(self):
        receipts = self.payload["receipts"]
        self.assertEqual(
            receipts["ledger_rows"],
            receipts["rendered_rows"] + receipts["dropped_unsectored_rows"],
        )
        self.assertEqual(receipts["position_bases"], [LI.POSITION_NOT_DISCLOSED])

    def test_the_payload_is_byte_deterministic(self):
        """Same inputs, same bytes. The document itself carries the absolute input
        paths in its hash footer, so determinism is asserted on the payload, which
        is everything the page actually renders from."""
        with tempfile.TemporaryDirectory() as directory:
            again, _root = _build_interactions(directory)
        second = again.split("<script id=page-data type=application/json>", 1)[1].split(
            "</script>", 1
        )[0]
        first = self.document.split(
            "<script id=page-data type=application/json>", 1
        )[1].split("</script>", 1)[0]
        self.assertEqual(first, second)


class PartialYearQuarter(unittest.TestCase):
    """'partial year through Q<n>' is derived from the data, never typed into the page.

    The rule is the industry page's own (MLN._load_spend + _sweep_as_of +
    _page_through_quarter; the minimum-across-sectors case is pinned there in
    test_industry_series_not_summed): a quarter counts once its LD-2 deadline (Q1 Apr 20,
    Q2 Jul 20, Q3 Oct 20, Q4 Jan 20 of the next year) has passed by the producer's sweep
    date AND it has filings."""

    def build(self, **fixture):
        with tempfile.TemporaryDirectory() as directory:
            document, _root = _build_interactions(directory, **fixture)
        payload = document.split("<script id=page-data type=application/json>", 1)[1].split(
            "</script>", 1
        )[0]
        return document, json.loads(payload)

    def test_the_real_data_shape_still_reads_through_q2(self):
        document, payload = self.build()
        self.assertEqual("Q2", payload["throughQuarter"])
        self.assertIn("partial year through ${DATA.throughQuarter}", document)
        self.assertIsNone(re.search(r"through Q\d", document))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_year_header_renders_the_derived_quarter(self):
        document, payload = self.build()
        head = re.search(r"<h3>(\$\{year\}.*?)</h3>", document, re.S).group(1)
        script = (
            "const year=2026; const DATA=" + json.dumps(payload) + ";"
            + "process.stdout.write(`" + head + "`);"
        )
        run = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, timeout=60)
        self.assertEqual(
            ("2026 <span class=partial>partial year through Q2</span>", 0),
            (run.stdout.decode().strip(), run.returncode),
            run.stderr.decode()[-400:],
        )

    def test_the_quarter_follows_the_sweep_date_and_the_filings(self):
        for through, sweep, expected in (
            (3, "2026-10-21T09:00:00-04:00", "Q3"),  # Q3 deadline passed and Q3 filed
            (3, "2026-10-20T09:00:00-04:00", "Q3"),  # the deadline day itself counts
            (3, "2026-10-07T09:00:00-04:00", "Q2"),  # an early Q3 filing never extends coverage
            (2, "2026-07-01T09:00:00-04:00", "Q1"),  # Q2 filed but its deadline is ahead
        ):
            with self.subTest(through=through, sweep=sweep):
                _document, payload = self.build(through_quarter=through, sweep=sweep)
                self.assertEqual(expected, payload["throughQuarter"])

    def test_a_partial_year_with_no_covered_quarter_is_refused_not_guessed(self):
        with self.assertRaises(MLI.IngestError) as caught:
            self.build(through_quarter=0)  # no quarter row has any filing
        self.assertIn("quarter", str(caught.exception))
        with self.assertRaises(MLI.IngestError):
            self.build(sweep="2026-04-01T09:00:00-04:00")  # filings exist, no deadline has passed


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = len(result.failures) + len(result.errors)
    passed = result.testsRun - failed
    # This shape must match fec/tests/run_tests.py's regex so it can register this suite.
    print(f"test_lobby_interactions: {passed} passed, {failed} failed")
    sys.exit(0 if result.wasSuccessful() else 1)

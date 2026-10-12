# -*- coding: utf-8 -*-
"""Contracts for per-industry legislative valence adjudication."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from lobbying import industry_tiers as IT


def _row(**changes):
    row = {
        "bill": "H.R.1",
        "congress": "119",
        "industry": "crypto",
        "valence": "pro",
        "confidence": 0.8,
        "basis": "bill-framework",
        "evidence": "fixture",
        "rationale": "fixture rationale",
        "adjudicated_by": "fable",
        "adjudicated": "2026-08-10",
        "status": "active",
    }
    row.update(changes)
    return row


def _axis_row(**changes):
    row = {
        "bill": "H.R.1",
        "congress": "119",
        "industry": "crypto",
        "relevance": 0.15,
        "valence": -1.0,
        "confidence": 0.8,
        "basis": "operative-text",
        "rationale": "fixture rationale",
        "adjudicated_by": "fable",
        "adjudicated": "2026-08-10",
        "status": "active",
    }
    row.update(changes)
    return row


class ValenceLedgerTests(unittest.TestCase):
    REQUIRED = (
        "bill", "congress", "industry", "valence", "basis", "rationale",
        "adjudicated_by", "adjudicated", "status",
    )

    def _ledger(self, directory, rows=None, raw=None):
        path = os.path.join(directory, "valence.ndjson")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            if raw is not None:
                handle.write(raw)
            else:
                for row in rows or []:
                    handle.write(
                        json.dumps(row, ensure_ascii=True, separators=(",", ":"))
                        + "\n"
                    )
        return path

    def test_accepts_active_and_schema_validates_then_skips_inactive(self):
        tags = {
            "H.R.1|119": {"class": "crypto_primary"},
            "H.R.2|119": {"class": "crypto_primary"},
        }
        rows = [_row(), _row(bill="H.R.2", status="inactive")]
        with tempfile.TemporaryDirectory() as directory:
            loaded, total = IT._load_valence_ledger(
                self._ledger(directory, rows), tags
            )
        self.assertEqual(total, 1)
        self.assertEqual(set(loaded), set(IT.VALENCE_INDUSTRIES))
        self.assertEqual(set(loaded["crypto"]), {"H.R.1|119"})
        self.assertEqual(loaded["bank"], {})
        self.assertEqual(loaded["fintech"], {})
        self.assertEqual(loaded["telecom"], {})

    def test_rejects_each_missing_or_empty_required_field(self):
        tags = {"H.R.1|119": {"class": "crypto_primary"}}
        for field in self.REQUIRED:
            for value in (None, ""):
                with self.subTest(field=field, value=value):
                    row = _row()
                    row[field] = value
                    with tempfile.TemporaryDirectory() as directory:
                        path = self._ledger(directory, [row])
                        with self.assertRaisesRegex(
                            IT.IndustryTiersError, "must be non-empty"
                        ):
                            IT._load_valence_ledger(path, tags)

    def test_rejects_invalid_industry_including_credit_union(self):
        for industry in ("credit_union", "insurance"):
            with self.subTest(industry=industry):
                with tempfile.TemporaryDirectory() as directory:
                    path = self._ledger(directory, [_row(industry=industry)])
                    with self.assertRaisesRegex(
                        IT.IndustryTiersError, "industry must be one of"
                    ):
                        IT._load_valence_ledger(
                            path, {"H.R.1|119": {"class": "crypto_primary"}}
                        )

    def test_rejects_invalid_valence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._ledger(directory, [_row(valence="mixed")])
            with self.assertRaisesRegex(
                IT.IndustryTiersError, "valence must be one of"
            ):
                IT._load_valence_ledger(
                    path, {"H.R.1|119": {"class": "crypto_primary"}}
                )

    def test_rejects_invalid_confidence_matrix(self):
        invalid = (None, "", True, "high", 0, -0.1, 1.1, float("nan"))
        tags = {"H.R.1|119": {"class": "crypto_primary"}}
        for confidence in invalid:
            with self.subTest(confidence=confidence):
                with tempfile.TemporaryDirectory() as directory:
                    path = self._ledger(
                        directory, [_row(confidence=confidence)]
                    )
                    with self.assertRaises(IT.IndustryTiersError):
                        IT._load_valence_ledger(path, tags)

    def test_rejects_missing_tag_key_and_primary_class_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._ledger(directory, [_row()])
            with self.assertRaisesRegex(
                IT.IndustryTiersError, "is not in beneficiary tags"
            ):
                IT._load_valence_ledger(path, {})
            with self.assertRaisesRegex(
                IT.IndustryTiersError, "class must be crypto_primary"
            ):
                IT._load_valence_ledger(
                    path, {"H.R.1|119": {"class": "bank_primary"}}
                )

    def test_accepts_oppose_class_for_bank_and_relabels_to_bank_anti(self):
        tags = {"H.R.1|119": {"class": "oppose", "title": "fixture"}}
        row = _row(industry="bank", valence="anti")
        with tempfile.TemporaryDirectory() as directory:
            loaded, total = IT._load_valence_ledger(
                self._ledger(directory, [row]), tags
            )
        self.assertEqual(total, 1)
        self.assertEqual(set(loaded["bank"]), {"H.R.1|119"})

        view, counts, cell_weights = IT._industry_tags_view(
            tags, loaded["bank"], {}, "bank"
        )
        self.assertEqual(view["H.R.1|119"]["class"], "bank_anti")
        self.assertEqual(
            counts,
            {"anti": 1, "neutral": 0, "pro": 0, "cells": 0},
        )
        self.assertEqual(cell_weights, {})

    def test_oppose_class_still_rejects_credit_union_rows(self):
        tags = {"H.R.1|119": {"class": "oppose"}}
        row = _row(
            industry="credit_union",
            valence="anti",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._ledger(directory, [row])
            with self.assertRaisesRegex(
                IT.IndustryTiersError,
                "industry must be one of",
            ):
                IT._load_valence_ledger(path, tags)

    def test_rejects_duplicate_tuple(self):
        tags = {"H.R.1|119": {"class": "crypto_primary"}}
        with tempfile.TemporaryDirectory() as directory:
            path = self._ledger(directory, [_row(), _row()])
            with self.assertRaisesRegex(
                IT.IndustryTiersError, "duplicate valence ledger row"
            ):
                IT._load_valence_ledger(path, tags)

    def test_inactive_rows_still_fail_schema_and_join_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._ledger(
                directory, [_row(status="inactive", confidence=0)]
            )
            with self.assertRaisesRegex(
                IT.IndustryTiersError, "confidence must be in"
            ):
                IT._load_valence_ledger(
                    path, {"H.R.1|119": {"class": "crypto_primary"}}
                )
            path = self._ledger(
                directory, [_row(status="inactive", bill="H.R.404")]
            )
            with self.assertRaisesRegex(
                IT.IndustryTiersError, "is not in beneficiary tags"
            ):
                IT._load_valence_ledger(path, {})

    def test_rejects_unknown_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._ledger(directory, [_row(status="actve")])
            with self.assertRaisesRegex(
                IT.IndustryTiersError,
                "status must be active or inactive; got 'actve'",
            ):
                IT._load_valence_ledger(
                    path, {"H.R.1|119": {"class": "crypto_primary"}}
                )

    def test_requires_nonempty_evidence(self):
        for value in (None, ""):
            with self.subTest(value=value):
                row = _row()
                if value is None:
                    row.pop("evidence")
                else:
                    row["evidence"] = value
                with tempfile.TemporaryDirectory() as directory:
                    path = self._ledger(directory, [row])
                    with self.assertRaisesRegex(
                        IT.IndustryTiersError,
                        "field evidence must be non-empty",
                    ):
                        IT._load_valence_ledger(
                            path,
                            {"H.R.1|119": {"class": "crypto_primary"}},
                        )

    def test_rejects_signed_letters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._ledger(directory, [_row(bill="LTR-119-TEST")])
            with self.assertRaisesRegex(
                IT.IndustryTiersError,
                r"signed-letter valence is undefined; letters score "
                r"LETTER_W and collapse to mix\['letter'\]",
            ):
                IT._load_valence_ledger(path, {})

    def test_rejects_pipe_in_bill_or_congress(self):
        for field, value in (("bill", "H.R.|1"), ("congress", "119|120")):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as directory:
                    path = self._ledger(directory, [_row(**{field: value})])
                    with self.assertRaisesRegex(
                        IT.IndustryTiersError,
                        r"bill/congress may not contain '\|'",
                    ):
                        IT._load_valence_ledger(path, {})

    def test_rejects_malformed_json_and_non_object(self):
        cases = (("{not json}\n", "invalid valence ledger JSON"),
                 ("[]\n", "must be an object"))
        for raw, message in cases:
            with self.subTest(raw=raw):
                with tempfile.TemporaryDirectory() as directory:
                    path = self._ledger(directory, raw=raw)
                    with self.assertRaisesRegex(IT.IndustryTiersError, message):
                        IT._load_valence_ledger(path, {})


class IndustryAxesTests(unittest.TestCase):
    def _axes(self, directory, rows=None, raw=None):
        path = os.path.join(directory, "axes.ndjson")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            if raw is not None:
                handle.write(raw)
            else:
                for row in rows or []:
                    handle.write(
                        json.dumps(
                            row, ensure_ascii=True, separators=(",", ":")
                        )
                        + "\n"
                    )
        return path

    def test_empty_tensor_matches_previous_valence_view(self):
        tags = {
            "A|119": {"class": "crypto_primary", "title": "anti"},
            "N|119": {"class": "crypto_primary", "title": "neutral"},
            "P|119": {"class": "crypto_primary", "title": "pro"},
        }
        original = copy.deepcopy(tags)
        expected = copy.deepcopy(tags)
        expected["A|119"]["class"] = "crypto_anti"
        expected["N|119"]["class"] = "crypto_neutral"
        rows = {
            "A|119": {"valence": "anti"},
            "N|119": {"valence": "neutral"},
            "P|119": {"valence": "pro"},
        }

        view, counts, cell_weights = IT._industry_tags_view(
            tags, rows, {}, "crypto"
        )

        self.assertEqual(view, expected)
        self.assertEqual(
            counts,
            {"anti": 1, "neutral": 1, "pro": 1, "cells": 0},
        )
        self.assertEqual(cell_weights, {})
        self.assertFalse(
            any("_cell_" in value["class"] for value in view.values())
        )
        self.assertEqual(tags, original)
        for key in tags:
            self.assertIsNot(view[key], tags[key])

    def test_cell_relabels_with_stable_index_and_scoped_weight(self):
        tags = {
            "Z|119": {"class": "crypto_primary"},
            "A|119": {"class": "bank_primary"},
        }
        axes_rows = {
            "Z|119": {"relevance": 0.5, "valence": 0.5},
            "A|119": {"relevance": 0.15, "valence": -1.0},
        }

        view, counts, cell_weights = IT._industry_tags_view(
            tags, {}, axes_rows, "crypto"
        )

        self.assertEqual(view["A|119"]["class"], "crypto_cell_1")
        self.assertEqual(view["Z|119"]["class"], "crypto_cell_2")
        self.assertEqual(cell_weights["crypto_cell_1"], -0.15)
        self.assertEqual(cell_weights["crypto_cell_2"], 0.25)
        self.assertEqual(counts["cells"], 2)

    def test_rejects_one_owner_conflict(self):
        tags = {"H.R.1|119": {"class": "crypto_primary"}}
        valence_rows = {"H.R.1|119": {"valence": "anti"}}
        axes_rows = {
            "H.R.1|119": {"relevance": 1.0, "valence": -1.0}
        }
        with self.assertRaisesRegex(
            IT.IndustryTiersError, "both own the cell"
        ):
            IT._industry_tags_view(
                tags, valence_rows, axes_rows, "crypto"
            )

    def test_accepts_credit_union_cell_without_class_restriction(self):
        tags = {
            "H.R.1|119": {
                "class": "offtopic",
                "acu_position": "SUPPORT",
            }
        }
        row = _axis_row(industry="credit_union")
        with tempfile.TemporaryDirectory() as directory:
            loaded, total = IT._load_industry_axes(
                self._axes(directory, [row]), tags
            )

        self.assertEqual(total, 1)
        self.assertEqual(set(loaded), set(IT.INDUSTRIES))
        self.assertEqual(set(loaded["credit_union"]), {"H.R.1|119"})

        view, counts, cell_weights = IT._industry_tags_view(
            tags, {}, loaded["credit_union"], "credit_union"
        )
        self.assertEqual(
            view["H.R.1|119"]["class"], "credit_union_cell_1"
        )
        self.assertNotIn("acu_position", view["H.R.1|119"])
        self.assertEqual(
            cell_weights, {"credit_union_cell_1": -0.15}
        )
        self.assertEqual(counts["cells"], 1)

    def test_rejects_signed_letters(self):
        row = _axis_row(bill="LTR-119-TEST")
        with tempfile.TemporaryDirectory() as directory:
            path = self._axes(directory, [row])
            with self.assertRaisesRegex(
                IT.IndustryTiersError, "signed-letter cells are undefined"
            ):
                IT._load_industry_axes(path, {})

    def test_rejects_duplicate_cell(self):
        tags = {"H.R.1|119": {"class": "offtopic"}}
        with tempfile.TemporaryDirectory() as directory:
            path = self._axes(
                directory, [_axis_row(), _axis_row()]
            )
            with self.assertRaisesRegex(
                IT.IndustryTiersError, "duplicate industry axes cell"
            ):
                IT._load_industry_axes(path, tags)

    def test_rejects_relevance_valence_and_confidence_ranges(self):
        cases = (
            ("relevance", -0.01, r"relevance must be in \[0, 1\]"),
            ("relevance", 1.01, r"relevance must be in \[0, 1\]"),
            ("valence", -1.01, r"valence must be in \[-1, 1\]"),
            ("valence", 1.01, r"valence must be in \[-1, 1\]"),
            ("confidence", 0.0, r"confidence must be in \(0, 1\]"),
            ("confidence", 1.01, r"confidence must be in \(0, 1\]"),
        )
        tags = {"H.R.1|119": {"class": "offtopic"}}
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    path = self._axes(
                        directory, [_axis_row(**{field: value})]
                    )
                    with self.assertRaisesRegex(
                        IT.IndustryTiersError, message
                    ):
                        IT._load_industry_axes(path, tags)

    def test_malformed_json_reports_line_number(self):
        valid = json.dumps(
            _axis_row(status="inactive"),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        raw = valid + "\n{not json}\n"
        tags = {"H.R.1|119": {"class": "offtopic"}}
        with tempfile.TemporaryDirectory() as directory:
            path = self._axes(directory, raw=raw)
            with self.assertRaisesRegex(
                IT.IndustryTiersError,
                "invalid industry axes JSON at line 2",
            ):
                IT._load_industry_axes(path, tags)

    def test_missing_file_raises_industry_tiers_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "missing.ndjson")
            with self.assertRaisesRegex(
                IT.IndustryTiersError, "cannot read industry axes"
            ):
                IT._load_industry_axes(path, {})


class ScopedActWeightsTests(unittest.TestCase):
    def _capture_build_map(
        self, industry, tags, valence_rows, axes_rows=None,
    ):
        captured = {}
        axes_rows = axes_rows or {}
        primary_class = IT.PRIMARY_CLASS[industry]
        anti_class, _neutral_class = IT._valence_classes(industry)
        generalized = {
            "Example Member": {
                "factor_ranked": 1.0,
                "mix": {primary_class: 1.0, anti_class: 1.0},
            }
        }

        def fake_compute(_bundle, _view, act_weights, acu_relevance,
                         include_adjudicated=True, uncertainty_k=None):
            self.assertEqual(uncertainty_k, None if industry == 'credit_union' else .25)
            captured.update(act_weights)
            captured["__acu_relevance__"] = acu_relevance
            captured["__include_adjudicated__"] = include_adjudicated
            return generalized, 0.0

        with (
            mock.patch.object(IT, "_compute_generalized", side_effect=fake_compute),
            mock.patch.object(
                IT.tier_util, "primacy_scores",
                return_value={"Example Member": 1.0},
            ),
            mock.patch.object(
                IT.tier_util, "apply_tier_overrides",
                return_value=["Example Member"],
            ),
            mock.patch.object(
                IT.tier_util, "apply_conduct_ladder",
                return_value=(["Example Member"], {}),
            ),
            mock.patch.object(IT, "_floor_minimum", return_value=0.0),
        ):
            IT._build_industry(
                industry=industry,
                bundle={},
                records=[{
                    "name": "Example Member",
                    "bioguide": "E000001",
                    "chamber": "House",
                }],
                member_names=["Example Member"],
                pcts={},
                tags=tags,
                valence_rows=valence_rows,
                axes_rows=axes_rows,
            )
        return captured

    def test_credit_union_cell_weight_is_scoped(self):
        scoped = self._capture_build_map(
            "credit_union",
            {"A|119": {"class": "cu_primary"}},
            {},
            {"A|119": {"relevance": 0.15, "valence": -1.0}},
        )
        self.assertEqual(scoped["credit_union_cell_1"], -0.15)
        self.assertIs(scoped["__acu_relevance__"], True)

    def test_synthetic_classes_are_scoped_per_industry(self):
        original = dict(IT.primacy_factors.ACT_W)
        crypto = self._capture_build_map(
            "crypto",
            {"A|119": {"class": "crypto_primary"}},
            {"A|119": {"valence": "anti"}},
        )
        bank = self._capture_build_map(
            "bank",
            {"B|119": {"class": "bank_primary"}},
            {},
        )
        self.assertEqual(crypto["crypto_anti"], -1.0)
        self.assertEqual(crypto["crypto_neutral"], 0.0)
        self.assertIs(crypto["__acu_relevance__"], False)
        self.assertNotIn("crypto_anti", bank)
        self.assertNotIn("crypto_neutral", bank)
        self.assertEqual(bank["bank_anti"], -1.0)
        self.assertEqual(bank["bank_neutral"], 0.0)
        self.assertEqual(IT.primacy_factors.ACT_W, original)

    def test_scoped_context_restores_the_original_global_map(self):
        original = IT.primacy_factors.ACT_W
        scoped = dict(original)
        scoped.update({"crypto_anti": -1.0, "crypto_neutral": 0.0})
        with IT._scoped_act_weights(scoped, acu_relevance=False):
            self.assertEqual(IT.primacy_factors.ACT_W["crypto_anti"], -1.0)
            self.assertEqual(IT.primacy_factors.ACT_W["crypto_neutral"], 0.0)
            self.assertIs(IT.primacy_factors.ACU_RELEVANCE_ACTIVE, False)
        self.assertIs(IT.primacy_factors.ACT_W, original)
        self.assertIs(IT.primacy_factors.ACU_RELEVANCE_ACTIVE, True)


class IndustryEvidencePriorityTests(unittest.TestCase):
    def test_qualified_priority_ends_at_total_tier_capacity(self):
        order = ["u0", "q1", "u2", "q3", "u4", "q5", "q6"]
        credits = {member: 1.0 if member.startswith("q") else 0.0
                   for member in order}
        actual = IT._prioritize_non_cu_evidence(order, credits, .75, (1, 1, 1))
        self.assertEqual(actual, ["q1", "q3", "q5", "u0", "u2", "u4", "q6"])

    def test_insufficient_qualifiers_preserve_remaining_score_order(self):
        order = ["u0", "q1", "u2", "q3", "u4"]
        credits = {"q1": .75, "q3": 1.0}
        self.assertEqual(
            IT._prioritize_non_cu_evidence(order, credits, .75, (1, 2, 1)),
            ["q1", "q3", "u0", "u2", "u4"],
        )
        self.assertEqual(
            IT._prioritize_non_cu_evidence(order, credits, 0, (1, 2, 1)),
            order,
        )

    def test_matches_historical_slot_walk_for_every_small_qualification_set(self):
        # Frozen old behavior is the compatibility oracle, including its
        # insufficient-qualifier append. The CU helper no longer permits that.
        for size in range(9):
            order = list(range(size))
            for mask in range(1 << size):
                credits = {member: float(bool(mask & (1 << member)))
                           for member in order}
                remaining, historical = list(order), []
                for slot in range(size):
                    for index, member in enumerate(remaining):
                        if credits[member] >= .75 or slot >= 4:
                            historical.append(remaining.pop(index))
                            break
                    else:
                        break
                historical.extend(remaining)
                with self.subTest(size=size, mask=mask):
                    self.assertEqual(
                        IT._prioritize_non_cu_evidence(
                            order, credits, .75, (1, 2, 1)),
                        historical,
                    )

    def _build_with_qualifiers(self, industry, qualifier_count):
        names = ["Member %03d" % index for index in range(100)]
        records = [{"name": member, "bioguide": "X%06d" % index,
                    "chamber": "House"} for index, member in enumerate(names)]
        qualified = set(names[-qualifier_count:]) if qualifier_count else set()
        generalized = {
            member: {"factor_ranked": 1.0 - index / 200.0,
                     "mix": {IT.PRIMARY_CLASS[industry]:
                             .75 if member in qualified else 0.0}}
            for index, member in enumerate(names)
        }
        with (
            mock.patch.object(IT, "_compute_generalized",
                              return_value=(generalized, .5)),
            mock.patch.object(IT, "_floor_minimum", return_value=.75),
            mock.patch.dict(os.environ, {"INDUSTRY_TIERS_T1_FLOOR": "1"}),
        ):
            result = IT._build_industry(
                industry, {}, records, names,
                {member: {"leg": .5} for member in names}, {}, {}, {},
            )
        return names, qualified, result

    def test_sparse_non_cu_boards_keep_rows_but_no_unqualified_tier_one(self):
        for industry, count in [("bank", 23), ("crypto", 40), ("fintech", 6), ("telecom", 6)]:
            with self.subTest(industry=industry):
                names, qualified, result = self._build_with_qualifiers(industry, count)
                rows = result["rows"]
                self.assertEqual([row["member"] for row in rows],
                                 names[-count:] + names[:-count])
                self.assertEqual([row["tierrank"] for row in rows], list(range(1, 101)))
                self.assertEqual(sum(row["tier"] == 1 for row in rows), min(count, 13))
                self.assertTrue(all(row["floor_qualified"]
                                    for row in rows if row["tier"] == 1))
                self.assertEqual(result["receipts"]["floor_qualified_count"], count)
                for row in rows:
                    self.assertEqual(row["floor_qualified"], row["member"] in qualified)
                if industry in ("fintech", "telecom"):
                    self.assertTrue(all(row["tier"] == 2 for row in rows[6:13]))
                    self.assertIn("demoted 7 unqualified slot-holders",
                                  result["receipts"]["tier1_floor"])

    def test_telecom_with_zero_telecom_primary_acts_builds_an_empty_board_without_error(self):
        # 2026-10-07: telecom ships before any telecom_primary tag or valence row
        # exists. The board must still build (every member ranked, nobody
        # qualified for tier 1) rather than crash or invent a tier-1 seat.
        names, _qualified, result = self._build_with_qualifiers("telecom", 0)
        rows = result["rows"]
        self.assertEqual(len(rows), len(names))
        self.assertEqual(sum(row["tier"] == 1 for row in rows), 0)
        self.assertFalse(any(row["floor_qualified"] for row in rows))
        self.assertEqual(result["receipts"]["floor_qualified_count"], 0)

    def test_telecom_zero_valence_rows_leave_tags_and_weights_untouched(self):
        tags = {"H.R.1|119": {"class": "shared"}, "H.R.2|119": {"class": "offtopic"}}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "valence.ndjson")
            open(path, "w", encoding="utf-8").close()
            loaded, total = IT._load_valence_ledger(path, tags)
        self.assertEqual(total, 0)
        self.assertEqual(loaded["telecom"], {})
        view, counts, cell_weights = IT._industry_tags_view(tags, {}, {}, "telecom")
        self.assertEqual(view, tags)
        self.assertEqual(counts, {"anti": 0, "neutral": 0, "pro": 0, "cells": 0})
        self.assertEqual(cell_weights, {})
        weights = IT._act_weight_map("telecom")
        self.assertEqual(weights["telecom_primary"], 1.0)
        self.assertEqual(IT._act_weight_map("fintech")["telecom_primary"], 0.15)
        self.assertEqual(IT.PRIMARY_CLASS["telecom"], "telecom_primary")
        self.assertEqual(IT.primacy_factors.ACT_W["telecom_primary"], 0.15)

    def test_telecom_valence_rows_require_a_telecom_primary_or_oppose_tag(self):
        row = _row(industry="telecom", bill="H.R.9", rationale="fixture")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "valence.ndjson")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            loaded, _total = IT._load_valence_ledger(
                path, {"H.R.9|119": {"class": "telecom_primary"}}
            )
            self.assertEqual(set(loaded["telecom"]), {"H.R.9|119"})
            with self.assertRaisesRegex(IT.IndustryTiersError, "class must be telecom_primary or oppose"):
                IT._load_valence_ledger(path, {"H.R.9|119": {"class": "shared"}})

    def test_private_cu_builder_retains_strict_qualification(self):
        with self.assertRaisesRegex(ValueError, "Infeasible tier seating"):
            self._build_with_qualifiers("credit_union", 6)
        _names, _qualified, result = self._build_with_qualifiers("credit_union", 85)
        self.assertEqual(len(result["rows"]), 100)
        self.assertTrue(all(row["floor_qualified"]
                            for row in result["rows"] if row["tier"] in (1, 2, 3)))


class TierOneFloorTests(unittest.TestCase):
    def test_demotes_only_unqualified_non_cu_tier_one_members(self):
        tiers = {"qualified": 1, "unqualified": 1, "tier_two": 2}
        floor_qualified = {
            "qualified": True, "unqualified": False, "tier_two": False,
        }
        with mock.patch.dict(
            os.environ, {"INDUSTRY_TIERS_T1_FLOOR": "1"}
        ):
            demotions = IT._apply_tier1_floor(
                tiers, floor_qualified, "bank"
            )
        self.assertEqual(demotions, 1)
        self.assertEqual(
            tiers,
            {"qualified": 1, "unqualified": 2, "tier_two": 2},
        )

    def test_environment_zero_reverts(self):
        tiers = {"unqualified": 1}
        with mock.patch.dict(
            os.environ, {"INDUSTRY_TIERS_T1_FLOOR": "0"}
        ):
            demotions = IT._apply_tier1_floor(
                tiers, {"unqualified": False}, "bank"
            )
        self.assertEqual(demotions, 0)
        self.assertEqual(tiers, {"unqualified": 1})

    def test_credit_union_is_uniform_under_the_tier1_floor(self):
        tiers = {"unqualified": 1, "qualified": 1}
        with mock.patch.dict(
            os.environ, {"INDUSTRY_TIERS_T1_FLOOR": "1"}
        ):
            demotions = IT._apply_tier1_floor(
                tiers,
                {"unqualified": False, "qualified": True},
                "credit_union",
            )
        self.assertEqual(demotions, 1)
        self.assertEqual(tiers, {"unqualified": 2, "qualified": 1})


class MethodPolicyTests(unittest.TestCase):
    def artifact(self, uncertainty):
        pf = IT.primacy_factors
        return {'_constants': {
            'role_weights': pf.ROLE_W, 'act_weights': pf.ACT_W,
            'acu_position_relevance': pf.ACU_RELEVANCE,
            'letter_weight': .6, 'uncertainty_penalty_k': uncertainty,
            'amendment_measure_weight': .5, 'shrink_anchor': 15.,
            'measure_weight_policy': pf.MK.measure_weight_policy(),
            'letter_weight_overrides': {}, 'classified_act_count': 0,
        }}

    def test_selected_method_accepts_only_its_ratified_penalty(self):
        for diagnostic, expected in [(True, 0.), (False, .25)]:
            with self.subTest(diagnostic=diagnostic), mock.patch.object(IT.primacy_factors, 'DIAGNOSTIC', diagnostic):
                with mock.patch.object(IT.primacy_factors, 'UNCERT_K', expected):
                    IT._validate_constants(self.artifact(expected), {})
                    with self.assertRaises(IT.IndustryTiersError):
                        IT._validate_constants(self.artifact(.25 if diagnostic else 0.), {})
                with mock.patch.object(IT.primacy_factors, 'UNCERT_K', .9):
                    with self.assertRaisesRegex(IT.IndustryTiersError, 'pinned uncertainty'):
                        IT._validate_constants(self.artifact(.9), {})

    def test_scoped_non_cu_penalty_restores_even_on_error(self):
        with mock.patch.object(IT.primacy_factors, 'UNCERT_K', 0.):
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with IT._scoped_act_weights(IT.primacy_factors.ACT_W, False, .25):
                    self.assertEqual(IT.primacy_factors.UNCERT_K, .25)
                    raise RuntimeError('fixture')
            self.assertEqual(IT.primacy_factors.UNCERT_K, 0.)


if __name__ == "__main__":
    unittest.main(verbosity=2)

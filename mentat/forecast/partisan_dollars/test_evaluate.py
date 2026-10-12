"""Synthetic controls only. No actual giving file or frozen research artifact is read."""
import copy
import csv
import importlib.util
import io
import math
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("dollar_evaluate", Path(__file__).with_name("evaluate.py"))
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def synthetic_rows(constant=False):
    rows = []
    for ci, chamber in enumerate(ev.CHAMBERS):
        for si, sector in enumerate(ev.INDUSTRIES):
            for yi, year in enumerate(ev.YEARS):
                d = (1000 + 100 * si) * (ci + 1) * (1 if constant else 1.1 ** yi)
                r = (800 + 100 * si) * (ci + 1) * (1 if constant else 0.9 ** yi)
                for party, amount in (("D", d), ("R", r)):
                    rows.append({"industry": sector, "opensecrets_code": ev.CODES[sector], "cycle": year,
                                 "chamber": chamber, "party": party, "actual_nominal_usd": amount,
                                 "two_party_nominal_usd": d + r})
    return rows


def reconciled(rows):
    for row in rows:
        row["two_party_nominal_usd"] = math.fsum(r["actual_nominal_usd"] for r in rows
            if (r["industry"], r["chamber"], r["cycle"]) == (row["industry"], row["chamber"], row["cycle"]))
    return rows


def scored_cells(actuals, predictions):
    rows, projected = [], []
    for i, (actual, prediction) in enumerate(zip(actuals, predictions)):
        rows.append({"chamber": "House", "industry": str(i), "cycle": 2020, "party": "D", "actual_nominal_usd": actual})
        projected.append({"chamber": "House", "industry": str(i), "target": 2020, "party": "D", "prediction_nominal_usd": prediction})
    return ev.score(projected, rows, "synthetic")


class FormulaTests(unittest.TestCase):
    def test_all_six_methods_full_grids_and_dollar_conservation(self):
        rows = synthetic_rows()
        before = copy.deepcopy(rows)
        for chamber in ev.CHAMBERS:
            for target in (2016, 2018, 2020, 2022, 2024, 2026):
                for family in ev.FAMILIES:
                    predicted = ev.predict(rows, chamber, target, family)
                    self.assertEqual(len(predicted), 8)
                    for sector in ev.INDUSTRIES:
                        pair = [r for r in predicted if r["industry"] == sector]
                        self.assertEqual({r["party"] for r in pair}, {"D", "R"})
                        self.assertAlmostEqual(math.fsum(r["prediction_nominal_usd"] for r in pair), pair[0]["projected_two_party_nominal_usd"])
                        self.assertLess(max(pair[0]["training_years"]), target)
        self.assertEqual(rows, before)

    def test_party_baselines_are_direct_dollars(self):
        rows = synthetic_rows()
        industry = ev.INDUSTRIES[0]
        source = {(r["cycle"], r["party"]): r["actual_nominal_usd"] for r in rows if r["industry"] == industry and r["chamber"] == "House"}
        for family in ev.FAMILIES[:3]:
            for prediction in ev.predict(rows, "House", 2020, family):
                if prediction["industry"] != industry:
                    continue
                previous, same = source[2018, prediction["party"]], source[2016, prediction["party"]]
                expected = previous if family == "prior_party" else same if family == "same_phase_party" else (previous + same) / 2
                self.assertEqual(prediction["prediction_nominal_usd"], expected)

    def test_last_three_log_changes_median_and_half_shrinkage(self):
        factor, trace = ev.half_growth({2012: 10, 2014: 20, 2016: 160, 2018: 80, 2020: 320})
        self.assertAlmostEqual(factor, 2)
        self.assertEqual([p["end_cycle"] for p in trace["transitions"]], [2016, 2018, 2020])
        self.assertEqual(trace["shrinkage"], 0.5)

    def test_one_early_transition_is_defined_and_no_extra_growth_clip(self):
        factor, trace = ev.half_growth({2012: 10, 2014: 1000})
        self.assertAlmostEqual(factor, 10)
        self.assertEqual(len(trace["transitions"]), 1)

    def test_direct_growth_and_joint_growth_are_different_estimands(self):
        rows = synthetic_rows()
        direct = ev.predict(rows, "House", 2020, "direct_party_half_growth")
        joint = ev.predict(rows, "House", 2020, "joint_total_half_growth_prior_share")
        self.assertNotAlmostEqual(direct[0]["prediction_nominal_usd"], joint[0]["prediction_nominal_usd"])
        self.assertIn("R_share_applied", joint[0]["trace"])
        self.assertNotIn("R_share_applied", direct[0]["trace"])

    def test_future_target_and_later_truth_cannot_change_predictions(self):
        rows = synthetic_rows()
        poisoned = [dict(r, actual_nominal_usd=1e9, two_party_nominal_usd=2e9) if r["cycle"] >= 2020 else dict(r) for r in rows]
        for family in ev.FAMILIES:
            self.assertEqual(ev.predict(rows, "House", 2020, family), ev.predict(poisoned, "House", 2020, family))

    def test_strict_phase_and_invalid_growth_fail_without_substitution(self):
        rows = synthetic_rows()
        with self.assertRaises(ev.ModelUnavailable):
            ev.predict(rows, "House", 2014, "same_phase_party")
        with self.assertRaisesRegex(ev.ModelUnavailable, "nonpositive"):
            ev.half_growth({2012: 100, 2014: 0, 2016: 100})
        with self.assertRaisesRegex(ev.ModelUnavailable, "no earlier"):
            ev.half_growth({2012: 100})


class MetricTests(unittest.TestCase):
    def test_actual_dollar_errors_zero_policy_and_bias(self):
        scored = scored_cells([100, 200, 0], [100, 150, 20])
        result = ev.metrics(scored)
        self.assertAlmostEqual(result["mae_nominal_usd"], 70 / 3)
        self.assertAlmostEqual(result["wape"], 70 / 300)
        self.assertAlmostEqual(result["signed_bias"], -30 / 300)
        self.assertEqual(result["positive_actual_count"], 2)
        self.assertEqual(result["within_10pct_rate"], 0.5)
        self.assertEqual(result["within_20pct_rate"], 0.5)
        self.assertEqual(result["zero_actual_false_positive_usd"], 20)
        self.assertIsNone(scored[-1]["ape"])
        self.assertIsNone(scored[-1]["within_20pct"])

    def test_band_boundaries_and_large_error_not_clipped(self):
        result = scored_cells([100, 100, 100, 100], [110, 120, 120.1, 500])
        self.assertTrue(result[0]["within_10pct"])
        self.assertTrue(result[1]["within_20pct"])
        self.assertFalse(result[2]["within_20pct"])
        self.assertEqual(result[3]["ape"], 4)

    def test_all_zero_no_automatic_success_or_percentage(self):
        result = ev.metrics(scored_cells([0, 0], [0, 15]))
        self.assertIsNone(result["wape"])
        self.assertIsNone(result["within_20pct_rate"])
        self.assertEqual(result["zero_actual_exact_count"], 1)
        self.assertEqual(result["zero_actual_false_positive_count"], 1)
        self.assertEqual(result["mae_nominal_usd"], 7.5)

    def test_macro_eight_strata_not_pooled_dollar_weights(self):
        rows = []
        for i, sector in enumerate(ev.INDUSTRIES):
            for party in ev.PARTIES:
                big = i == 0 and party == "D"
                actual = 1_000_000 if big else 100
                rows.append({"chamber": "House", "industry": sector, "cycle": 2020, "party": party, "actual_nominal_usd": actual})
        forecasts = [dict(chamber="House", industry=r["industry"], target=2020, party=r["party"], prediction_nominal_usd=r["actual_nominal_usd"] * (1.5 if i == 0 else 1)) for i, r in enumerate(rows)]
        scored = ev.score(forecasts, rows, "synthetic")
        macro, strata = ev.macro_wape(scored, [2020])
        self.assertAlmostEqual(macro, 0.5 / 8)
        self.assertGreater(ev.metrics(scored)["wape"], 0.49)
        self.assertEqual(len(strata), 8)
        with self.assertRaises(ev.ModelUnavailable):
            ev.macro_wape(scored[:-1], [2020])


class SelectionAndContractTests(unittest.TestCase):
    def test_minimum_origins_and_constant_tie_order(self):
        rows = synthetic_rows(constant=True)
        early = ev.select_family(rows, "House", 2018)
        self.assertTrue(early["fallback"])
        self.assertEqual(early["inner_origins"], [2016])
        chosen = ev.select_family(rows, "House", 2020)
        self.assertFalse(chosen["fallback"])
        self.assertEqual(chosen["selected_family"], "prior_party")
        self.assertLessEqual(max(chosen["macro_wape_by_family"].values()), ev.TOL)

    def test_selection_never_reads_outer_or_later_outcomes(self):
        rows = synthetic_rows()
        changed = [dict(r, actual_nominal_usd=1e10) if r["cycle"] >= 2020 else r for r in rows]
        self.assertEqual(ev.select_family(rows, "House", 2020), ev.select_family(changed, "House", 2020))

    def test_all_zero_stratum_remains_ineligible_not_dropped(self):
        rows = synthetic_rows()
        for r in rows:
            if r["industry"] == ev.INDUSTRIES[0] and r["party"] == "D":
                r["actual_nominal_usd"] = 0
        rows = reconciled(rows)
        selection = ev.select_family(rows, "House", 2020)
        self.assertTrue(selection["fallback"])
        self.assertFalse(selection["selection_eligible"])
        self.assertFalse(selection["macro_wape_by_family"])
        self.assertEqual(selection["ineligible_families"], list(ev.FAMILIES))
        self.assertTrue(selection["failures"])

    def test_history_requires_exact_scope_and_dollar_reconciliation(self):
        rows = synthetic_rows()
        ev.validate_rows(rows)
        for mutation in (lambda r: r.pop(), lambda r: r.append(dict(r[0])),
                         lambda r: r[0].update(actual_nominal_usd=-1),
                         lambda r: r[0].update(two_party_nominal_usd=999),
                         lambda r: r[0].update(industry="community_banks")):
            changed = copy.deepcopy(rows)
            mutation(changed)
            with self.assertRaises(ev.InputError):
                ev.validate_rows(changed)

    def test_complete_evaluation_records_all_cells_and_retrospective_status(self):
        result = ev.evaluate(synthetic_rows())
        self.assertEqual(len(result["predictions"]), 2 * 4 * 8 * 7)
        self.assertEqual(result["outer_failure_count"], 0)
        self.assertEqual(result["inner_failure_count"], 0)
        self.assertFalse(result["untouched_test_claimed"])
        self.assertFalse(result["accuracy_target_claimed"])
        for chamber in ev.CHAMBERS:
            self.assertEqual(result["final_2026_selection"][chamber]["inner_origins"], list(ev.INNER))

    def test_output_overwrite_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "result"
            result = ev.evaluate(synthetic_rows())
            ev.write_outputs(destination, result, "input", "code", "protocol")
            before = (destination / "evaluation.json").read_bytes()
            with self.assertRaises(FileExistsError):
                ev.write_outputs(destination, result, "changed", "code", "protocol")
            self.assertEqual((destination / "evaluation.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

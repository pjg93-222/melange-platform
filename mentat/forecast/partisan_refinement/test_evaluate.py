"""Synthetic controls only: no real bundle, source record, or held-out outcome is read."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

SPEC = importlib.util.spec_from_file_location("partisan_evaluate", Path(__file__).with_name("evaluate.py"))
ev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ev)


def row(year, share, forecast, sector="alpha", growth=None):
    return {"industry": sector, "cycle": year, "share": share, "forecast": forecast,
            "current_control": (year // 2) % 2, "growth_pct": year - 2010 if growth is None else growth,
            "prior_growth_pct": year - 2013, "pres": int(year % 4 == 0)}


def synthetic_bundle():
    histories = {}
    for chamber in ev.CHAMBERS:
        histories["control_" + chamber] = {"history": [
            row(year, 0.35 + 0.02 * si + 0.01 * (i % 3), 0.2 + 0.1 * (i % 4), sector,
                growth=1 + i * 2 + si * 0.4)
            for i, year in enumerate(ev.DEVELOPMENT_YEARS) for si, sector in enumerate(ev.INDUSTRIES)]}
    return {"share_models": histories}


class CandidateTests(unittest.TestCase):
    def test_three_anchor_families(self):
        history = [row(2012, 0.2, 0.1), row(2014, 0.6, 0.3)]
        targets = [row(2016, 0.99, 0.4)]
        expected = {"persistence": 0.6, "same_phase": 0.2, "equal_blend": 0.4}
        for family, value in expected.items():
            self.assertAlmostEqual(ev.candidate_predictions(history, targets, family)[0]["prediction"], value)

    def test_ridge_closed_form_and_mean_loss(self):
        history = [row(2012, 0.2, 0.1), row(2014, 0.6, 0.3)]
        fit = ev.ridge_fit(history, 1)
        self.assertAlmostEqual(fit["rms"], 0.2)
        self.assertAlmostEqual(fit["slope_scaled"], 0.2)
        self.assertAlmostEqual(fit["slope_raw"], 1)
        duplicate_sector = [dict(r, industry="beta") for r in history]
        self.assertAlmostEqual(ev.ridge_fit(history + duplicate_sector, 1)["slope_raw"], 1)
        self.assertAlmostEqual(ev.candidate_predictions(history, [row(2016, 0.8, 0.4)], "ridge_control_1")[0]["prediction"], 0.7)

    def test_zero_control_change_preserves_zero_origin(self):
        history = [row(2012, 0.2, 0.1), row(2014, 0.6, 0.1)]
        self.assertEqual(ev.ridge_fit(history, 10)["slope_raw"], 0)
        self.assertEqual(ev.candidate_predictions(history, [row(2016, 0.8, 0.9)], "ridge_control_10")[0]["prediction"], 0.6)

    def test_future_rows_and_target_truth_do_not_enter_fit(self):
        history = [row(2012, 0.2, 0.1), row(2014, 0.6, 0.3)]
        target = row(2016, 0.8, 0.4)
        expected = ev.candidate_predictions(history, [target], "ridge_control_1")
        target["share"] = float("nan")
        contaminated = history + [row(2016, 1, 0.99), row(2022, 0, 0)]
        self.assertEqual(ev.candidate_predictions(contaminated, [target], "ridge_control_1"), expected)
        self.assertEqual(expected[0]["fit"]["pair_end_years"], [2014])

    def test_clipping_logged_but_truth_and_error_not_clipped(self):
        history = [row(2012, 0.1, 0.1), row(2014, 0.9, 0.2)]
        targets = [row(2016, 0.05, 0.9)]
        raw = ev.candidate_predictions(history, targets, "ridge_control_1")
        self.assertGreater(raw[0]["raw_prediction"], 1)
        self.assertTrue(raw[0]["clipped"])
        scored = ev.score_predictions(raw, targets, "House", "synthetic")[0]
        self.assertEqual(scored["actual"], 0.05)
        self.assertAlmostEqual(scored["error_pp"], 95)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.histories = ev.extract_histories(synthetic_bundle())

    def test_one_inner_origin_forces_persistence(self):
        selected = ev.select_policy(self.histories["House"], "House", 2018)
        self.assertEqual(selected["common_inner_origins"], [2016])
        self.assertEqual(selected["selected_family"], "persistence")
        self.assertTrue(selected["fallback"])

    def test_selection_is_strictly_before_outer_target(self):
        rows = self.histories["House"]
        expected = ev.select_policy(rows, "House", 2020)
        changed = [dict(r, share=1-r["share"], forecast=1-r["forecast"]) if r["cycle"] >= 2020 else r for r in rows]
        actual = ev.select_policy(changed, "House", 2020)
        self.assertEqual(actual, expected)
        self.assertEqual(expected["common_inner_origins"], [2016, 2018])

    def test_tie_order_and_tolerance(self):
        self.assertEqual(ev.choose_family({"same_phase": 0.1, "persistence": 0.1 + 0.5e-12}), "persistence")
        self.assertEqual(ev.choose_family({"same_phase": 0.1, "persistence": 0.1 + 2e-12}), "same_phase")

    def test_all_outer_rows_and_final_policy_traces_exist(self):
        result = ev.development(self.histories)
        self.assertEqual(result["failure_count"], 0)
        selected = [r for r in result["predictions"] if r["stage"] == "outer_policy"]
        self.assertEqual(len(selected), 2 * 4 * 3)
        for r in result["predictions"]:
            if r["stage"] != "fixed_policy_loeo_future_trained":
                self.assertLess(max(r["training_years"]), r["target"])
            self.assertIn("selected_family", r)
        for c in ev.CHAMBERS:
            self.assertEqual(result["final_selection"][c]["common_inner_origins"], list(ev.INNER_YEARS))


class IncumbentTests(unittest.TestCase):
    def test_alias_is_zero_not_minimum_norm_extrapolation(self):
        x = np.array([[1, 1, -1], [1, 1, 0], [1, 1, 1]], dtype=float)
        y = np.array([2, 3, 4], dtype=float)
        beta, keep = ev.ordered_ols(x, y)
        self.assertEqual(keep, [0, 2])
        np.testing.assert_allclose(beta, [3, 0, 1], atol=1e-12)
        self.assertAlmostEqual(float(np.array([1, 0, 2]) @ beta), 5)

    def test_incumbent_scaling_uses_training_current_growth_only(self):
        histories = ev.extract_histories(synthetic_bundle())
        rows = histories["House"]
        targets = [r for r in rows if r["cycle"] == 2020]
        before = ev.incumbent_predictions(rows, targets, "House")
        changed = [dict(r, growth_pct=9999) if r["cycle"] >= 2020 else r for r in rows]
        self.assertEqual(ev.incumbent_predictions(changed, targets, "House"), before)
        self.assertEqual(before[0]["fit"]["terms"][-1], "P_R")
        self.assertEqual(before[0]["fit"]["terms"][-3:-1], ["G_prior", "Pres"])


class IntegrityTests(unittest.TestCase):
    def test_development_rejects_future_year_instead_of_filtering(self):
        bundle = synthetic_bundle()
        bundle["share_models"]["control_House"]["history"].append(row(2024, 0.5, 0.5))
        with self.assertRaisesRegex(ev.ProtocolError, "disallowed history year"):
            ev.extract_histories(bundle)

    def test_nonfinite_and_duplicate_history_refused(self):
        bundle = synthetic_bundle()
        bundle["share_models"]["control_House"]["history"][0]["forecast"] = float("nan")
        with self.assertRaises(ev.ProtocolError):
            ev.extract_histories(bundle)
        bundle = synthetic_bundle()
        bundle["share_models"]["control_House"]["history"].append(copy.deepcopy(bundle["share_models"]["control_House"]["history"][0]))
        with self.assertRaisesRegex(ev.ProtocolError, "duplicate"):
            ev.extract_histories(bundle)

    def test_holdout_rows_adapter_preserves_chambers(self):
        heldout = {"rows": [dict(row(2024, 0.4, 0.6, s), chamber=c) for c in ev.CHAMBERS for s in ev.INDUSTRIES]}
        adapted = ev.extract_histories(heldout, holdout=True)
        self.assertEqual(adapted["House"][0]["cycle"], 2024)
        heldout["rows"][0]["cycle"] = 2022
        with self.assertRaises(ev.ProtocolError):
            ev.extract_histories(heldout, holdout=True)

    def test_manifest_binds_history_input_code_and_no_failure(self):
        histories = ev.extract_histories(synthetic_bundle())
        result = ev.development(histories)
        manifest = ev.freeze_manifest(result, "sourcehash", histories, "codehash")
        ev.verify_manifest(manifest, "sourcehash", histories, "codehash")
        for field, value in (("input_sha256", "wrong"), ("evaluator_sha256", "changed"), ("failure_count", 1), ("reserved_2024_used_for_this_run_selection", True)):
            bad = dict(manifest, **{field: value})
            with self.assertRaises(ev.ProtocolError):
                ev.verify_manifest(bad, "sourcehash", histories, "codehash")

    def test_frozen_holdout_never_selects(self):
        histories = ev.extract_histories(synthetic_bundle())
        result = ev.development(histories)
        manifest = ev.freeze_manifest(result, "sourcehash", histories, "codehash")
        targets = {c: [row(2024, 0.5, 0.4, s) for s in ev.INDUSTRIES] for c in ev.CHAMBERS}
        with mock.patch.object(ev, "select_policy", side_effect=AssertionError("selection on holdout")):
            heldout = ev.holdout(histories, targets, manifest)
        self.assertFalse(heldout["selection_performed"])
        self.assertEqual(len(heldout["predictions"]), 2 * 4 * 5)
        self.assertTrue(heldout["not_an_untouched_test"])

    def test_exact_population_required(self):
        bundle = synthetic_bundle()
        for m in bundle["share_models"].values():
            m["history"] = [r for r in m["history"] if r["industry"] != "property_casualty"]
        with self.assertRaisesRegex(ev.ProtocolError, "four frozen industries"):
            ev.extract_histories(bundle)

    def test_loeo_is_fixed_choice_explicit_future_training_and_strict_phase(self):
        histories = ev.extract_histories(synthetic_bundle())
        with mock.patch.object(ev, "select_policy", side_effect=AssertionError("LOEO selected")):
            diagnostic = ev.fixed_policy_loeo(histories, {c: "ridge_control_1" for c in ev.CHAMBERS})
        self.assertFalse(diagnostic["failures"])
        earliest = next(r for r in diagnostic["predictions"] if r["target"] == 2014)
        self.assertIn(2022, earliest["training_years"])
        self.assertNotIn(2014, earliest["training_years"])
        self.assertNotIn(2016, earliest["fit"]["pair_end_years"])
        self.assertTrue(earliest["future_trained_diagnostic"])
        strict = ev.fixed_policy_loeo(histories, {c: "same_phase" for c in ev.CHAMBERS})
        self.assertEqual(len(strict["failures"]), 2)
        self.assertEqual({f["target"] for f in strict["failures"]}, {2014})

    def test_output_overwrite_refused_without_touching_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "run"
            result = {"predictions": [], "metrics": []}
            ev.write_outputs(destination, result)
            before = (destination / "evaluation.json").read_bytes()
            with self.assertRaises(FileExistsError):
                ev.write_outputs(destination, {"predictions": [], "metrics": [], "changed": True})
            self.assertEqual((destination / "evaluation.json").read_bytes(), before)

    def test_cli_rejects_bad_policy_before_opening_holdout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle.json"
            bundle.write_bytes(ev.canonical_bytes(synthetic_bundle()))
            self.assertEqual(ev.main(["--development", "--bundle", str(bundle), "--output", str(root / "dev")]), 0)
            policy = root / "dev" / "policy_manifest.json"
            with mock.patch.object(ev, "holdout", side_effect=AssertionError("holdout evaluated")):
                status = ev.main(["--holdout", "--bundle", str(bundle), "--output", str(root / "held"),
                                  "--policy-manifest", str(policy), "--policy-sha256", "wronghash",
                                  "--holdout-input", str(root / "does-not-exist.json")])
            self.assertEqual(status, 2)
            self.assertFalse((root / "held").exists())


if __name__ == "__main__":
    unittest.main()

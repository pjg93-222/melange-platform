import copy
import unittest

import baseline_refinement as m
from predict_interval import synthetic_example


def frame():
    return synthetic_example()["frame"]


class BaselineRefinementTests(unittest.TestCase):
    def test_no_mutation_and_baseline_equality(self):
        f = frame(); original = copy.deepcopy(f)
        result = m.predict(f, "baseline")
        self.assertEqual(f, original)
        for r in result:
            self.assertEqual(r["prediction"], r["baseline"])

    def test_mean_uses_three_direct_remainders(self):
        f = frame()
        for old, n in zip(f["history"], [1000, 2000, 9000]):
            old["remaining"]["D"] = dict(lo_cents=n, hi_cents=n + 300)
        r = m.predict(f, "mean_remaining")[0]
        self.assertEqual(r["prediction"], dict(lo_cents=10000, hi_cents=10300))

    def test_analogue_indices_and_house_noop(self):
        f = frame()
        for old, n in zip(f["history"], [1000, 2000, 9000]):
            old["remaining"]["D"] = dict(lo_cents=n, hi_cents=n)
        self.assertEqual(m.predict(f, "recent_half")[0]["prediction"]["lo_cents"], 11500)
        self.assertEqual(m.predict(f, "phase_half")[0]["prediction"]["lo_cents"], 8000)
        self.assertEqual(m.predict(f, "senate_six_year_half")[0]["prediction"]["lo_cents"], 8000)
        f["chamber"] = "Senate"
        self.assertEqual(m.predict(f, "senate_six_year_half")[0]["prediction"]["lo_cents"], 7500)

    def test_pace_shared_by_parties_and_bounded(self):
        f = frame()
        f["observed"]["D"] = dict(lo_cents=60000, hi_cents=65000)
        f["observed"]["R"] = dict(lo_cents=90000, hi_cents=95000)
        a, b = m.predict(f, "pace_sqrt")
        self.assertEqual(a["trace"]["shared_pace_factor"], dict(lo_cents=2, hi_cents=2))
        self.assertEqual(a["trace"]["shared_pace_factor"], b["trace"]["shared_pace_factor"])

    def test_negative_remaining_falls_back_without_clipping_source(self):
        f = frame()
        for old in f["history"]:
            old["remaining"]["D"] = dict(lo_cents=-100, hi_cents=100)
        for r in m.predict(f, "pace_sqrt"):
            self.assertEqual(r["prediction"], r["baseline"])
            self.assertIsNotNone(r["fallback_reason"])

    def test_negative_candidate_preserves_baseline(self):
        f = frame()
        for old, n in zip(f["history"], [-100000, 1000, 1000]):
            old["remaining"]["D"] = dict(lo_cents=n, hi_cents=n)
        r = m.predict(f, "mean_remaining")[0]
        self.assertEqual(r["prediction"], r["baseline"])
        self.assertIsNotNone(r["fallback_reason"])

    def test_interval_score_not_midpoint(self):
        r = dict(prediction=dict(lo_cents=80, hi_cents=120), prior_scale_cents=100)
        self.assertFalse(m.score(r, dict(lo_cents=90, hi_cents=110))["certified_within20"])

    def test_selector_never_reads_target_or_future_and_prefers_baseline_ties(self):
        scored = [dict(chamber="House", target=y, family=f, sector="commercial_banks", horizon="m12", party="D", certified_within20=True,
                       certified_within10=True, normalized_absolute_error_upper=.1,
                       absolute_error_upper_cents=100, fallback_reason=None)
                  for y in (2018, 2020, 2022) for f in m.FAMILIES]
        a = m.select(scored, "House", 2022)
        self.assertEqual(a["family"], "baseline")
        self.assertEqual(a["earlier_origins"], [2018, 2020])
        for r in scored:
            if r["target"] == 2022:
                r["certified_within20"] = False
                r["normalized_absolute_error_upper"] = 10000
        self.assertEqual(a, m.select(scored, "House", 2022))
        self.assertEqual(m.select(scored, "House", 2020)["reason"], "fewer than two earlier origins")

    def test_equal_count_different_selection_cells_rejected(self):
        rows = [dict(chamber="House", target=y, family=f, sector="commercial_banks", horizon="m12", party="D")
                for y in (2018, 2020) for f in m.FAMILIES]
        rows[-1]["party"] = "R"
        with self.assertRaisesRegex(ValueError, "identity populations differ"):
            m.select(rows, "House", 2022)

    def test_incomplete_evaluation_grid_rejected(self):
        with self.assertRaisesRegex(ValueError, "160-frame grid"):
            m.evaluate(dict(feature_frames=[frame()], truth_intervals=[]))


if __name__ == "__main__":
    unittest.main()

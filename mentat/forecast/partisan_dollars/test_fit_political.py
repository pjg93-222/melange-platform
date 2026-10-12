import copy
import unittest

import fit_political as m


def row(year, lean, dw, delta, sector="commercial_banks", horizon="m18"):
    def bound(x): return dict(lo_cents=x, hi_cents=x)
    return dict(target=year, sector=sector, chamber="House", horizon=horizon,
                features=dict(target_r_share=lean, champion_mean_dw=dw), prior_scale_cents=10000,
                baseline=dict(D=bound(5000), R=bound(5000)),
                truth=dict(D=bound(5000 - delta), R=bound(5000 + delta)),
                remaining=dict(D=bound(2500), R=bound(2500)))


class TrainingTests(unittest.TestCase):
    def test_later_truth_and_features_cannot_enter_training(self):
        rows = [row(2018, .3, -.2, -500), row(2020, .7, .2, 500), row(2022, .5, 0, 0)]
        a = m.fit(rows, "both", 2022)
        rows[-1] = row(2022, 0, -1, -100000000)
        self.assertEqual(a, m.fit(rows, "both", 2022))
        self.assertEqual(a["training_cycles"], [2018, 2020])

    def test_missing_is_excluded_not_neutral(self):
        rows = [row(2018, .3, -.2, -500), row(2020, None, .2, 500)]
        self.assertEqual(m.fit(rows, "both", 2022)["status"], "insufficient_earlier_elections")

    def test_horizon_repetition_does_not_change_fit(self):
        rows = [row(2018, .3, -.2, -500), row(2020, .7, .2, 500)]
        repeated = rows + [{**copy.deepcopy(rows[0]), "horizon": "m21"}]
        a, b = m.fit(rows, "both", 2022), m.fit(repeated, "both", 2022)
        self.assertEqual(a["coefficients"], b["coefficients"])

    def test_constant_signal_gets_zero_coefficient(self):
        rows = [row(2018, .5, -.2, -500), row(2020, .5, .2, 500)]
        model = m.fit(rows, "both", 2022)
        self.assertEqual(model["constant_features"], ["target_r_share"])
        self.assertEqual(model["coefficients"][0], 0)

    def test_ridge_shrinks_single_standardized_feature(self):
        model = m.fit([row(2018, .3, 0, -1000), row(2020, .7, 0, 1000)], "lean_only", 2022)
        self.assertAlmostEqual(model["coefficients"][0], .05)

    def test_full_interval_certification_cannot_use_midpoint(self):
        result = m.score(dict(lo_cents=80, hi_cents=120), dict(lo_cents=90, hi_cents=110))
        self.assertFalse(result["certified_within20"])

    def test_negative_remainder_excluded(self):
        r = row(2018, .4, .2, 100)
        r["remaining"]["D"]["lo_cents"] = -1
        self.assertFalse(m.usable(r, m.FEATURES["both"]))

    def test_nonfinite_scale_excluded(self):
        for value in (float("inf"), float("nan"), True):
            r = row(2018, .4, .2, 100)
            r["prior_scale_cents"] = value
            self.assertFalse(m.usable(r, m.FEATURES["both"]))


if __name__ == "__main__":
    unittest.main()

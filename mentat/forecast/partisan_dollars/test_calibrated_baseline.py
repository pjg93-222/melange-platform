import copy
import unittest
import calibrated_baseline as m


def row(year, truth=140, remaining=100, party="D"):
    def b(x): return dict(lo_cents=x, hi_cents=x)
    return dict(sector="commercial_banks", chamber="House", target=year, horizon="m12",party=party,
                prior_scale_cents=200, baseline=b(100+remaining), prediction=b(100+remaining),
                observed=b(100),remaining=b(remaining),truth=b(truth),original_baseline_fallback_reason=None)


class CalibrationTests(unittest.TestCase):
    def test_half_correction_and_full_endpoints(self):
        model=m.fit([row(2018),row(2020)],"House",2022)
        self.assertAlmostEqual(model["alpha"],.7)
        r=row(2022);r["remaining"]["hi_cents"]=120;r["baseline"]["hi_cents"]=220
        self.assertEqual(m.project(r,model)["prediction"],dict(lo_cents=170,hi_cents=184))

    def test_no_target_truth_enters_fit(self):
        rows=[row(2018),row(2020),row(2022,99999)]
        a=m.fit(rows,"House",2022);rows[-1]=row(2022,-99999)
        self.assertEqual(a,m.fit(rows,"House",2022))

    def test_original_prior_fallback_not_used_as_remaining(self):
        r=row(2018);r["original_baseline_fallback_reason"]="prior fallback"
        model=m.fit([r,row(2020)],"House",2022)
        self.assertEqual(model["status"],"insufficient_earlier_origins")
        self.assertEqual(model["excluded_training_rows"],1)

    def test_zero_energy_no_epsilon(self):
        model=m.fit([row(2018,remaining=0),row(2020,remaining=0)],"House",2022)
        self.assertEqual(model["status"],"zero_remaining_energy")
        self.assertEqual(model["alpha"],1)

    def test_clip_and_no_mutation(self):
        model=m.fit([row(2018,99999),row(2020,99999)],"House",2022)
        self.assertEqual(model["alpha"],1.5)
        r=row(2022);before=copy.deepcopy(r);m.project(r,model);self.assertEqual(r,before)

    def test_renormalizes_origins_and_does_not_count_extra_horizons_as_years(self):
        rows=[row(2018),row(2020,200)]
        a=m.fit(rows,"House",2022)
        rows.append(dict(row(2018),horizon="m15"))
        b=m.fit(rows,"House",2022)
        self.assertAlmostEqual(a["alpha"],b["alpha"])
        self.assertAlmostEqual(b["weight_sum"],1)

    def test_negative_remainder_keeps_signed_baseline(self):
        model=m.fit([row(2018),row(2020)],"House",2022)
        r=row(2022,remaining=-20)
        self.assertEqual(m.project(r,model)["prediction"],r["baseline"])

    def test_nonfinite_truth_cannot_be_clipped_into_coefficient(self):
        with self.assertRaises(ValueError):
            m.fit([row(2018,float("nan")),row(2020)],"House",2022)


if __name__=="__main__":unittest.main()

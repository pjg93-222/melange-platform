import unittest
import guarded_baseline as g

def row(year,within20=True,within10=True,error=.1,party="D"):
    return dict(sector="commercial_banks",chamber="House",target=year,horizon="m12",party=party,
                calibration_status="fitted",certified_within20=within20,certified_within10=within10,
                normalized_absolute_error_upper=error,absolute_error_upper_cents=100,fallback_reason=None)

class GuardTests(unittest.TestCase):
    def test_earlier_only_and_no_warmup_authorization(self):
        b=[row(2022,False),row(2024,False)]
        c=[row(2022),row(2024)]
        self.assertFalse(g.choose(b,c,"House",2022)["apply_calibration"])
        a=g.choose(b,c,"House",2024)
        self.assertTrue(a["apply_calibration"])
        c[-1]=row(2024,False,False,999)
        self.assertEqual(a,g.choose(b,c,"House",2024))

    def test_ties_and_worse_errors_retain_baseline(self):
        b=[row(2022)]
        self.assertFalse(g.choose(b,b,"House",2024)["apply_calibration"])
        self.assertFalse(g.choose([row(2022,False)],[row(2022,error=.2)],"House",2024)["apply_calibration"])

    def test_unfitted_does_not_authorize_guard(self):
        b=[row(2020,False)]
        c=[dict(row(2020),calibration_status="insufficient_earlier_origins")]
        self.assertFalse(g.choose(b,c,"House",2024)["apply_calibration"])

    def test_missing_or_duplicate_identity_rejected(self):
        with self.assertRaises(ValueError):g.choose([row(2022,party="R")],[row(2022)],"House",2024)
        with self.assertRaises(ValueError):g.choose([row(2022)],[row(2022),row(2022)],"House",2024)

if __name__=="__main__":unittest.main()

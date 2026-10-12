from datetime import date
from pathlib import Path
import sys
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.intervals import UncertainInterval, guaranteed_overlap_days, possible_overlap_days


class StafferIntervalTests(unittest.TestCase):
    def test_half_open_exact_overlap(self):
        a = UncertainInterval(date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 10), date(2024, 1, 10), "day", "day")
        b = UncertainInterval(date(2024, 1, 5), date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 8), "day", "day")
        self.assertEqual(guaranteed_overlap_days(a, b), 3)
        self.assertEqual(possible_overlap_days(a, b), 3)

    def test_uncertainty_distinguishes_guaranteed_and_possible(self):
        a = UncertainInterval(date(2024, 1, 1), date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 12), "day", "day")
        b = UncertainInterval(date(2024, 1, 7), date(2024, 1, 9), date(2024, 1, 11), date(2024, 1, 15), "day", "day")
        self.assertEqual(guaranteed_overlap_days(a, b), 0)
        self.assertEqual(possible_overlap_days(a, b), 5)

    def test_asserted_current_is_right_censored_at_build_time(self):
        current = UncertainInterval(date(2025, 6, 1), date(2025, 6, 1), None, None, "day", "unknown", True)
        clipped = current.effective(date(2026, 8, 29))
        self.assertEqual(clipped.end_earliest, date(2026, 8, 29))
        self.assertFalse(clipped.asserted_current)


if __name__ == "__main__":
    unittest.main()

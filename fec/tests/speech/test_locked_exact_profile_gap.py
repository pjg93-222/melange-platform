import copy
import unittest

from fec.speech import build_locked_exact_profile_gap as GAP


class LockedCalibrationTests(unittest.TestCase):
    def calibration(self):
        return {
            "schema": "speech-speaker-anchor-calibration-v2",
            "status": "PASS_ZERO_FALSE_ACCEPT_CALIBRATION",
            "false_accepts": 0,
            "minimum_target_best_score": GAP.LOCKED_MIN_TARGET_BEST_SCORE,
            "minimum_best_anchor_margin": GAP.LOCKED_MIN_BEST_ANCHOR_MARGIN,
            "scoring_contract_sha256": GAP.LOCKED_SCORING_CONTRACT_SHA256,
        }

    def test_exact_published_contract_passes(self):
        self.assertEqual(
            GAP.validate_locked_calibration(self.calibration()),
            (
                GAP.LOCKED_MIN_TARGET_BEST_SCORE,
                GAP.LOCKED_MIN_BEST_ANCHOR_MARGIN,
            ),
        )

    def test_lower_score_floor_is_refused(self):
        row = copy.deepcopy(self.calibration())
        row["minimum_target_best_score"] -= 0.000001
        with self.assertRaisesRegex(GAP.GapError, "drifted"):
            GAP.validate_locked_calibration(row)

    def test_lower_margin_floor_is_refused(self):
        row = copy.deepcopy(self.calibration())
        row["minimum_best_anchor_margin"] -= 0.000001
        with self.assertRaisesRegex(GAP.GapError, "drifted"):
            GAP.validate_locked_calibration(row)

    def test_scoring_contract_hash_drift_is_refused(self):
        row = copy.deepcopy(self.calibration())
        row["scoring_contract_sha256"] = "0" * 64
        with self.assertRaisesRegex(GAP.GapError, "drifted"):
            GAP.validate_locked_calibration(row)


if __name__ == "__main__":
    unittest.main()

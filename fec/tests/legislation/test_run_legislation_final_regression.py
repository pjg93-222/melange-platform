"""Acceptance-allowance contract for the legislation final-regression runner.

These tests exercise the pure failure classifier only.  ``run()`` executes the
entire regression corpus and writes an immutable receipt directory, so it is
never invoked here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "fec" / "legislation" / "run_legislation_final_regression.py"
SPEC = importlib.util.spec_from_file_location(
    "legislation_final_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
FR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FR)


# Resolved defensively so a runner missing the contract fails on the assertion
# that names the defect, not on an import error.
ALLOWED = sorted(getattr(FR, "KNOWN_ACCEPTANCE_BLOCKER_TEST_IDS", ()))
EXPECTED_COUNT = getattr(FR, "EXPECTED_ACCEPTANCE_BLOCKER_FAILURE_COUNT", -1)


def classify(failed_ids: set[str]) -> dict[str, object]:
    classifier = getattr(FR, "classify_python_failures", None)
    if classifier is None:
        raise AssertionError(
            "run_legislation_final_regression exposes no exact-match failure "
            "classifier"
        )
    return classifier(failed_ids)


class AcceptanceBlockerAllowanceTests(unittest.TestCase):
    def test_allowance_ids_are_module_qualified(self) -> None:
        self.assertEqual(len(ALLOWED), EXPECTED_COUNT)
        self.assertEqual(len(ALLOWED), 5)
        for test_id in ALLOWED:
            parts = test_id.split(".")
            self.assertGreaterEqual(len(parts), 6, test_id)
            self.assertEqual(parts[:3], ["fec", "tests", "legislation"])
            self.assertTrue(parts[3].startswith("test_"), test_id)
            module_path = (
                ROOT / Path(*parts[:-2]).with_suffix(".py")
            )
            self.assertTrue(module_path.is_file(), module_path)

    def test_exactly_the_expected_residual_is_absolved(self) -> None:
        result = classify(set(ALLOWED))
        self.assertEqual(result["expected_acceptance_blocker_failures"], ALLOWED)
        self.assertEqual(result["implementation_regression_failures"], [])
        self.assertEqual(result["stale_acceptance_blocker_allowances"], [])
        self.assertTrue(result["acceptance_blocker_allowance_current"])
        self.assertEqual(
            result["observed_acceptance_blocker_failure_count"],
            EXPECTED_COUNT,
        )

    def test_a_lookalike_in_another_module_is_not_absolved(self) -> None:
        """The suffix match let any module reuse a whitelisted class+method.

        This id ends with exactly the whitelisted class and method name but
        lives in a different module, so it must count as a real regression.
        """

        impostor = (
            "fec.tests.legislation.test_some_other_module."
            "PresyncAuditTests."
            "test_decay_workflow_snapshot_values_are_not_gate_authority"
        )
        result = classify({*ALLOWED, impostor})
        self.assertNotIn(
            impostor, result["expected_acceptance_blocker_failures"]
        )
        self.assertEqual(
            result["implementation_regression_failures"], [impostor]
        )
        self.assertTrue(result["acceptance_blocker_allowance_current"])

    def test_a_new_failure_cannot_hide_inside_the_allowance(self) -> None:
        extra = (
            "fec.tests.legislation.test_run_legislation_presync_audit."
            "PresyncAuditTests.test_brand_new_failure"
        )
        result = classify({*ALLOWED, extra})
        self.assertEqual(result["implementation_regression_failures"], [extra])
        self.assertEqual(
            result["observed_acceptance_blocker_failure_count"],
            EXPECTED_COUNT,
        )

    def test_a_stale_allowance_entry_is_a_regression(self) -> None:
        """A whitelisted test that now passes must not be quietly forgiven."""

        result = classify(set(ALLOWED[1:]))
        self.assertEqual(
            result["stale_acceptance_blocker_allowances"], [ALLOWED[0]]
        )
        self.assertFalse(result["acceptance_blocker_allowance_current"])
        self.assertEqual(
            result["observed_acceptance_blocker_failure_count"],
            EXPECTED_COUNT - 1,
        )

    def test_allowance_drift_is_counted_as_an_implementation_regression(
        self,
    ) -> None:
        """The residual count must be compared to something.

        ``explicit_acceptance_blocker_failure_count`` was never checked against
        an expected value anywhere, so the currentness auditor accepted the
        allowance as proof of zero regressions.  The receipt now folds drift
        into ``actual_implementation_regression_count``, which that auditor
        already requires to be zero.
        """

        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "+ int(not allowance_current)",
            source,
        )
        self.assertIn(
            '"acceptance_blocker_allowance_current": allowance_current,',
            source,
        )
        self.assertIn(
            '"expected_acceptance_blocker_failure_count": (',
            source,
        )


if __name__ == "__main__":
    unittest.main()

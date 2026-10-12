import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


FEC_DIR = Path(__file__).resolve().parents[2]
if str(FEC_DIR) not in sys.path:
    sys.path.insert(0, str(FEC_DIR))

from legislation import run_legislation_timeout_rollover as rollover


class TimeoutRolloverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _timeout_manifest(self, sequence: int) -> Path:
        receipt = self.root / f"timeout-{sequence}.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema": "congress-automatic-verification-cooldown-v1",
                    "consecutive_timeouts": sequence,
                    "cooldown_ms": 7_200_000,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        manifest = self.root / f"manifest.partial.{sequence}.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "acu-congress-advanced-pages-manifest-v1",
                    "queries": [{"query_id": "q1"}],
                    "automatic_verification_timeout": {
                        "classification": "automatic_verification_timeout",
                        "receipt_file": receipt.name,
                        "receipt_sha256": hashlib.sha256(
                            receipt.read_bytes()
                        ).hexdigest(),
                        "consecutive_timeouts": sequence,
                        "cooldown_ms": 7_200_000,
                        "next_retry_not_before": (
                            f"2026-07-26T{sequence:02d}:00:00Z"
                        ),
                        "interaction_performed": False,
                        "captcha_bypass_attempted": False,
                        "collection_stopped_without_result_acceptance": True,
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return manifest

    def test_repeated_capped_timeout_transitions_roll_once_each(self) -> None:
        seventh = self._timeout_manifest(7)
        eighth = self._timeout_manifest(8)
        ninth = self._timeout_manifest(9)

        first = rollover.capped_timeout_transition(seventh, eighth)
        repeated = rollover.capped_timeout_transition(eighth, eighth)
        second = rollover.capped_timeout_transition(eighth, ninth)

        self.assertTrue(first["changed"])
        self.assertEqual((7, 8), (first["previous_count"], first["current_count"]))
        self.assertFalse(repeated["changed"])
        self.assertEqual((8, 8), (repeated["previous_count"], repeated["current_count"]))
        self.assertTrue(second["changed"])
        self.assertEqual((8, 9), (second["previous_count"], second["current_count"]))
        self.assertEqual(7_200_000, first["cooldown_ms"])
        self.assertEqual(7_200_000, second["cooldown_ms"])

    def test_timeout_receipt_tamper_fails_closed(self) -> None:
        eighth = self._timeout_manifest(8)
        ninth = self._timeout_manifest(9)
        (self.root / "timeout-9.json").write_text(
            "tampered", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            rollover.RolloverError,
            "receipt validation failed",
        ):
            rollover.capped_timeout_transition(eighth, ninth)

    def test_supervisor_polls_rollover_before_wait_and_before_pipeline(self) -> None:
        wrapper = (
            FEC_DIR / "run_legislation_specificity_followup.ps1"
        ).read_text(encoding="utf-8")
        loop_poll = wrapper.index(
            "Invoke-ValidatedTimeoutRollover *>> $Log",
            wrapper.index("# Poll the validated strict-manifest selector"),
        )
        sleep = wrapper.index("Start-Sleep -Seconds 30", loop_poll)
        final_poll = wrapper.index(
            "Invoke-ValidatedTimeoutRollover *>> $Log",
            sleep,
        )
        pipeline = wrapper.index("$pipelineStartedAt", final_poll)
        self.assertLess(loop_poll, sleep)
        self.assertLess(final_poll, pipeline)
        self.assertIn(
            "timeout_rollover_polling_while_waiting = $true",
            wrapper,
        )


if __name__ == "__main__":
    unittest.main()

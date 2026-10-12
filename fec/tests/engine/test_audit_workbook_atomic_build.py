from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from stance import honors_workbook as hw  # noqa: E402
from surfaces import build_audit_workbook as builder  # noqa: E402


class AuditWorkbookAtomicBuild(unittest.TestCase):
    """A failure between the base build and the recognition-view merge must leave the live file alone."""

    def _run(self, publish):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "Moneyball Audit Workbook.xlsx"
            live.write_bytes(b"LIVE-ORIGINAL")
            with mock.patch.object(builder, "ensure_inputs"),                     mock.patch.object(builder, "_read_json", return_value={}),                     mock.patch.object(builder, "build",
                                      side_effect=lambda h, c, r, out: Path(out).write_bytes(b"BASE-STEP-1")),                     mock.patch.object(hw, "publish", side_effect=publish),                     mock.patch.object(hw, "_write_bindings"):
                try:
                    builder.build_default(out_path=str(live), handoff_path=str(Path(tmp) / "h.json"))
                    error = None
                except RuntimeError as exc:
                    error = exc
            return error, live.read_bytes(), sorted(os.listdir(tmp))

    def test_failure_between_steps_leaves_live_file_untouched(self):
        def boom(path, *a, **k):
            raise RuntimeError("killed between step 1 and step 2")
        error, body, listing = self._run(boom)
        self.assertIsNotNone(error)
        self.assertEqual(b"LIVE-ORIGINAL", body)
        self.assertEqual(["Moneyball Audit Workbook.xlsx"], listing)  # scratch cleaned up

    def test_success_replaces_live_file_after_both_steps(self):
        def merge(path, *a, **k):
            Path(path).write_bytes(Path(path).read_bytes() + b"+STEP-2")
        error, body, listing = self._run(merge)
        self.assertIsNone(error)
        self.assertEqual(b"BASE-STEP-1+STEP-2", body)
        self.assertEqual(["Moneyball Audit Workbook.xlsx"], listing)


if __name__ == "__main__":
    unittest.main()

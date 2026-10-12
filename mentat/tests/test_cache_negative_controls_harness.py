"""response_cache_negative_controls.py itself (review of batch 1, round 3, 2026-09-24).

  - A variant run that does not finish (timeout or crash) is recorded under the pseudo-test '<run>'. The
    coverage listing raised IndexError on that name, so the harness printed no verdict and wrote no --out report.
  - Since review H2 a lock this process holds is live to this process too, so R-lock-leak made later builds of a
    leaked key wait the full WAIT_SECONDS and the suites outran the harness's TIMEOUT. R-lock-leak now also
    shortens the wait, and a passing control runs that same wait without the defect.
  - Once the harness finished again, two variants whose anchors the eviction-by-value change had removed were
    INVALID, so it could not pass. Every variant must apply to the modules as they are now.

The harness's main() runs here with its suite runner replaced: no suite runs, so each test takes seconds.
CACHE_NEGCTL_UNDER_TEST=<path> pins the harness under test (default: the one beside this file).
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

HARNESS = Path(os.environ.get('CACHE_NEGCTL_UNDER_TEST')
               or Path(__file__).resolve().parent / 'response_cache_negative_controls.py')
PASSING = {'test_response_cache': {'outcomes': {'test_response_cache.TierTests.test_stand_in': 'ok'}, 'tracebacks': {}}}


def load():
    spec = importlib.util.spec_from_file_location('cache_negctl_under_test', HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_variant(overlay):
    return overlay is not None and re.fullmatch(r'v\d+', Path(overlay).parents[1].name) is not None


class HarnessTests(unittest.TestCase):
    def main(self, harness, only, variant_result, seen=None):
        """harness.main() for the variants `only`: every control run passes, every variant run returns
        `variant_result`, and `seen` gets each run's response_cache.py (None for the untouched run).
        (exit code or the exception main() raised, what it printed, the --out report or None)."""
        def run(overlay, work):
            if seen is not None:
                seen.append((is_variant(overlay),
                             None if overlay is None else (Path(overlay) / 'response_cache.py').read_text(encoding='utf-8')))
            return json.loads(json.dumps(variant_result if is_variant(overlay) else PASSING))
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'report.json'
            printed = io.StringIO()
            argv = ['response_cache_negative_controls.py', '--only', ','.join(only), '--out', str(out)]
            with mock.patch.object(harness, 'run', run), mock.patch.object(sys, 'argv', argv), \
                    contextlib.redirect_stdout(printed):
                try:
                    result = harness.main()
                except Exception as error:   # noqa: BLE001 - a crash is the defect under test
                    result = error
            report = json.loads(out.read_text(encoding='utf-8')) if out.exists() else None
        return result, printed.getvalue(), report

    def test_a_run_that_did_not_finish_is_reported_and_the_report_written(self):
        harness = load()
        for outcome in ({'timeout': True}, {'crashed': 'the log tail'}):
            with self.subTest(outcome=sorted(outcome)):
                result, printed, report = self.main(harness, ['R-lock-leak'], outcome)
                self.assertNotIsInstance(result, Exception, f'the harness crashed: {result!r}\n{printed[-1500:]}')
                self.assertIsNotNone(report, 'no --out report was written')
                self.assertEqual(report['variants']['R-lock-leak']['state'], 'CAUGHT (run did not finish)')
                self.assertEqual(report['coverage'], {'<run>': ['R-lock-leak']})
                self.assertRegex(printed, r'(?m)^  <run>\s+<- R-lock-leak$')
                self.assertIn(report['verdict'], printed)

    def test_the_lock_leak_variant_waits_seconds_beside_a_control_with_the_same_wait(self):
        harness = load()
        seen = []
        result, printed, report = self.main(harness, ['R-lock-leak'], PASSING, seen)
        self.assertNotIsInstance(result, Exception, f'the harness crashed: {result!r}\n{printed[-1500:]}')
        variants = [text for variant, text in seen if variant]
        controls = [text for variant, text in seen if not variant and text is not None]
        self.assertEqual(len(variants), 1, seen)
        wait = re.search(r'(?m)^WAIT_SECONDS = (\d+)$', variants[0])
        self.assertIsNotNone(wait, 'the variant has no WAIT_SECONDS line')
        self.assertLessEqual(int(wait.group(1)), 10, 'R-lock-leak waits minutes on its own leaked lock')
        old, new = next(edits for name, _, edits in harness.VARIANTS if name == 'R-lock-leak')[0]
        self.assertTrue(any(text.count(old) == 1 and text.replace(old, new) == variants[0] for text in controls),
                        'no passing control differs from R-lock-leak by the leak alone')

    def test_every_variant_applies_to_the_current_modules(self):
        # The harness applies each edit in turn and calls a variant INVALID unless its text occurs exactly once;
        # one INVALID variant makes the whole run fail. Found here, in seconds, not at the end of a 30-minute run.
        harness = load()
        stale = []
        for identifier, module, edits in harness.VARIANTS:
            text = (harness.APP / module).read_text(encoding='utf-8')
            for old, new in edits:
                if text.count(old) != 1:
                    stale.append(f'{identifier}: occurs {text.count(old)} times: {old[:70]!r}')
                    break
                text = text.replace(old, new)
        self.assertEqual(stale, [])


if __name__ == '__main__':
    unittest.main()

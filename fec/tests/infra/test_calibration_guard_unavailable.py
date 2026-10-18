"""AST-only calibration guard tests; no real calibration, model, or artifacts."""
import ast
import contextlib
import io
import os
from pathlib import Path
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[2] / 'calibration_guard.py'


def harness(code, published):
    tree = ast.parse(SOURCE.read_bytes())
    selected = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                and node.name in ('check', 'main')], type_ignores=[])
    env = {'os': os, 'GUARDED': {'fixture': ('synthetic calibration', code, published)}}
    exec(compile(selected, str(SOURCE), 'exec'), env)
    return env


class CalibrationAvailability(unittest.TestCase):
    def test_unreadable_code_cannot_report_success_even_when_acknowledged(self):
        for ack in ('', '1', 'fixture'):
            with self.subTest(ack=ack):
                source = mock.Mock(side_effect=ImportError('synthetic unavailable dependency'))
                published = mock.Mock(return_value=1)
                stream = io.StringIO()
                with mock.patch.dict(os.environ, {'CALIB_ACK': ack}), contextlib.redirect_stdout(stream):
                    with self.assertRaisesRegex(RuntimeError, 'cannot read code value'):
                        harness(source, published)['main']()
                self.assertNotIn('calibration guard: OK', stream.getvalue())
                published.assert_not_called()

    def test_matching_calibration_is_unchanged(self):
        with contextlib.redirect_stdout(io.StringIO()):
            drift, checked = harness(lambda: 1, lambda: 1)['check']()
        self.assertEqual((drift, checked), ({}, ['fixture']))

    def test_real_drift_is_still_reported_and_specific_ack_still_works(self):
        env = harness(lambda: 2, lambda: 1)
        with contextlib.redirect_stdout(io.StringIO()):
            drift, checked = env['check']()
            acknowledged, checked_again = env['check'](['fixture'])
        self.assertEqual(drift, {'fixture': ('synthetic calibration', 2, 1)})
        self.assertEqual(acknowledged, {})
        self.assertEqual(checked, checked_again)

    def test_missing_first_publication_is_still_allowed(self):
        with contextlib.redirect_stdout(io.StringIO()):
            drift, checked = harness(lambda: 1, lambda: None)['check']()
        self.assertEqual((drift, checked), ({}, ['fixture']))


if __name__ == '__main__':
    unittest.main(verbosity=2)

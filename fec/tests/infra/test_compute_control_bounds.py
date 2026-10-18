"""Compute-control timeout regressions. No processes or network are started."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bounded_subprocess as bounded
import compute_engine as engine

IMAGE = 'example.test/compute@sha256:' + 'a' * 64


class ControlBounds(unittest.TestCase):
    def test_nonfinite_timeout_rejected_before_launch(self):
        for timeout in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(timeout=timeout), mock.patch.object(
                    bounded.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
                with self.assertRaises(ValueError):
                    bounded.run_bounded(['fixture'], timeout=timeout)

    def test_positive_finite_timeout_preserves_result(self):
        process = SimpleNamespace(pid=12, wait=mock.Mock(return_value=0))
        with mock.patch.object(bounded.subprocess, 'Popen', return_value=process):
            result = bounded.run_bounded(['fixture'], timeout=0.25)
        process.wait.assert_called_once_with(timeout=0.25)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.timed_out)

    def test_manifest_resolution_uses_bounded_capture(self):
        result = SimpleNamespace(stdout=IMAGE + '\n', stderr='', returncode=0, timed_out=False)
        with mock.patch.object(engine, 'run_bounded', return_value=result, create=True) as run, \
                mock.patch.object(engine.subprocess, 'run', side_effect=AssertionError('pipe capture forbidden')):
            self.assertEqual(engine.image('compute', 'IMAGE_FIXTURE', root=Path.cwd(), environ={}), IMAGE)
        self.assertEqual(run.call_args.kwargs['timeout'], 60)
        self.assertEqual(run.call_args.args[0][-2:], ['image', 'compute'])

    def test_timed_out_manifest_never_accepts_even_valid_stdout(self):
        result = SimpleNamespace(stdout=IMAGE, stderr='TIMED OUT', returncode=124, timed_out=True)
        with mock.patch.object(engine, 'run_bounded', return_value=result, create=True), \
                mock.patch.object(engine.subprocess, 'run', side_effect=AssertionError('pipe capture forbidden')):
            with self.assertRaises(engine.EngineError):
                engine.image('compute', 'IMAGE_FIXTURE', root=Path.cwd(), environ={})

    def test_reap_removes_after_bounded_stop_timeout(self):
        result = SimpleNamespace(stdout='', stderr='TIMED OUT', returncode=124, timed_out=True)
        with mock.patch.object(engine, 'run_bounded', return_value=result, create=True) as run, \
                mock.patch.object(engine.subprocess, 'run', side_effect=AssertionError('pipe capture forbidden')):
            engine.reap(SimpleNamespace(container='fixture-container'))
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['docker', 'stop', '-t', '30', 'fixture-container'], ['docker', 'rm', '-f', 'fixture-container']])
        self.assertEqual([call.kwargs['timeout'] for call in run.call_args_list], [90, 60])

    def test_reap_removes_after_stop_oserror(self):
        with mock.patch.object(engine, 'run_bounded', side_effect=[OSError('fixture'), None], create=True) as run, \
                mock.patch.object(engine.subprocess, 'run', side_effect=AssertionError('pipe capture forbidden')):
            engine.reap(SimpleNamespace(container='fixture-container'))
        self.assertEqual(run.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)

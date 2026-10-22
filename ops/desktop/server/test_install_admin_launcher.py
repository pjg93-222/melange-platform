import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / 'Install-AdminLauncher.ps1'
POWERSHELL = shutil.which('powershell.exe')


def squash(text):
    return re.sub(r'\s+', '', text)   # Windows PowerShell may wrap long error lines


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows PowerShell unavailable')
class WorktreeGuardTests(unittest.TestCase):
    """Runs a copy of the installer with a missing python.exe, so no run reaches the shortcut writes.
    -Uninstall is never run: it would delete the real shortcuts."""

    def run_copy(self, *parts):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        server = root.joinpath(*parts, 'ops', 'desktop', 'server')
        server.mkdir(parents=True)
        shutil.copy(SCRIPT, server)
        argv = [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(server / SCRIPT.name),
                '-PythonExecutable', str(root / 'missing' / 'python.exe'), '-StateDirectory', str(root / 'state')]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return root, done.returncode, done.stdout + done.stderr

    def test_worktree_copy_is_refused_and_names_the_main_checkout_copy(self):
        root, code, output = self.run_copy('checkout', '.claude', 'worktrees', 'agitated-perlman')
        self.assertNotEqual(code, 0)
        self.assertIn('Runtheinstallerfromthemaincheckout', squash(output))
        self.assertIn(squash(str(root / 'checkout' / 'ops' / 'desktop' / 'server' / SCRIPT.name)), squash(output))

    def test_main_checkout_copy_passes_the_guard(self):
        _, code, output = self.run_copy('checkout')
        self.assertNotEqual(code, 0)   # the missing python.exe stops it before any shortcut is written
        self.assertNotIn('maincheckout', squash(output))
        self.assertIn('python.exe', squash(output))

    def test_uninstall_returns_before_the_guard(self):
        text = SCRIPT.read_text(encoding='utf-8')
        self.assertLess(text.index('if ($Uninstall) {'), text.index('if ($PSScriptRoot -match'))


if __name__ == '__main__':
    unittest.main()

"""ops/ci/build_or_reuse.sh: "reuse" only on a proven hit, "build" only on a proven miss, anything else fails the step.
Runs the script with bash and a fake `docker` on PATH; skipped where bash is not usable (the script itself runs on the Linux runner)."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ci" / "build_or_reuse.sh"
HASH, DIGEST, REPO = "a" * 64, "sha256:" + "b" * 64, "ghcr.io/pjg93-222/melange-desk"


def find_bash() -> str | None:
    candidates = [shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"]
    for exe in candidates:
        if exe and Path(exe).is_file() and "system32" not in exe.lower():          # System32\bash.exe is WSL, not Git Bash
            try:
                if subprocess.run([exe, "-c", "echo ok"], capture_output=True, text=True, timeout=20).stdout.strip() == "ok":
                    return exe
            except (OSError, subprocess.SubprocessError):
                pass
    return None


BASH = find_bash()
FAKE_DOCKER = """#!/bin/sh
echo "$@" >> "$FAKE_LOG"
case "$*" in
  *imagetools\\ inspect*) [ -n "$FAKE_INSPECT_OUT" ] && { echo "$FAKE_INSPECT_OUT"; exit 0; }; echo "$FAKE_INSPECT_ERR" >&2; exit 1 ;;
  *imagetools\\ create*) exit 0 ;;
esac
exit 3
"""


@unittest.skipUnless(BASH, "no usable bash")
class BuildOrReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bor-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "bin").mkdir()
        docker = self.tmp / "bin" / "docker"
        docker.write_bytes(FAKE_DOCKER.encode())
        docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
        self.log, self.out = self.tmp / "log.txt", self.tmp / "out.txt"

    def run_script(self, *args, inspect_out="", inspect_err=""):
        env = {**os.environ, "PATH": str(self.tmp / "bin") + os.pathsep + os.environ["PATH"], "FAKE_LOG": str(self.log),
               "FAKE_INSPECT_OUT": inspect_out, "FAKE_INSPECT_ERR": inspect_err, "GITHUB_OUTPUT": str(self.out)}
        return subprocess.run([BASH, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)

    def outputs(self):
        return self.out.read_text().split() if self.out.is_file() else []

    def test_a_hit_reuses_the_digest_it_found(self):
        run = self.run_script("check", REPO, HASH, inspect_out=DIGEST)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.outputs(), ["reused=true", f"digest={DIGEST}"])
        self.assertIn(f"inspect {REPO}:inputs-{HASH}", self.log.read_text().replace("buildx imagetools ", ""))

    def test_a_proven_miss_builds(self):
        run = self.run_script("check", REPO, HASH, inspect_err=f"ERROR: {REPO}:inputs-{HASH}: not found")
        self.assertEqual((run.returncode, self.outputs()), (0, ["reused=false"]), run.stderr)

    def test_any_other_registry_failure_stops_the_step_instead_of_guessing(self):
        run = self.run_script("check", REPO, HASH, inspect_err="ERROR: failed to authorize: 401 Unauthorized")
        self.assertEqual(run.returncode, 1)
        self.assertEqual(self.outputs(), [])

    def test_a_malformed_digest_is_not_reused(self):
        run = self.run_script("check", REPO, HASH, inspect_out="sha256:nothex")
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.outputs(), [])

    def test_tag_gives_the_new_digest_the_inputs_tag_and_the_extras(self):
        run = self.run_script("tag", REPO, HASH, DIGEST, "v2026.10.12.1")
        self.assertEqual(run.returncode, 0, run.stderr)
        line = self.log.read_text().strip()
        self.assertIn(f"-t {REPO}:inputs-{HASH}", line)
        self.assertIn(f"-t {REPO}:v2026.10.12.1", line)
        self.assertTrue(line.endswith(f"{REPO}@{DIGEST}"))

    def test_bad_arguments_are_refused_before_docker_runs(self):
        for args in (("check", "docker.io/x/y", HASH), ("check", REPO, "short"), ("tag", REPO, HASH, "latest"),
                     ("tag", REPO, HASH, DIGEST, "bad tag; rm"), ("nope", REPO, HASH)):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_script(*args).returncode, 0)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()

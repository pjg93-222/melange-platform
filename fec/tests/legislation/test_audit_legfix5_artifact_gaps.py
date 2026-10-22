"""Behavioural tests for the legfix5 artifact-gap auditor.

Every test drives ``main()`` end to end against a synthetic receipt tree and
asserts on the printed report, so the suite exercises the same surface an
operator reads.  ``ROOT`` and ``WORKSPACE`` are patched on the module rather
than passed in, which keeps the tests runnable against any revision of the
auditor -- including the pre-fix one, which is how the phantom-MISSING and
name-collapse regressions were proven.

The tree under ``audits/`` is never read or written by these tests.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

from fec.audit import audit_legfix5_artifact_gaps as auditor


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def run_audit(root: Path, workspace: Path, argv: list[str] | None = None):
    """Run the auditor over ``root`` and return ``(exit_code, stdout)``."""

    saved = (auditor.ROOT, auditor.WORKSPACE, sys.argv)
    auditor.ROOT, auditor.WORKSPACE = root, workspace
    sys.argv = ["audit_legfix5_artifact_gaps.py", *(argv or [])]
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = auditor.main()
    finally:
        auditor.ROOT, auditor.WORKSPACE, sys.argv = saved
    return code, buffer.getvalue()


def missing_count(output: str) -> int:
    match = re.search(r"=== MISSING \((\d+)", output)
    if match is None:
        raise AssertionError(f"no MISSING header in output:\n{output}")
    return int(match.group(1))


def distinct_paths(output: str) -> int:
    match = re.search(r"distinct [a-z ]*artifact paths: (\d+)", output)
    if match is None:
        raise AssertionError(f"no distinct-path line in output:\n{output}")
    return int(match.group(1))


class ArtifactGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary.name)
        self.root = self.workspace / "audits" / "legfix5"
        self.root.mkdir(parents=True)
        self.addCleanup(self._temporary.cleanup)

    # -- mis-rooting ------------------------------------------------------

    def test_manifest_relative_declaration_resolves_beside_its_manifest(
        self,
    ) -> None:
        """``union.records_file`` names a sibling of the manifest, not of the
        workspace.  Rooting it at the workspace invented a MISSING row for a
        file that is present."""

        stage = self.root / "congress_advanced_pages_20260725"
        body = b"{\"canonical_key\":\"117:hr:1\"}\n"
        write_bytes(stage / "congress_advanced_records.ndjson", body)
        write_json(
            stage / "manifest.json",
            {
                "schema": "acu-congress-advanced-discovery-v1",
                "status": "complete",
                "union": {
                    "records_file": "congress_advanced_records.ndjson",
                    "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                },
            },
        )

        code, output = run_audit(self.root, self.workspace)
        self.assertEqual(missing_count(output), 0, output)
        self.assertEqual(code, 0, output)

    def test_workspace_relative_declaration_still_falls_back_to_the_workspace(
        self,
    ) -> None:
        """Non-regression: rows like ``fec/tests/legislation/test_*.py`` are genuinely
        workspace-relative and must keep resolving."""

        write_bytes(
            self.workspace / "fec" / "tests" / "legislation" / "test_thing.py",
            b"x = 1\n",
        )
        write_json(
            self.root / "stage" / "receipt.json",
            {"path": "fec/tests/legislation/test_thing.py", "bytes": 6},
        )

        code, output = run_audit(self.root, self.workspace)
        self.assertEqual(missing_count(output), 0, output)
        self.assertEqual(code, 0, output)

    def test_absolute_declaration_of_an_absent_body_is_still_reported(self) -> None:
        """Non-regression: the archive losses this audit exists to catch are
        declared with absolute paths and must keep failing the gate."""

        write_json(
            self.root / "stage" / "receipts" / "116" / "hamdt" / "2" / "receipt.json",
            {
                "status": "accepted",
                "html_file": str(self.root / "stage" / "gone" / "page.html.gz"),
            },
        )

        code, output = run_audit(self.root, self.workspace)
        self.assertEqual(missing_count(output), 1, output)
        self.assertEqual(code, 1, output)

    # -- de-duplication ---------------------------------------------------

    def test_same_named_artifacts_in_different_directories_stay_separate(
        self,
    ) -> None:
        """``failure_attempts.ndjson`` is declared from six stage directories.
        Keying de-duplication on the raw string collapsed them into one row and
        resolved that row against a directory none of them meant."""

        for stage, body in (("stage_a", b"aaaaa"), ("stage_b", b"bbbbbbbbb")):
            write_bytes(self.root / stage / "failure_attempts.ndjson", body)
            write_json(
                self.root / stage / "receipt.json",
                {
                    "outputs": {
                        "attempts": {
                            "path": "failure_attempts.ndjson",
                            "bytes": len(body),
                        }
                    }
                },
            )

        code, output = run_audit(self.root, self.workspace)
        self.assertEqual(distinct_paths(output), 2, output)
        self.assertEqual(missing_count(output), 0, output)
        self.assertIn("present and size-verified: 2", output)
        self.assertEqual(code, 0, output)

    def test_a_loss_in_one_directory_is_not_masked_by_a_twin_in_another(
        self,
    ) -> None:
        """Only ``stage_b``'s copy is gone.  The report must name ``stage_b``,
        not a synthetic workspace-root path, and must still see ``stage_a``."""

        write_bytes(self.root / "stage_a" / "batch_accounting.ndjson", b"abc")
        for stage in ("stage_a", "stage_b"):
            write_json(
                self.root / stage / "receipt.json",
                {"path": "batch_accounting.ndjson", "bytes": 3},
            )

        code, output = run_audit(self.root, self.workspace)
        self.assertEqual(distinct_paths(output), 2, output)
        self.assertEqual(missing_count(output), 1, output)
        self.assertIn("stage_b/batch_accounting.ndjson", output, output)
        self.assertNotIn("stage_a/batch_accounting.ndjson", output, output)
        self.assertIn("present and size-verified: 1", output)
        self.assertEqual(code, 1, output)

    def test_missing_header_reports_how_many_declarations_are_affected(
        self,
    ) -> None:
        """The blast radius of a loss is the number of receipts pointing at it,
        not the number of surviving report rows."""

        gone = str(self.root / "nowhere" / "recovered_failures.ndjson")
        for stage in ("s1", "s2", "s3"):
            write_json(self.root / stage / "receipt.json", {"path": gone})

        code, output = run_audit(self.root, self.workspace)
        self.assertEqual(missing_count(output), 1, output)
        self.assertIn("3 declarations", output, output)
        self.assertEqual(code, 1, output)

    # -- hash accounting --------------------------------------------------

    def _hash_fixture(self) -> None:
        body = b"z" * 2048
        write_bytes(self.root / "stage" / "queue.ndjson", body)
        write_json(
            self.root / "stage" / "receipt.json",
            {
                "path": "queue.ndjson",
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            },
        )

    def test_a_body_too_large_to_hash_is_reported_skipped_not_verified(
        self,
    ) -> None:
        """``category_review_queue/queue.ndjson`` is 202 MB and always exceeds
        ``--max-hash-mb``; counting it as "verified OK" overstated the audit."""

        self._hash_fixture()
        code, output = run_audit(
            self.root, self.workspace, ["--hash", "--max-hash-mb", "0.001"]
        )
        self.assertRegex(output, r"sha256 SKIPPED \(> 0\.001 MB\):\s+1")
        self.assertRegex(output, r"sha256 verified:\s+0")
        self.assertEqual(code, 0, output)

    def test_a_body_within_the_limit_is_actually_hash_verified(self) -> None:
        self._hash_fixture()
        code, output = run_audit(
            self.root, self.workspace, ["--hash", "--max-hash-mb", "64"]
        )
        self.assertRegex(output, r"sha256 verified:\s+1")
        self.assertRegex(output, r"sha256 SKIPPED \(> 64 MB\):\s+0")
        self.assertEqual(code, 0, output)

    def test_a_corrupt_body_within_the_limit_fails_the_gate(self) -> None:
        self._hash_fixture()
        write_bytes(self.root / "stage" / "queue.ndjson", b"y" * 2048)
        code, output = run_audit(
            self.root, self.workspace, ["--hash", "--max-hash-mb", "64"]
        )
        self.assertIn("=== HASH MISMATCH (1) ===", output)
        self.assertEqual(code, 1, output)

    def test_invalid_declaration_fields_are_flagged_not_fatal(self) -> None:
        """A receipt declaring an unhashable/ill-typed bytes or sha256 must not
        crash the audit; it is flagged and the other gaps are still reported."""

        write_bytes(self.root / "body.bin", b"abc")
        write_json(self.root / "bad1" / "receipt.json",
                   {"path": str(self.root / "body.bin"), "bytes": []})
        write_json(self.root / "bad2" / "receipt.json",
                   {"path": str(self.root / "body.bin"), "sha256": ["x"]})
        write_json(self.root / "bad3" / "receipt.json",
                   {"path": str(self.root / "body.bin"), "bytes": True})
        write_json(self.root / "good" / "receipt.json",
                   {"path": str(self.root / "body.bin"), "bytes": 3})
        write_json(self.root / "gone" / "receipt.json",
                   {"path": str(self.root / "nowhere" / "lost.ndjson")})
        code, output = run_audit(self.root, self.workspace)
        self.assertIn("=== INVALID DECLARATION (3) ===", output)
        self.assertEqual(missing_count(output), 1, output)
        self.assertIn("present and size-verified: 1", output)
        self.assertIn("=== CONFLICT (0) ===", output)
        self.assertEqual(code, 1, output)


if __name__ == "__main__":
    unittest.main()

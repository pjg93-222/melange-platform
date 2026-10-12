"""Unit tests for repin_staffer_live.py.

No network access, no process control: every test that would otherwise touch a
real PID, a real socket, or a real subprocess mocks the seam instead.  Only
filesystem fixtures under tempfile.TemporaryDirectory are used.
"""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from fec.surfaces import repin_staffer_live as rsl


REAL_SNAPSHOT_PATH = rsl.DEFAULT_SOURCE_ROOT / rsl.SNAPSHOT_MODULE_RELATIVE


class DetachedLauncherTests(unittest.TestCase):
    def test_detached_launcher_uses_files_not_pipes_and_retains_output(self):
        def run(cmd, **kwargs):
            self.assertNotIn('capture_output', kwargs)
            self.assertEqual(kwargs['timeout'], 180)
            self.assertGreaterEqual(kwargs['stdout'].fileno(), 0)
            kwargs['stdout'].write('Started hidden sidecar\n')
            kwargs['stderr'].write('diagnostic\n')
            return rsl.subprocess.CompletedProcess(cmd, 0)
        with patch.object(rsl.subprocess, 'run', side_effect=run):
            result = rsl.start_sidecar(Path('fixture'), 8841)
        self.assertEqual(result.stdout, 'Started hidden sidecar\n')
        self.assertEqual(result.stderr, 'diagnostic\n')

    def test_launcher_timeout_becomes_a_recordable_halt(self):
        with patch.object(rsl.subprocess, 'run', side_effect=rsl.subprocess.TimeoutExpired('fixture', 180)):
            with self.assertRaisesRegex(rsl.RepinHalt, 'launcher timed out'):
                rsl.start_sidecar(Path('fixture'), 8841)


class ConstantRewriteTests(unittest.TestCase):
    """(a) constant rewrite on a temp copy of staffer_graph_snapshot.py."""

    def setUp(self):
        if not REAL_SNAPSHOT_PATH.exists():
            self.skipTest(f"real snapshot module not found at {REAL_SNAPSHOT_PATH}")
        self.original_text = REAL_SNAPSHOT_PATH.read_text(encoding="utf-8")
        self.assertEqual(len(rsl.GENERATION_PATTERN.findall(self.original_text)), 1,
                          "fixture assumption: exactly one GENERATION line in the real file")
        self.assertEqual(len(rsl.VERIFICATION_PATTERN.findall(self.original_text)), 1,
                          "fixture assumption: exactly one VERIFICATION line in the real file")
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.tmp_root = Path(self.tempdir.name)

    def test_both_lines_change_and_rest_is_byte_identical(self):
        snapshot_copy = self.tmp_root / "staffer_graph_snapshot.py"
        snapshot_copy.write_text(self.original_text, encoding="utf-8")
        backup_path = self.tmp_root / "backup" / "staffer_graph_snapshot.py.TESTSTAMP.bak"

        new_generation = "staffer_social_network_20260904_v5_primary"
        new_verification = "staffer_social_network_20260904_v5_verification"
        info = rsl.repin_constants(snapshot_copy, new_generation, new_verification, backup_path)

        new_text = snapshot_copy.read_text(encoding="utf-8")
        self.assertIn(f'GENERATION = "{new_generation}"', new_text)
        self.assertIn(f'VERIFICATION = "{new_verification}"', new_text)
        self.assertEqual(info["generation_after"], f'GENERATION = "{new_generation}"')
        self.assertEqual(info["verification_after"], f'VERIFICATION = "{new_verification}"')

        # Backup is a byte-for-byte copy of the original.
        self.assertEqual(backup_path.read_text(encoding="utf-8"), self.original_text)

        # Substituting the two new lines back to the originals reproduces the
        # original file exactly -- proof that nothing else changed.
        reverted = rsl.GENERATION_PATTERN.sub(lambda _m: info["generation_before"], new_text, count=1)
        reverted = rsl.VERIFICATION_PATTERN.sub(lambda _m: info["verification_before"], reverted, count=1)
        self.assertEqual(reverted, self.original_text)

    def test_rewrite_and_rollback_preserve_mixed_newlines_byte_for_byte(self):
        path = self.tmp_root / "snapshot.py"
        backup = self.tmp_root / "snapshot.bak"
        original = ('# Café\r\nGENERATION = "old_primary"\r\n'
                    'VERIFICATION = "old_verification"\n# preserved tail\r\n').encode("utf-8")
        path.write_bytes(original)
        rsl.repin_constants(path, "new_primary", "new_verification", backup)
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(path.read_bytes(), original.replace(b'"old_primary"', b'"new_primary"')
                         .replace(b'"old_verification"', b'"new_verification"'))
        rsl.restore_backup(path, backup)
        self.assertEqual(path.read_bytes(), original)

    def test_duplicated_generation_line_is_refused(self):
        duplicated_text = self.original_text + '\nGENERATION = "duplicate_intruder"\n'
        snapshot_copy = self.tmp_root / "staffer_graph_snapshot.py"
        snapshot_copy.write_text(duplicated_text, encoding="utf-8")
        backup_path = self.tmp_root / "backup" / "staffer_graph_snapshot.py.TESTSTAMP.bak"

        with self.assertRaises(rsl.RepinHalt):
            rsl.repin_constants(snapshot_copy, "irrelevant_primary", "irrelevant_verification", backup_path)

        # Refused before touching anything: no backup, no rewrite.
        self.assertFalse(backup_path.exists())
        self.assertEqual(snapshot_copy.read_text(encoding="utf-8"), duplicated_text)


class ReceiptGateTests(unittest.TestCase):
    """(b) receipt gate: a fabricated PASS receipt passes, FAIL/missing halts."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.generation = "staffer_social_network_20260904_v5_primary"
        self.verification = "staffer_social_network_20260904_v5_verification"

    def _write_verification_receipt(self, payload):
        path = (self.root / rsl.STAFFER_NETWORK_RELATIVE / self.verification
                / rsl.VERIFICATION_RECEIPT_NAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_generation_receipt(self):
        path = self.root / rsl.STAFFER_NETWORK_RELATIVE / self.generation / rsl.GENERATION_RECEIPT_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        return path

    def _write_bound_verification(self):
        path = self._write_generation_receipt()
        digest = rsl.sha256_file(path)
        payload = {"status": "PASS", "failures": [], "twin_byte_identity": [{
            "path": rsl.GENERATION_RECEIPT_NAME, "identical": True,
            "primary_sha256": digest, "twin_sha256": digest}]}
        self._write_verification_receipt(payload)
        return payload, path

    def test_hash_bound_pass_receipt_passes(self):
        self._write_bound_verification()
        verification_path, data, generation_receipt_path = rsl.gate_receipts(
            self.root, self.generation, self.verification, wait=False, max_wait_hours=0)
        self.assertEqual(data["status"], "PASS")
        self.assertTrue(verification_path.exists())
        self.assertTrue(generation_receipt_path.exists())

    def test_unbound_pass_receipt_is_refused(self):
        self._write_verification_receipt({"status": "PASS", "failures": []})
        self._write_generation_receipt()
        with self.assertRaisesRegex(rsl.RepinHalt, "bind exactly one"):
            rsl.gate_receipts(self.root, self.generation, self.verification,
                             wait=False, max_wait_hours=0)

    def test_modified_generation_receipt_is_refused(self):
        _, path = self._write_bound_verification()
        path.write_bytes(b'{"different": true}')
        with self.assertRaisesRegex(rsl.RepinHalt, "hash differs"):
            rsl.gate_receipts(self.root, self.generation, self.verification,
                             wait=False, max_wait_hours=0)

    def test_invalid_twin_binding_is_refused(self):
        for mutation in ("duplicate", "different_twin", "not_identical", "malformed_digest", "missing_checks"):
            with self.subTest(mutation=mutation):
                payload, _ = self._write_bound_verification()
                check = payload["twin_byte_identity"][0]
                if mutation == "duplicate":
                    payload["twin_byte_identity"].append(dict(check))
                elif mutation == "different_twin":
                    check["twin_sha256"] = "0" * 64
                elif mutation == "not_identical":
                    check["identical"] = False
                elif mutation == "malformed_digest":
                    check["primary_sha256"] = check["twin_sha256"] = "invalid"
                else:
                    payload["twin_byte_identity"] = None
                self._write_verification_receipt(payload)
                with self.assertRaises(rsl.RepinHalt):
                    rsl.gate_receipts(self.root, self.generation, self.verification,
                                     wait=False, max_wait_hours=0)

    def test_fail_status_halts(self):
        self._write_verification_receipt({"status": "FAIL", "failures": ["some check"]})
        self._write_generation_receipt()
        with self.assertRaises(rsl.RepinHalt):
            rsl.gate_receipts(self.root, self.generation, self.verification,
                               wait=False, max_wait_hours=0)

    def test_pass_status_with_nonempty_failures_halts(self):
        # Mirrors StaticGraph._load(): status == "PASS" but failures truthy must still fail closed.
        self._write_verification_receipt({"status": "PASS", "failures": ["late-discovered issue"]})
        self._write_generation_receipt()
        with self.assertRaises(rsl.RepinHalt):
            rsl.gate_receipts(self.root, self.generation, self.verification,
                               wait=False, max_wait_hours=0)

    def test_missing_verification_receipt_halts(self):
        self._write_generation_receipt()
        with self.assertRaises(rsl.RepinHalt):
            rsl.gate_receipts(self.root, self.generation, self.verification,
                               wait=False, max_wait_hours=0)

    def test_missing_generation_receipt_halts_even_with_passing_verification(self):
        self._write_verification_receipt({"status": "PASS", "failures": []})
        with self.assertRaises(rsl.RepinHalt):
            rsl.gate_receipts(self.root, self.generation, self.verification,
                               wait=False, max_wait_hours=0)


class VerificationDerivationTests(unittest.TestCase):
    """(c) verification default derivation '_primary' -> '_verification'."""

    def test_primary_suffix_replaced(self):
        self.assertEqual(
            rsl.derive_verification("staffer_social_network_20260904_v5_primary"),
            "staffer_social_network_20260904_v5_verification",
        )

    def test_default_generation_derives_expected_default_verification(self):
        self.assertEqual(
            rsl.derive_verification(rsl.DEFAULT_GENERATION),
            "staffer_social_network_20260907_v6_verification",
        )

    def test_argparse_default_verification_is_none_until_derived_in_main(self):
        args = rsl.build_argparser().parse_args([])
        self.assertIsNone(args.verification)
        self.assertEqual(args.generation, rsl.DEFAULT_GENERATION)


class PidCommandLineGuardTests(unittest.TestCase):
    """(d) the PID command-line guard refuses a non-matching command line."""

    def test_matching_command_line_is_accepted(self):
        def fake_lookup(pid):
            return f'"C:\\python.exe" "C:\\...\\{rsl.SERVER_PROCESS_MARKER}" --source-root X --port 8841'

        cmdline = rsl.confirm_pid_is_sidecar(31784, lookup=fake_lookup)
        self.assertIn(rsl.SERVER_PROCESS_MARKER, cmdline)

    def test_non_matching_command_line_is_refused(self):
        def fake_lookup(pid):
            return 'C:\\python.exe some_other_script.py'

        with self.assertRaises(rsl.RepinHalt) as ctx:
            rsl.confirm_pid_is_sidecar(31784, lookup=fake_lookup)
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_lookup_failure_is_refused(self):
        def fake_lookup(pid):
            return None

        with self.assertRaises(rsl.RepinHalt) as ctx:
            rsl.confirm_pid_is_sidecar(99999, lookup=fake_lookup)
        self.assertEqual(ctx.exception.exit_code, 3)


class LivePinTests(unittest.TestCase):
    def test_clean_exact_generation_receipt_is_required(self):
        generation = "example_v5_primary"
        for flavor in ("valid", "suffix", "wrong_role", "errors", "unverified", "bad_status"):
            with self.subTest(flavor=flavor):
                source = {"role": "graph_receipt", "verified": True,
                          "path": "C:\\audit\\" + generation + "\\" + rsl.GENERATION_RECEIPT_NAME}
                live = {"ready": True, "verification_status": "PASS", "errors": [], "sources": [source]}
                if flavor == "suffix":
                    source["path"] = source["path"].replace(generation, generation + "_failed")
                elif flavor == "wrong_role":
                    source["role"] = "some_other_artifact"
                elif flavor == "errors":
                    live["errors"] = ["source load failed"]
                elif flavor == "unverified":
                    source["verified"] = False
                elif flavor == "bad_status":
                    live["verification_status"] = "FAIL"
                with patch.object(rsl, "wait_for_live_ready", return_value=live):
                    if flavor == "valid":
                        self.assertIs(rsl.verify_live_pin(8841, generation), live)
                    else:
                        with self.assertRaises(rsl.RepinHalt):
                            rsl.verify_live_pin(8841, generation)


class DryRunTests(unittest.TestCase):
    """(e) dry run of the real module against the live tree: no exceptions, nothing executed."""

    def test_dry_run_against_live_tree_executes_nothing(self):
        backups_dir = (rsl.DEFAULT_SOURCE_ROOT / rsl.RUNTIME_RELATIVE / "backups"
                        / "fec" / "surfaces")
        before = set(backups_dir.glob("staffer_graph_snapshot.py.*.bak")) if backups_dir.exists() else set()

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = rsl.main([])

        self.assertEqual(result, 0)
        output = buffer.getvalue()
        self.assertIn("DRY RUN", output)
        self.assertIn("Nothing above was executed.", output)

        after = set(backups_dir.glob("staffer_graph_snapshot.py.*.bak")) if backups_dir.exists() else set()
        self.assertEqual(before, after, "dry run must not create any repin backup file")


if __name__ == "__main__":
    unittest.main()

"""Gates with fake locks and temp repos: every one fails closed and none keeps a lock."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from ops.tests.test_release_common import Base, Fixture, REAL_LIVE, g, write_invited
from ops.release_lib import core, gates


@unittest.skipUnless((REAL_LIVE / "fec" / "pipeline_lock.py").is_file(), "live tree (read-only source of pipeline_lock) absent")
class LockGateTests(Base):
    def setUp(self):
        self.live = self.tmpdir()
        (self.live / "fec").mkdir()
        for name in ("pipeline_lock.py", "process_liveness.py", "paths.py"):
            (self.live / "fec" / name).write_bytes((REAL_LIVE / "fec" / name).read_bytes())

    def lock(self, payload) -> Path:
        path = self.live / ".pipeline_run.lock"
        path.write_text(json.dumps(payload) if isinstance(payload, dict) else payload)
        return path

    def test_no_lock_file_is_free(self):
        self.assertTrue(gates.pipeline_lock(self.live)["ok"])

    def test_dead_pid_is_free(self):
        done = subprocess.Popen([sys.executable, "-c", "pass"])
        done.wait()
        self.lock({"pid": done.pid, "token": "t", "owner_kind": "pipeline"})
        result = gates.pipeline_lock(self.live)
        self.assertTrue(result["ok"], result)

    def test_own_pid_is_held(self):
        self.lock({"pid": os.getpid(), "token": "t", "owner_kind": "release"})
        result = gates.pipeline_lock(self.live)
        self.assertFalse(result["ok"])
        self.assertIn(str(os.getpid()), result["detail"])

    def test_freshly_created_empty_lock_counts_as_held(self):
        self.lock("")                                  # a peer is mid-creation: do not guess it away
        self.assertFalse(gates.pipeline_lock(self.live)["ok"])


class WeeklyLockTests(Base):
    def test_free_when_absent_or_unlocked_and_probe_keeps_nothing(self):
        live = self.tmpdir()
        self.assertTrue(gates.weekly_lock(live)["ok"])
        (live / ".weekly_refresh.lock").write_bytes(b"0")
        self.assertTrue(gates.weekly_lock(live)["ok"])
        self.assertTrue(gates.weekly_lock(live)["ok"])         # the first probe did not leave it locked

    def test_held_when_another_handle_owns_the_byte(self):
        import msvcrt
        live = self.tmpdir()
        path = live / ".weekly_refresh.lock"
        path.write_bytes(b"0")
        with open(path, "a+b") as owner:
            owner.seek(0)
            msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
            held = gates.weekly_lock(live)
            owner.seek(0)
            msvcrt.locking(owner.fileno(), msvcrt.LK_UNLCK, 1)
        self.assertFalse(held["ok"])
        self.assertTrue(gates.weekly_lock(live)["ok"])


class LaneWriterTests(Base):
    def test_free_then_busy_while_a_writer_holds_its_lock_file(self):
        run_dir = self.tmpdir() / "20261012T000000-1"
        lane = run_dir / "lanes" / "lane-01"
        lane.mkdir(parents=True)
        lock_file = lane / "mentat.db.writer-lock.sqlite"
        writer = sqlite3.connect(lock_file, isolation_level=None)
        writer.execute("CREATE TABLE t(x)")
        pattern = str(run_dir.parent / "*")
        self.assertTrue(gates.mentat_lane_writers([pattern])["ok"])
        writer.execute("BEGIN IMMEDIATE")
        busy = gates.mentat_lane_writers([pattern])
        writer.rollback()
        writer.close()
        self.assertFalse(busy["ok"])
        self.assertIn("20261012T000000-1", busy["detail"])
        self.assertTrue(gates.mentat_lane_writers([pattern])["ok"])

    def test_no_run_directories_means_no_writers(self):
        self.assertTrue(gates.mentat_lane_writers([str(self.tmpdir() / "api-fanout" / "*")])["ok"])


class RepoGateTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture(with_liveness=False, eol_attrs=False)

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def test_clean_repos_with_head_on_the_ref_pass(self):
        result = gates.repos_clean(self.fx.cfg)
        self.assertTrue(result["ok"], result)

    def test_dirty_allowlisted_path_fails_but_dirty_data_does_not(self):
        data_file = self.fx.mentat / "mentat" / "state" / "scratch.json"      # excluded data tree: ignored
        data_file.write_text("{}")
        local = self.fx.terrier / ".local"
        local.mkdir(exist_ok=True)
        (local / "scores.sqlite3").write_bytes(b"x")                          # Terrier data tree: ignored
        try:
            self.assertTrue(gates.repos_clean(self.fx.cfg)["ok"])
            (self.fx.melange / "fec" / "lf.py").write_bytes(b"print('dirty')\n")
            result = gates.repos_clean(self.fx.cfg)
            self.assertFalse(result["ok"])
            self.assertIn("melange", result["detail"])
        finally:
            g("checkout", "--", "fec/lf.py", cwd=self.fx.melange)
            data_file.unlink()
            (local / "scores.sqlite3").unlink()

    def test_untracked_file_inside_an_allowlisted_path_fails(self):
        extra = self.fx.melange / "fec" / "new_module.py"
        extra.write_text("x = 1\n")
        try:
            self.assertFalse(gates.repos_clean(self.fx.cfg)["ok"])
        finally:
            extra.unlink()

    def test_head_off_the_released_ref_fails(self):
        g("checkout", "-q", "-b", "side", cwd=self.fx.terrier)
        (self.fx.terrier / "README.md").write_text("changed\n")
        g("commit", "-qam", "side", cwd=self.fx.terrier)
        try:
            result = gates.repos_clean(self.fx.cfg)
            self.assertFalse(result["ok"])
            self.assertIn("terrier", result["detail"])
        finally:
            g("checkout", "-q", "claude/walk-list", cwd=self.fx.terrier)


class InvitedBuildGateTests(Base):
    """D-164/D-169/R3: only a ruled, untampered, current invited build may be released."""

    def setUp(self):
        self.live = self.tmpdir() / "live"
        self.live.mkdir()
        self.fx = write_invited(self.live)
        self.cfg = {"melange_live": str(self.live)}

    def doc(self, **change):
        path = self.fx["invited"] / "INVITED_BUILD.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        for key, value in change.items():
            doc[key] = value
        path.write_text(json.dumps(doc), encoding="utf-8")

    def test_a_ruled_current_build_passes_and_names_its_governance_and_profile(self):
        result = gates.invited_build(self.cfg)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["governance"], result["profile_sha256"]), ("ruled", "d" * 64))
        self.assertNotIn("governance_override", result)

    def test_a_pending_build_is_refused_and_the_override_is_recorded_not_silent(self):
        self.doc(governance="pending")
        refused = gates.invited_build(self.cfg)
        self.assertFalse(refused["ok"])
        self.assertIn("pending", refused["detail"])
        allowed = gates.invited_build(self.cfg, override=True)
        self.assertTrue(allowed["ok"], allowed)
        self.assertTrue(allowed["governance_override"])
        self.assertEqual(allowed["governance"], "pending")                      # the snapshot manifest carries the truth

    def test_the_override_forgives_governance_only_never_tampering_or_staleness(self):
        self.doc(governance="pending")
        (self.fx["invited"] / "Moneyball.html").write_bytes(b"<html>edited</html>")
        self.assertFalse(gates.invited_build(self.cfg, override=True)["ok"])

    def test_a_tampered_page_or_gz_is_refused(self):
        for name in ("Moneyball.html", "Moneyball.html.gz"):
            path = self.fx["invited"] / name
            before = path.read_bytes()
            path.write_bytes(before + b" ")
            result = gates.invited_build(self.cfg)
            self.assertFalse(result["ok"], name)
            self.assertIn(name, result["detail"])
            path.write_bytes(before)
        self.assertTrue(gates.invited_build(self.cfg)["ok"])

    def test_a_page_that_is_not_in_the_manifest_is_refused(self):
        (self.fx["invited"] / "lobby-interactions.html").write_text("<html>a page the profile dropped</html>")
        result = gates.invited_build(self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("lobby-interactions.html", result["detail"])

    def test_a_stale_build_is_refused(self):
        (self.live / "Moneyball.html").write_bytes(b"<html>the desk was rebuilt after the invited build</html>")
        result = gates.invited_build(self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("rebuild", result["detail"])

    def test_a_missing_or_malformed_manifest_is_refused(self):
        (self.fx["invited"] / "INVITED_BUILD.json").write_text("{broken")
        self.assertFalse(gates.invited_build(self.cfg)["ok"])
        (self.fx["invited"] / "INVITED_BUILD.json").unlink()
        self.assertFalse(gates.invited_build(self.cfg)["ok"])

    def test_it_runs_with_the_other_gates(self):
        cfg = dict(core.load_config(), melange_live=str(self.live), mentat_live=str(self.tmpdir()), terrier_checkout=str(self.tmpdir()))
        self.assertTrue(gates.run_gates(cfg, skip_kernel=True, netlify=False)["invited_build"]["ok"])
        self.doc(governance="pending")
        self.assertFalse(gates.run_gates(cfg, skip_kernel=True, netlify=False)["invited_build"]["ok"])
        self.assertTrue(gates.run_gates(cfg, skip_kernel=True, netlify=False, governance_override=True)["invited_build"]["governance_override"])


class KernelAndStatusGateTests(Base):
    def stub_runner(self, body: str) -> Path:
        live = self.tmpdir()
        (live / "fec" / "tests").mkdir(parents=True)
        (live / "fec" / "tests" / "run_tests.py").write_text(body, encoding="utf-8")
        return live

    def test_requires_a_zero_failed_final_line(self):
        ok = self.stub_runner("print('noise')\nprint('\\nKERNEL TESTS — 12 passed, 0 FAILED')\n")
        self.assertTrue(gates.kernel_tests(ok)["ok"])
        failed = self.stub_runner("print('KERNEL TESTS — 12 passed, 1 FAILED')\nraise SystemExit(1)\n")
        self.assertFalse(gates.kernel_tests(failed)["ok"])
        silent = self.stub_runner("print('all good')\n")
        self.assertFalse(gates.kernel_tests(silent)["ok"])
        zero = self.stub_runner("print('KERNEL TESTS — 0 passed, 0 FAILED')\n")
        self.assertFalse(gates.kernel_tests(zero)["ok"])
        crashed = self.stub_runner("print('KERNEL TESTS — 5 passed, 0 FAILED')\nraise SystemExit(3)\n")
        self.assertFalse(gates.kernel_tests(crashed)["ok"])

    def test_skip_is_recorded_not_hidden(self):
        result = gates.kernel_tests(self.tmpdir(), skip=True)
        self.assertTrue(result["ok"] and result["skipped"])
        self.assertIn("--skip-kernel", result["detail"])

    def test_weekly_outcome(self):
        live = self.tmpdir()
        status = live / "data" / "engine" / "weekly_refresh_status.json"
        self.assertFalse(gates.weekly_outcome(live)["ok"])                       # missing
        status.parent.mkdir(parents=True)
        for outcome, expected in (("COMPLETE", True), ("COMPLETE_WITH_KNOWN_GATED_FAILURES", True),
                                  ("HARD_FAILURE", False), ("RUNNING", False), (None, False)):
            status.write_text(json.dumps({"last_outcome": outcome}))
            self.assertEqual(gates.weekly_outcome(live)["ok"], expected, outcome)
        status.write_text("{broken")
        self.assertFalse(gates.weekly_outcome(live)["ok"])

    def test_weekly_gate_names_the_tolerated_failures_it_lets_through(self):
        live = self.tmpdir()
        status = live / "data" / "engine" / "weekly_refresh_status.json"
        status.parent.mkdir(parents=True)
        tolerated = ["check_alpha", "check_beta"]
        status.write_text(json.dumps({"last_outcome": "COMPLETE_WITH_KNOWN_GATED_FAILURES", "failures": tolerated}))
        result = gates.weekly_outcome(live)
        self.assertTrue(result["ok"])
        self.assertIn("tolerated (2): check_alpha, check_beta", result["detail"])
        self.assertEqual(result["tolerated"], tolerated)                          # recorded in the manifest's gates
        status.write_text(json.dumps({"last_outcome": "COMPLETE", "failures": []}))
        self.assertEqual(gates.weekly_outcome(live)["detail"], "last_outcome COMPLETE")
        status.write_text(json.dumps({"last_outcome": "HARD_FAILURE", "failures": ["press delta refresh"]}))
        hard = gates.weekly_outcome(live)
        self.assertFalse(hard["ok"])
        self.assertIn("failures (1): press delta refresh", hard["detail"])
        status.write_text(json.dumps({"last_outcome": "COMPLETE_WITH_KNOWN_GATED_FAILURES", "failures": [f"c{i}" for i in range(20)]}))
        self.assertIn("(+8 more)", gates.weekly_outcome(live)["detail"])
        self.assertEqual(len(gates.weekly_outcome(live)["tolerated"]), 20)

    def test_receipt_hash_must_equal_deploy_index(self):
        live = self.tmpdir()
        (live / "deploy").mkdir()
        (live / "data" / "engine").mkdir(parents=True)
        index, receipt = live / "deploy" / "index.html", live / "data" / "engine" / "dashboard_deployment_receipt.json"
        index.write_bytes(b"locked bytes")
        self.assertFalse(gates.receipt_matches_deploy(live)["ok"])               # no receipt
        receipt.write_text(json.dumps({"local_deploy_artifact": {"sha256": core.sha256_bytes(b"locked bytes")}}))
        self.assertTrue(gates.receipt_matches_deploy(live)["ok"])
        index.write_bytes(b"rebuilt after the receipt")
        self.assertFalse(gates.receipt_matches_deploy(live)["ok"])

    def test_census_pending_blocks_the_data_push_unless_overridden_and_says_so(self):
        self.assertTrue(gates.census({"census_pending": False})["ok"])
        self.assertTrue(gates.census({})["ok"])
        blocked = gates.census({"census_pending": True})
        self.assertFalse(blocked["ok"])
        self.assertIn("--census-override", blocked["detail"])
        allowed = gates.census({"census_pending": True}, override=True)
        self.assertTrue(allowed["ok"] and allowed["census_override"])
        cfg = dict(core.load_config(), melange_live=str(self.tmpdir() / "nowhere"), mentat_live=str(self.tmpdir()),
                   terrier_checkout=str(self.tmpdir()))
        self.assertNotIn("census", gates.run_gates(cfg, skip_kernel=True, netlify=False))               # no serve sets handed in
        self.assertFalse(gates.run_gates(cfg, skip_kernel=True, netlify=False, serve_sets={"census_pending": True})["census"]["ok"])

    def test_disk_gate_needs_headroom(self):
        self.assertTrue(gates.disk_free(self.tmpdir(), 0)["ok"] or core.free_bytes(self.tmpdir()) < gates.DISK_HEADROOM)
        self.assertFalse(gates.disk_free(self.tmpdir(), 1 << 60)["ok"])

    def test_security_gate_absent_fails_closed_and_exit_code_decides(self):
        ops = self.tmpdir()
        target = self.tmpdir()
        self.assertFalse(gates.security_gate(ops, target)["ok"])
        (ops / "security_gate.py").write_text("import sys; print('export', sys.argv[1:]); sys.exit(0)\n")
        self.assertTrue(gates.security_gate(ops, target)["ok"])
        (ops / "security_gate.py").write_text("import sys; print('canary found'); sys.exit(2)\n")
        result = gates.security_gate(ops, target)
        self.assertFalse(result["ok"])
        self.assertIn("canary found", result["detail"])

    def test_security_gate_runs_the_named_stage_over_every_target(self):
        ops = self.tmpdir()
        (ops / "security_gate.py").write_text("import sys; print('FAIL x/y: boom' if 'bad' in sys.argv[-1] else 'ok', sys.argv[1:]); "
                                              "sys.exit(1 if 'bad' in sys.argv[-1] else 0)\n")
        one, two, bad = self.tmpdir(), self.tmpdir(), self.tmpdir() / "bad"
        bad.mkdir()
        good = gates.security_gate(ops, one, "packages", (two,))
        self.assertTrue(good["ok"], good)
        self.assertIn("'packages', '--target'", good["detail"])
        self.assertEqual(good["detail"].count("--target"), 2)
        refused = gates.security_gate(ops, one, "packages", (bad,))
        self.assertFalse(refused["ok"])
        self.assertIn("FAIL x/y: boom", refused["detail"])                      # the failing check, not just the last line

    def test_security_gate_passes_the_scan_cache_only_when_given_one(self):
        ops = self.tmpdir()
        (ops / "security_gate.py").write_text("import sys, os; print('ok', sys.argv[-2], os.path.basename(sys.argv[-1]))")
        target, cache = self.tmpdir(), self.tmpdir() / "scan_cache.sqlite"
        self.assertNotIn("--scan-cache", gates.security_gate(ops, target, "farm")["detail"])
        self.assertIn("ok --scan-cache scan_cache.sqlite", gates.security_gate(ops, target, "farm", scan_cache=cache)["detail"])

    def test_a_gate_that_raises_fails_instead_of_passing(self):
        cfg = dict(core.load_config(), melange_live=str(self.tmpdir() / "nowhere"), mentat_live=str(self.tmpdir()),
                   terrier_checkout=str(self.tmpdir()))
        results = gates.run_gates(cfg, skip_kernel=True, netlify=False)
        self.assertFalse(gates.all_ok(results))
        self.assertFalse(results["repos_clean"]["ok"])
        self.assertTrue(results["receipt_hash"]["skipped"])
        self.assertTrue(results["kernel_tests"]["skipped"])


if __name__ == "__main__":
    unittest.main()

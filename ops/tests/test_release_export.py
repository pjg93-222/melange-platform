"""Export: byte-faithful, deterministic, allowlisted, proof-checked, never forced. Temp repos only."""
from __future__ import annotations

import unittest
from unittest import mock

from ops.tests.test_release_common import Base, Fixture, MELANGE_FILES, g, rmtree
from ops.release_lib import core, export, gates
from ops import release


def ctx_for(cfg):
    return release.Ctx(cfg, emit=core.noop_emit)


class GitHooksTests(Base):
    """core.git() runs git with core.hooksPath= so no hook in a source repo or the bare repo ever runs under the release."""

    def test_no_hook_runs_under_the_release_git_wrapper_but_one_runs_under_plain_git(self):
        repo, marker = self.tmpdir() / "repo", self.tmpdir() / "marker.txt"
        repo.mkdir()
        g("init", "-q", "-b", "main", cwd=repo)
        for hook in ("pre-commit", "pre-push", "reference-transaction"):
            path = repo / ".git" / "hooks" / hook
            path.write_bytes(f'#!/bin/sh\necho {hook} >> "{marker.as_posix()}"\nexit 0\n'.encode())
            path.chmod(0o755)
        core.git("commit", "-q", "--allow-empty", "-m", "x", cwd=repo, env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                                                           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        head = core.git_text("rev-parse", "HEAD", cwd=repo)
        core.git("update-ref", "refs/tags/v1", head, cwd=repo)
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else "")
        g("update-ref", "refs/tags/v2", head, cwd=repo)                          # the same repo, plain git: the hook is real
        self.assertIn("reference-transaction", marker.read_text())


class ExportConfigTests(Base):
    def test_exactly_the_eleven_research_dependent_tests_are_excluded(self):
        research_tests = export.MELANGE_RESEARCH_TESTS
        self.assertEqual(len(research_tests), 11)
        # PR #14 round 3: six fec/tests/research files that import or read fec/research/.
        for name in ("exchange_context_audit_checks", "fetch_demographics_guards", "fetch_hearing_pilot_provenance",
                     "mine_anchors_matcher", "refinement_common_write_json", "refinement_report_guards"):
            self.assertIn(f"fec/tests/research/test_{name}.py", research_tests, name)
        expected = {                                                    # synthetic paths -> disposition
            **{path: "excluded path" for path in research_tests},
            "fec/research/cosponsor_universe/universe_gold_cohort.py": "excluded path",   # the research rule itself
            "fec/tests/infra/test_cohort_output_bindings_extra.py": "exported",
            "fec/tests/infra/test_other_guard.py": "exported",
            "fec/tests/research/test_kept_suite.py": "exported",
            "fec/tests/surfaces/test_lobby_sector_state_extra.cjs": "exported",
            "fec/tests/research/test_research_io_regression.py.bak": "extension not allowed",
        }
        with mock.patch.dict(MELANGE_FILES, {path: b"# synthetic test\n" for path in expected}):
            fixture = Fixture(with_liveness=False, big=False)
        try:
            plan = export.plan_export(fixture.cfg)
            dropped = dict(plan["dropped"])
            for path, disposition in expected.items():
                if disposition == "exported":
                    self.assertIn(path, plan["entries"], path)
                    self.assertNotIn(path, dropped, path)
                else:
                    self.assertNotIn(path, plan["entries"], path)
                    self.assertIn(path, dropped, path)
                    self.assertEqual(dropped[path], disposition, path)
        finally:
            fixture.close()
        # The pattern itself: exact paths only (anchored, dots escaped), research rule unchanged.
        for path in research_tests:
            self.assertTrue(export.MELANGE_EXCLUDE.match(path), path)
        for path in ("x/" + research_tests[0], research_tests[0] + "c", research_tests[0][:-3] + "Xpy",
                     "fec/tests/infra/test_cohort_output_bindings_extra.py", "fec/tests/research/test_kept_suite.py",
                     "fec/tests/surfaces/test_staffer_live_server.py"):
            self.assertIsNone(export.MELANGE_EXCLUDE.match(path), path)
        self.assertTrue(export.MELANGE_EXCLUDE.match("fec/research/anything.py"))

    def test_the_local_artifact_workspace_test_is_excluded_by_exact_path(self):
        self.assertEqual(export.MELANGE_LOCAL_ARTIFACT_TESTS, ("fec/tests/surfaces/test_original_workspace.py",
                                                               "fec/tests/collect/test_cspan_chromote_retry.py",
                                                               "fec/tests/collect/test_cspan_chromote_retry.R"))
        for path in export.MELANGE_LOCAL_ARTIFACT_TESTS:
            self.assertTrue(export.MELANGE_EXCLUDE.match(path), path)
        for path in ("fec/tests/surfaces/test_original_workspace.pyc", "fec/tests/surfaces/test_original_workspace_extra.py",
                     "fec/tests/surfaces/test_clean_workspace.cjs", "x/fec/tests/surfaces/test_original_workspace.py",
                     "fec/tests/collect/test_cspan_chromote_retry.pyc", "fec/tests/collect/test_cspan_chromote_retry_extra.py",
                     "fec/tests/collect/test_refresh_cspan_wj_errors.py", "x/fec/tests/collect/test_cspan_chromote_retry.R"):
            self.assertIsNone(export.MELANGE_EXCLUDE.match(path), path)

    def test_the_shipped_config_keeps_the_r_lock_that_deps_yml_reads(self):
        shipped = core.load_config()["export"]
        self.assertTrue(export.allowed_name("ops/locks/r-packages.txt", shipped))
        self.assertFalse(export.allowed_name("ops/locks/r-packages.txt", {}))             # only extra_names lets a .txt out

    def test_desktop_installer_hook_survives_export_without_admitting_other_installer_scripts(self):
        hook = "ops/desktop/client/installer.nsh"
        omitted = ("ops/other/installer.nsh", "ops/desktop/client/unreviewed.nsh",
                   "ops/desktop/packaging/native-build.yml.template")
        contents = b"!macro customInit\r\n!macroend\r\n"
        with mock.patch.dict(MELANGE_FILES, {hook: contents, **{path: b"not a release input\n" for path in omitted}}):
            fixture = Fixture(with_liveness=False, big=False)
        try:
            plan = export.plan_export(fixture.cfg)
            self.assertIn(hook, plan["entries"])
            self.assertEqual(plan["entries"][hook][1], g("rev-parse", "HEAD:" + hook, cwd=fixture.melange))
            dropped = dict(plan["dropped"])
            for path in omitted:
                self.assertNotIn(path, plan["entries"])
                self.assertEqual(dropped[path], "extension not allowed")
        finally:
            fixture.close()


class ExportTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()                      # built once: every test gets its own bare repo, the sources stay read-only

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def setUp(self):
        self.cfg = dict(self.fx.cfg, platform_bare=str(self.fx.tmp / f"{self._testMethodName}.git"))

    def build(self, bare_name="platform.git", parent=None):
        cfg = dict(self.cfg, platform_bare=str(self.fx.tmp / bare_name))
        bare = export.ensure_bare(cfg["platform_bare"], cfg["platform_repo"], cfg["platform_remote_url"])
        plan = export.plan_export(cfg)
        commit, tree = export.build_commit(bare, plan, cfg, parent=parent, date_unix=1_700_000_000, message="Release t\n")
        return bare, plan, commit, tree

    def test_expected_tree_allowlist_and_remap(self):
        plan = export.plan_export(self.cfg)
        paths = set(plan["entries"])
        for kept in ("fec/crlf.py", "fec/lf.py", "fec/logo.png", "fec/sub/module.js", "requirements.txt", "ops/tool.py",
                     ".github/workflows/release.yml", ".gitattributes", "mentat/app/server.py", "mentat/tools/t.R",
                     "mentat/README.md", "ops/vps/allowed_signers",
                     "mentat/inputs/fec_receipt_rules.json", "mentat/inputs/fec_transaction_types.json",     # rule files Mentat CI needs
                     "mentat/tests/fixtures/other_cases.json"):
            self.assertIn(kept, paths)
        for gone in ("fec/big.json", "fec/data.csv", "fec/_quarantine_old/q.py", "fec/attic/a.py", "fec/research/r.py",
                     "fec/pas224.zip", "outside/x.py", "ops/github/workflows/release.yml", "ops/export.gitattributes",
                     "mentat/inputs/in.py", "mentat/state/s.json", "mentat/audits/a.md", "mentat/.cache/c.json",
                     "mentat/tools/pocket/mentat-pocket.html",      # Pocket template: stale baked-in data snapshot
                     "mentat/R-packages.lock.tsv",                  # local paths; the security gate denies .tsv
                     "fec/docs/NOTES.md", "fec/docs/audit/findings.json",              # working notes, not source
                     "mentat/inputs/fec_other.json",                                   # inputs are data except the two rule files
                     "mentat/tests/fixtures/memo_redisclosure_cases.json"):            # real contributor names
            self.assertNotIn(gone, paths)
        reasons = dict(plan["dropped"])
        self.assertIn("larger than 5 MB", reasons["fec/big.json"])
        self.assertEqual(reasons["fec/data.csv"], "extension not allowed")
        self.assertEqual(reasons["mentat/R-packages.lock.tsv"], "extension not allowed")
        for excluded in ("fec/docs/NOTES.md", "mentat/inputs/fec_other.json", "mentat/tests/fixtures/memo_redisclosure_cases.json"):
            self.assertEqual(reasons[excluded], "excluded path", excluded)
        self.assertEqual(reasons["fec/attic/a.py"], "excluded path")
        self.assertEqual(reasons["mentat/tools/pocket/mentat-pocket.html"], "excluded path")

    def test_blob_ids_equal_source_and_bytes_survive_crlf_autocrlf(self):
        bare, plan, commit, _tree = self.build()
        want = {"fec/crlf.py": g("rev-parse", "HEAD:fec/crlf.py", cwd=self.fx.melange),
                "mentat/app/server.py": g("rev-parse", "HEAD:mentat/app/server.py", cwd=self.fx.mentat)}
        got = export.ls_tree(None, commit, git_dir=bare)
        for path, oid in want.items():
            self.assertEqual(got[path][1], oid)
        out = export.materialize(bare, plan, self.fx.tmp / "scan")
        self.assertEqual((out / "fec" / "crlf.py").read_bytes(), b"print('a')\r\nprint('b')\r\n")      # CRLF kept
        self.assertEqual((out / "fec" / "lf.py").read_bytes(), b"print('lf')\n")                          # LF kept
        self.assertEqual((out / "fec" / "logo.png").read_bytes(), bytes(range(256)) * 4)
        self.assertEqual((out / ".gitattributes").read_bytes(), b"* -text\n")
        export.prove(bare, commit, plan)

    def test_export_twice_gives_the_same_commit_even_in_a_fresh_repo(self):
        _b1, _p1, first, tree1 = self.build("one.git")
        _b2, _p2, second, tree2 = self.build("two.git")
        again = export.build_commit(_b1, _p1, self.cfg, parent=None, date_unix=1_700_000_000, message="Release t\n")
        self.assertEqual((first, tree1), (second, tree2))
        self.assertEqual(first, again[0])
        other_date = export.build_commit(_b1, _p1, self.cfg, parent=None, date_unix=1_700_000_001, message="Release t\n")[0]
        self.assertNotEqual(first, other_date)                                    # the date is an input, nothing else is

    def test_bare_repo_has_autocrlf_off_and_no_hooks(self):
        bare = export.ensure_bare(self.fx.tmp / "x.git", "pjg93-222/melange-platform", self.cfg["platform_remote_url"])
        self.assertEqual(g("config", "core.autocrlf", cwd=bare), "false")
        self.assertFalse((bare / "hooks").exists() and any((bare / "hooks").iterdir()))

    def test_proof_failure_leaves_refs_untouched(self):
        ctx = ctx_for(self.cfg)
        st = core.State(self.fx.tmp / "state.json")
        st.update(commit_unix=1_700_000_000, gates={})
        with mock.patch.object(export, "prove", side_effect=core.ReleaseError("PROOF_FAILED", "forced")), \
                mock.patch.object(gates, "security_gate", return_value={"ok": True, "detail": "stub"}):
            with self.assertRaises(core.ReleaseError) as raised:
                release.step_export(ctx, st, "2026.10.12.1")
        self.assertEqual(raised.exception.code, "PROOF_FAILED")
        bare = self.cfg["platform_bare"]
        self.assertIsNone(export.rev(bare, "refs/heads/main"))
        self.assertIsNone(export.rev(bare, "refs/tags/v2026.10.12.1"))

    def test_ref_update_that_lands_on_the_wrong_commit_is_rolled_back(self):
        ctx = ctx_for(self.cfg)
        st = core.State(self.fx.tmp / "state.json")
        st.update(commit_unix=1_700_000_000, gates={})
        bare = export.ensure_bare(self.cfg["platform_bare"], self.cfg["platform_repo"], self.cfg["platform_remote_url"])
        decoy = export.build_commit(bare, export.plan_export(self.cfg), self.cfg, parent=None, date_unix=1_700_000_005, message="decoy\n")[0]
        real_set = export.set_ref
        def wrong(bare_, ref, new, old):           # a racing writer moves main somewhere else
            real_set(bare_, ref, decoy, old)
        with mock.patch.object(export, "set_ref", wrong), mock.patch.object(gates, "security_gate", return_value={"ok": True, "detail": "stub"}):
            with self.assertRaises(core.ReleaseError) as raised:
                release.step_export(ctx, st, "2026.10.12.1")
        self.assertEqual(raised.exception.code, "REF_UPDATE")
        self.assertIsNone(export.rev(bare, "refs/heads/main"))

    def test_security_gate_failure_stops_before_any_ref_moves_and_absent_gate_fails_closed(self):
        ctx = ctx_for(self.cfg)
        st = core.State(self.fx.tmp / "state.json")
        st.update(commit_unix=1_700_000_000, gates={})
        with mock.patch.object(gates, "security_gate", return_value={"ok": False, "detail": "canary found"}):
            with self.assertRaises(core.ReleaseError) as raised:
                release.step_export(ctx, st, "2026.10.12.1")
        self.assertEqual(raised.exception.code, "SECURITY_GATE")
        self.assertIsNone(export.rev(self.cfg["platform_bare"], "refs/heads/main"))
        empty = self.tmpdir()
        self.assertFalse(gates.security_gate(empty, empty)["ok"])                  # ops/security_gate.py absent -> FAIL

    def test_full_step_creates_main_and_tag_then_resume_is_a_no_op(self):
        ctx = ctx_for(self.cfg)
        st = core.State(self.fx.tmp / "state.json")
        st.update(commit_unix=1_700_000_000, gates={})
        with mock.patch.object(gates, "security_gate", return_value={"ok": True, "detail": "stub"}):
            release.step_export(ctx, st, "2026.10.12.1")
            first = st["platform_commit"]
            self.assertEqual(export.rev(self.cfg["platform_bare"], "refs/heads/main"), first)
            self.assertEqual(export.rev(self.cfg["platform_bare"], "refs/tags/v2026.10.12.1"), first)
            release.step_export(ctx, st, "2026.10.12.1")                           # resume: tag matches -> nothing to do
            self.assertEqual(st["platform_commit"], first)
            self.assertEqual(g("rev-list", "--count", "main", cwd=self.cfg["platform_bare"]), "1")
            # second release: parent is the first, history-free otherwise
            st2 = core.State(self.fx.tmp / "state2.json")
            st2.update(commit_unix=1_700_000_100, gates={})
            release.step_export(ctx, st2, "2026.10.12.2")
            self.assertEqual(g("rev-parse", "main^", cwd=self.cfg["platform_bare"]), first)

    def test_tag_on_a_different_commit_is_a_hard_stop(self):
        bare, _plan, commit, _tree = self.build()
        export.tag_release(bare, "v2026.10.12.1", commit)
        export.tag_release(bare, "v2026.10.12.1", commit)                          # same commit: fine
        other = export.build_commit(bare, export.plan_export(self.cfg), self.cfg, parent=None, date_unix=1_700_000_007, message="other\n")[0]
        with self.assertRaises(core.ReleaseError) as raised:
            export.tag_release(bare, "v2026.10.12.1", other)
        self.assertEqual(raised.exception.code, "TAG_CONFLICT")

    def test_sources_that_moved_since_the_tag_cannot_reuse_the_id(self):
        fx = Fixture(with_liveness=False)           # private fixture: this test commits to the source repo
        self.addCleanup(fx.close)
        st = core.State(fx.tmp / "s.json")
        st.update(commit_unix=1_700_000_000, gates={})
        with mock.patch.object(gates, "security_gate", return_value={"ok": True, "detail": "stub"}):
            release.step_export(ctx_for(fx.cfg), st, "2026.10.12.1")
            (fx.melange / "fec" / "lf.py").write_bytes(b"print('changed')\n")
            g("add", "-A", cwd=fx.melange)
            g("commit", "-q", "-m", "change", cwd=fx.melange)
            with self.assertRaises(core.ReleaseError) as raised:
                release.step_export(ctx_for(fx.cfg), core.State(fx.tmp / "s2.json"), "2026.10.12.1")
        self.assertEqual(raised.exception.code, "TAG_CONFLICT")

    def test_push_never_forces(self):
        for flag in ("--force", "-f", "--force-with-lease", "+main", "--mirror", "--delete"):
            with self.assertRaises(core.ReleaseError) as raised:
                core.git("push", "origin", flag, cwd=self.fx.melange)
            self.assertEqual(raised.exception.code, "FORCE_REFUSED")

    def test_ids_order_numerically(self):
        self.assertGreater(core.parse_id("2026.10.12.10"), core.parse_id("2026.10.12.9"))
        self.assertEqual(core.next_release_id(["2026.10.12.9", "2026.10.11.20"], __import__("datetime").date(2026, 10, 12)), "2026.10.12.10")
        self.assertEqual(core.next_release_id([], __import__("datetime").date(2026, 10, 12)), "2026.10.12.1")
        with self.assertRaises(core.ReleaseError):
            core.next_release_id(["2026.10.13.1"], __import__("datetime").date(2026, 10, 12))   # clock behind the newest release
        with self.assertRaises(core.ReleaseError):
            core.parse_id("v2026.10.12.1")


if __name__ == "__main__":
    unittest.main()

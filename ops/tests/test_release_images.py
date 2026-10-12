"""`release.py images`: the code-only path (export + security gate, push, CI wait, digests) and `--init-remote`.
Same fakes as test_release_status: local git remotes, a stubbed security gate, gh shim. No network."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest import mock

from ops.tests.test_release_common import Base, g, read_log
from ops.tests.test_release_status import RunEnv, cred
from ops.release_lib import core, export
from ops import release

RID = "2026.10.12.1"
BOTH = ["--confirm-outward", "git,geo"]
ROOT = "pjg93-222/melange-platform"


class ImagesEnv(RunEnv):
    """RunEnv plus the Geo image branch (one commit ahead of claude/walk-list, which is checked out and must never be pushed)."""

    def __init__(self, test):
        super().__init__(test)
        fx = self.fx
        g("checkout", "-q", "-b", "deploy/terrier-hosted", cwd=fx.terrier)
        (fx.terrier / "Dockerfile").write_bytes(b"FROM hosted\n")
        g("commit", "-qam", "hosted", cwd=fx.terrier)
        g("checkout", "-q", "claude/walk-list", cwd=fx.terrier)
        self.geo_commit = g("rev-parse", "refs/heads/deploy/terrier-hosted", cwd=fx.terrier)
        self.platform_remote, self.geo_remote = fx.tmp / "platform-remote.git", fx.tmp / "geo-remote.git"
        self.cfg["terrier_images_ref"] = "deploy/terrier-hosted"

    def images(self, *argv):
        return self.main("images", "--id", RID, *argv)

    def state(self):
        return json.loads((self.releases() / RID / "state.json").read_text())

    def gh_calls(self):
        return [c["argv"] for c in read_log(self.log) if c["tool"] == "gh"]


class ImagesTests(Base):
    def test_without_confirm_it_exports_and_gates_locally_and_nothing_leaves(self):
        env = ImagesEnv(self)
        (env.fx.melange / "fec" / "lf.py").write_bytes(b"print('dirty')\n")        # a dirty live tree does not matter: committed objects only
        self.assertEqual(env.images(), 3, env.text)
        self.assertIn("pending confirmation: git push", env.text)
        self.assertIn("code-only image build", env.text)
        state = env.state()
        self.assertEqual(export.rev(env.cfg["platform_bare"], "refs/tags/v" + RID), state["platform_commit"])
        self.assertEqual((state["kind"], list(state["gates"])), ("images", ["security_gate"]))
        self.assertEqual([c.args[2:] for c in env.sec.call_args_list], [()])         # the export gate ran (default stage), once
        self.assertEqual(state["code_only"]["gates_evaluated"], ["tag_collision", "security_gate"])
        self.assertIn("invited_build", state["code_only"]["gates_not_evaluated"])     # the report says what was NOT evaluated, and why
        self.assertIn("no data leaves", state["code_only"]["why"])
        self.assertEqual(env.tools_called(), [])
        for remote in (env.platform_remote, env.geo_remote):
            self.assertEqual(g("for-each-ref", cwd=remote), "")
        self.assertFalse((env.releases() / RID / "snapshot_manifest.json").exists())  # no capture, pocket, desk or farm

    def test_a_failing_security_gate_stops_before_any_tag_or_push(self):
        env = ImagesEnv(self)
        env.sec.return_value = {"ok": False, "detail": "gitleaks: 1 finding"}
        self.assertEqual(env.images(*BOTH), 1, env.text)
        self.assertIn("SECURITY_GATE: gitleaks: 1 finding", env.text)
        self.assertIsNone(export.rev(env.cfg["platform_bare"], "refs/tags/v" + RID))
        self.assertIsNone(export.rev(env.cfg["platform_bare"], "refs/heads/main"))
        self.assertEqual(env.tools_called(), [])
        for remote in (env.platform_remote, env.geo_remote):
            self.assertEqual(g("for-each-ref", cwd=remote), "")

    def test_only_the_confirmed_step_pushes(self):
        env = ImagesEnv(self)
        self.assertEqual(env.images("--confirm-outward", "git"), 3, env.text)
        self.assertIn("pending confirmation: geo push", env.text)
        commit = env.state()["platform_commit"]
        self.assertEqual(g("rev-parse", "refs/heads/main", "refs/tags/v" + RID, cwd=env.platform_remote).split(), [commit, commit])
        self.assertEqual(g("for-each-ref", cwd=env.geo_remote), "")
        self.assertEqual(env.gh_calls(), [])

    def foreign_commit(self, repo):
        return g("commit-tree", g("hash-object", "-t", "tree", "-w", "--stdin", cwd=repo, input=b""), "-m", "foreign", cwd=repo)

    def test_a_local_tag_on_another_commit_is_a_hard_stop(self):
        env = ImagesEnv(self)
        bare = export.ensure_bare(env.cfg["platform_bare"], ROOT, env.cfg["platform_remote_url"])
        other = self.foreign_commit(bare)
        g("update-ref", "refs/tags/v" + RID, other, cwd=bare)
        self.assertEqual(env.images(*BOTH), 1, env.text)
        self.assertIn("TAG_CONFLICT", env.text)
        self.assertEqual(g("rev-parse", "refs/tags/v" + RID, cwd=bare), other)
        for remote in (env.platform_remote, env.geo_remote):
            self.assertEqual(g("for-each-ref", cwd=remote), "")
        self.assertEqual(env.gh_calls(), [])

    def test_a_platform_remote_tag_on_another_commit_is_a_hard_stop(self):
        env = ImagesEnv(self)
        self.assertEqual(env.images(), 3, env.text)
        other = self.foreign_commit(env.platform_remote)
        g("update-ref", "refs/tags/v" + RID, other, cwd=env.platform_remote)
        self.assertEqual(env.images(*BOTH), 1, env.text)
        self.assertIn("TAG_CONFLICT", env.text)
        self.assertEqual(g("rev-parse", "refs/tags/v" + RID, cwd=env.platform_remote), other)   # untouched
        self.assertEqual(g("for-each-ref", cwd=env.geo_remote), "")
        self.assertEqual(env.gh_calls(), [])

    def test_a_geo_remote_tag_on_another_commit_is_a_hard_stop(self):
        env = ImagesEnv(self)
        other = self.foreign_commit(env.geo_remote)
        g("update-ref", "refs/tags/v" + RID, other, cwd=env.geo_remote)
        self.assertEqual(env.images(*BOTH), 1, env.text)
        self.assertIn("TAG_CONFLICT", env.text)
        self.assertEqual(g("for-each-ref", "--format=%(refname)", cwd=env.geo_remote), "refs/tags/v" + RID)    # no branch went up
        self.assertEqual(env.gh_calls(), [])

    def test_geo_gets_its_image_branch_never_the_walk_list(self):
        env = ImagesEnv(self)
        env.can_gh(RID)
        self.assertEqual(env.images(*BOTH), 0, env.text)
        refs = g("for-each-ref", "--format=%(refname) %(objectname)", cwd=env.geo_remote).splitlines()
        self.assertEqual(sorted(refs), sorted([f"refs/heads/deploy/terrier-hosted {env.geo_commit}", f"refs/tags/v{RID} {env.geo_commit}"]))
        self.assertEqual(env.state()["terrier_commit"], env.geo_commit)
        self.assertNotEqual(env.geo_commit, env.fx.commits["terrier"])

    def test_geo_is_pushed_only_to_its_own_remote_and_never_the_full_releases_branch(self):
        for key, value, code in (("terrier_remote_url", "https://example.invalid/not-geo.git", "GEO_REMOTE"),
                                 ("terrier_images_ref", "claude/walk-list", "GEO_REF")):
            with self.subTest(code=code):
                env = ImagesEnv(self)
                env.cfg[key] = value
                self.assertEqual(env.images(*BOTH), 1, env.text)
                self.assertIn(code, env.text)
                self.assertEqual(g("for-each-ref", cwd=env.geo_remote), "")
                if code == "GEO_REF":                                                      # a config error stops before the platform push too
                    self.assertEqual(g("for-each-ref", cwd=env.platform_remote), "")

    def test_success_prints_the_three_digests_and_the_run_urls_and_stops_there(self):
        env = ImagesEnv(self)
        env.can_gh(RID)
        self.assertEqual(env.images(*BOTH), 0, env.text)
        self.assertIn("ghcr.io/pjg93-222/melange-desk@sha256:" + "1" * 64, env.text)
        self.assertIn("ghcr.io/pjg93-222/mentat-console@sha256:" + "2" * 64, env.text)
        self.assertIn("ghcr.io/pjg93-222/terrier-web@sha256:" + "3" * 64, env.text)
        self.assertIn(f"https://github.com/{ROOT}/actions/runs/1", env.text)
        self.assertIn("https://github.com/pjg93-222/Geo/actions/runs/1", env.text)
        commit = env.state()["platform_commit"]
        self.assertEqual(g("rev-parse", "refs/heads/main", "refs/tags/v" + RID, cwd=env.platform_remote).split(), [commit, commit])
        self.assertEqual(g("rev-parse", "refs/heads/deploy/terrier-hosted", "refs/tags/v" + RID, cwd=env.geo_remote).split(), [env.geo_commit] * 2)
        self.assertNotIn("refs/heads/claude/walk-list", g("for-each-ref", cwd=env.geo_remote))
        self.assertEqual(sorted(set(env.tools_called())), ["gh"])                         # no restic, sftp, cloudflared
        self.assertFalse([a for a in env.gh_calls() if "PATCH" in a])                      # nothing un-drafted
        self.assertFalse((env.remote / "inbox").exists())
        self.assertFalse((env.releases() / RID / "manifest.json").exists())               # nothing signed
        self.assertEqual(env.images(*BOTH), 0, env.text)                                  # a rerun of the same id finds everything done

    def test_a_ci_failure_exits_nonzero_with_a_masked_tail_of_the_failed_log(self):
        env = ImagesEnv(self)
        env.can_gh(RID)
        (env.gh / "run_list.json").write_text(json.dumps([{"databaseId": 7, "status": "completed", "conclusion": "failure"}]))
        (env.gh / "run_view.json").write_text("\n".join([f"build line {i}" for i in range(1, 101)] + ["login with ghp_" + "a" * 36, "token: abc123"]))
        self.assertEqual(env.images(*BOTH), 1, env.text)
        self.assertIn("CI_FAILED", env.text)
        self.assertIn(f"https://github.com/{ROOT}/actions/runs/7", env.text)
        self.assertIn("build line 100", env.text)
        self.assertNotIn("build line 61", env.text)                                       # only the tail
        self.assertNotIn("ghp_" + "a" * 36, env.text)
        self.assertNotIn("abc123", env.text)
        view = [a for a in env.gh_calls() if a[:2] == ["run", "view"]]
        self.assertEqual(view, [["run", "view", "7", "--repo", ROOT, "--log-failed"]])
        self.assertFalse((env.releases() / RID / "manifest.json").exists())

    def test_a_bom_and_unencodable_characters_in_the_failed_log_do_not_crash_a_cp1252_console(self):
        """UnicodeEncodeError under cp1252 printed over the real failure (the first Linux CI log began with a BOM)."""
        env = ImagesEnv(self)
        env.can_gh(RID)
        (env.gh / "run_list.json").write_text(json.dumps([{"databaseId": 7, "status": "completed", "conclusion": "failure"}]))
        (env.gh / "run_view.json").write_text("﻿2026-10-12T16:53:26Z ##[group]Run build\nstep ✓ → done\nlast line", encoding="utf-8")
        console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")                      # what a Windows console gives python
        with contextlib.redirect_stdout(console):
            rc = release.main(["images", "--id", RID, *BOTH], cfg=env.cfg, emit=core.noop_emit, cred=cred, sleep=lambda _s: None, say=print)
        console.flush()
        shown = console.buffer.getvalue().decode("cp1252")
        self.assertEqual(rc, 1, shown)
        self.assertIn("CI_FAILED", shown)
        self.assertIn("last line", shown)
        self.assertNotIn("﻿", shown)                                                # stripped at the log-tail site
        self.assertIn("step ? ? done", shown)                                            # anything else cp1252 cannot encode is replaced

    def test_a_run_that_never_finishes_times_out_instead_of_hanging(self):
        env = ImagesEnv(self)
        env.can_gh(RID)
        (env.gh / "run_list.json").write_text(json.dumps([{"databaseId": 7, "status": "in_progress", "conclusion": None}]))
        real = core.run

        def run(argv, **kw):
            if list(argv[:3]) == ["gh", "run", "watch"]:
                self.assertEqual(kw["timeout"], release.CI_TIMEOUT)
                raise subprocess.TimeoutExpired(argv, kw["timeout"])
            return real(argv, **kw)
        with mock.patch.object(core, "run", run):
            self.assertEqual(env.images(*BOTH), 1, env.text)
        self.assertIn("TIMEOUT", env.text)

    def test_the_confirm_list_names_only_image_steps_and_run_never_accepts_them(self):
        env = ImagesEnv(self)
        for argv in (["images", "--confirm-outward", "data"], ["images", "--confirm-outward", "git,publish"], ["run", "--confirm-outward", "geo"],
                     ["run", "--confirm-outward", "repo"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    env.main(*argv)
                self.assertEqual(raised.exception.code, 2)
        self.assertEqual(env.tools_called(), [])


class InitRemoteTests(Base):
    CREATE = ["repo", "create", ROOT, "--private", "--disable-wiki", "--description", "Melange platform (source-only release exports)"]

    def test_nothing_happens_without_confirm_outward_repo(self):
        env = ImagesEnv(self)
        self.assertEqual(env.main("images", "--init-remote"), 3, env.text)
        self.assertIn("pending confirmation: create the platform GitHub repo", env.text)
        self.assertEqual(env.tools_called(), [])
        self.assertFalse(os.path.exists(env.cfg["platform_bare"]))
        self.assertEqual(env.main("images", "--init-remote", "--confirm-outward", "git,geo"), 3, env.text)    # other steps do not stand in
        self.assertEqual(env.tools_called(), [])

    def test_it_creates_the_private_repo_when_gh_cannot_see_it_and_sets_the_bare_origin_idempotently(self):
        env = ImagesEnv(self)
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL": "repo view"}):
            self.assertEqual(env.main("images", "--init-remote", "--confirm-outward", "repo"), 0, env.text)
            self.assertEqual(env.main("images", "--init-remote", "--confirm-outward", "repo"), 0, env.text)
        calls = env.gh_calls()
        self.assertEqual(calls[:2], [["repo", "view", ROOT], self.CREATE])                 # the existence check comes first
        self.assertEqual(g("remote", "get-url", "origin", cwd=env.cfg["platform_bare"]), env.cfg["platform_remote_url"])
        self.assertEqual(g("remote", cwd=env.cfg["platform_bare"]), "origin")

    def test_an_existing_repo_is_left_alone(self):
        env = ImagesEnv(self)
        for _ in range(2):
            self.assertEqual(env.main("images", "--init-remote", "--confirm-outward", "repo"), 0, env.text)
        self.assertEqual(env.gh_calls(), [["repo", "view", ROOT]] * 2)                     # never `repo create`
        self.assertIn("already exists", env.text)
        self.assertEqual(g("remote", "get-url", "origin", cwd=env.cfg["platform_bare"]), env.cfg["platform_remote_url"])

    def test_a_bare_repo_whose_origin_is_somewhere_else_is_refused(self):
        env = ImagesEnv(self)
        export.ensure_bare(env.cfg["platform_bare"], ROOT, "https://example.invalid/other.git")
        self.assertEqual(env.main("images", "--init-remote", "--confirm-outward", "repo"), 1, env.text)
        self.assertIn("BARE_REMOTE", env.text)


if __name__ == "__main__":
    unittest.main()

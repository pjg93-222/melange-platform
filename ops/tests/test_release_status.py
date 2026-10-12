"""CLI level: plan is a dry run, run stops cleanly without --confirm-outward, a full release end to end against
local remotes + fake restic/sftp/gh shims, a data-only release, and status with missing config. No network."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from ops.tests.test_release_common import (Base, FULL_DESK, Fixture, REAL_LIVE, g, install_shims, lock_envelope_bytes, read_log,
                                           write_invited)
from ops.release_lib import core, data, export, gates, manifest
from ops import release
from ops.tests.test_release_netlify import FakeNetlify, SITE

KEYGEN = manifest.keygen()
HEX = "c" * 64
PUSH_ID, PUSH_SECRET = "push-id.access", "TUNNEL-SECRET-xyz"


def cred(target):
    return {"melange-restic": ("", core.Secret("RESTIC-PW-xyz")), "melange-svc-push": (PUSH_ID, core.Secret(PUSH_SECRET)),
            "melange-svc-status": ("status-id.access", core.Secret("STATUS-SECRET"))}[target]


class RunEnv:
    """A complete fake world: three repos with local remotes, a live tree, serve sets, a signing key, PATH shims."""

    def __init__(self, test: Base):
        self.fx = Fixture(with_liveness=True, eol_attrs=False, big=False)
        test.addCleanup(self.fx.close)
        fx, tmp = self.fx, self.fx.tmp
        (fx.melange / "data" / "engine").mkdir(parents=True)
        (fx.melange / "data" / "engine" / "weekly_refresh_status.json").write_text(json.dumps({"last_outcome": "COMPLETE"}))
        self.invited = write_invited(fx.melange)
        (fx.terrier / ".local").mkdir()
        db = sqlite3.connect(fx.terrier / ".local" / "scores.sqlite3")
        db.execute("CREATE TABLE t(x)")
        db.commit()
        db.close()
        sets = {"schema": "melange-serve-sets-v1", "census_pending": False, "systems": {
            "melange": {"data_subdir": "melange", "rules": [{"root": "melange_live", "src": "outputs/invited_build", "include": ["Moneyball.html"], "dest": ""}]},
            "mentat": {"data_subdir": "mentat", "rules": [{"root": "mentat_live", "src": "mentat/state", "include": ["**"], "dest": "state"}]},
            "terrier": {"data_subdir": "terrier", "rules": [{"root": "terrier_checkout", "src": ".local", "include": ["scores.sqlite3"], "dest": "co-05"}]}}}
        self.sets = tmp / "serve_sets.json"
        self.sets.write_text(json.dumps(sets))
        subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "release@melange", "-f", str(tmp / "sign_key")], check=True)
        self.allowed = tmp / "allowed_signers"
        self.allowed.write_text(f'release@melange namespaces="melange-release" {(tmp / "sign_key.pub").read_text().strip()}\n')
        fx.cfg["signing"].update(allowed_signers=str(self.allowed))
        fx.cfg.update(domain="example.test")
        self.log, self.remote, self.gh = tmp / "shim.jsonl", tmp / "vps", tmp / "gh"
        self.remote.mkdir()
        self.gh.mkdir()
        self.env = install_shims(tmp / "bin", ["restic", "sftp", "cloudflared", "gh"], self.log, self.remote, {"FAKE_GH_DIR": str(self.gh)})
        patches = [mock.patch.dict(os.environ, self.env), mock.patch.object(release, "SERVE_SETS", self.sets),
                   mock.patch.object(gates, "security_gate", return_value={"ok": True, "detail": "stub"})]
        for patch in patches:
            started = patch.start()
            test.addCleanup(patch.stop)
        self.sec = started                                          # the stubbed security gate: tests read its calls
        self.lines: list[str] = []
        self.cfg = fx.cfg

    def main(self, *argv, cfg=None):
        return release.main(list(argv), cfg=cfg or self.cfg, emit=core.noop_emit, cred=cred, sleep=lambda _s: None, say=self.lines.append)

    @property
    def text(self):
        return "\n".join(self.lines)

    def releases(self) -> Path:
        return Path(self.cfg["releases_dir"])

    def tools_called(self):
        return [c["tool"] for c in read_log(self.log)]

    def can_gh(self, rid, draft=True):
        """Canned GitHub for both repos: the tag's successful workflow run, and a release that run's bot made, with
        digests.json + one kit asset. The commits are the release's own (the shim fills in @..._COMMIT@ from its state)."""
        os.environ["FAKE_STATE"] = str(self.releases() / rid / "state.json")

        def asset(i, name, size):
            return {"id": i, "name": name, "size": size, "digest": "sha256:" + HEX, "uploader": {"login": "github-actions[bot]"}}
        for n, (repo, images, commit) in enumerate((("pjg93-222/melange-platform", {"melange-desk": "sha256:" + "1" * 64, "mentat-console": "sha256:" + "2" * 64}, "@PLATFORM_COMMIT@"),
                                                    ("pjg93-222/Geo", {"terrier-web": "ghcr.io/pjg93-222/terrier-web@sha256:" + "3" * 64}, "@TERRIER_COMMIT@"))):
            base = f"api_repos_{repo.replace('/', '_')}"
            (self.gh / "run_list.json").write_text(json.dumps([{"databaseId": 1, "status": "completed", "conclusion": "success"}]))
            (self.gh / f"{base}_actions_runs.json").write_text(json.dumps({"workflow_runs": [
                {"id": 1, "head_sha": commit, "head_branch": "v" + rid, "event": "push", "conclusion": "success",
                 "run_started_at": "2026-10-12T10:00:00Z", "updated_at": "2026-10-12T10:20:00Z"}]}))
            (self.gh / f"{base}_releases.json").write_text(json.dumps([{"id": 100 + n, "tag_name": "v" + rid, "draft": draft, "created_at": "2026-10-12T10:15:00Z",
                                                                         "author": {"login": "github-actions[bot]"},
                                                                         "assets": [asset(10 + n, "digests.json", 40), asset(20 + n, "kit.zip", 5000)]}]))
            (self.gh / f"{base}_releases_assets_{10 + n}.json").write_text(json.dumps({"tag": "v" + rid, "commit": commit, "images": images}))
            (self.gh / f"{base}_releases_{100 + n}.json").write_text(json.dumps({"id": 100 + n, "draft": draft}))


FLAGS = ["--skip-kernel", "--no-netlify"]
ALL_STEPS = "git,data,manifest,netlify,undraft"
CONFIRM_ALL = ["--confirm-outward", ALL_STEPS]


class PlanTests(Base):
    def test_plan_is_a_dry_run_that_writes_only_its_report(self):
        env = RunEnv(self)
        code = env.main("plan", *FLAGS)
        self.assertEqual(code, 0, env.text)
        self.assertFalse(Path(env.cfg["platform_bare"]).exists())                 # export not performed
        reports = list(env.releases().iterdir())
        self.assertEqual([p.name[:5] for p in reports], ["plan-"])
        report = json.loads(reports[0].read_text())
        self.assertTrue(report["gates"]["kernel_tests"]["skipped"])               # the skip is recorded, not hidden
        self.assertGreater(report["export"]["files"], 5)
        self.assertIn(["fec/data.csv", "extension not allowed"], [list(d) for d in report["export"]["dropped"]])
        self.assertEqual(set(report["serve_sets"]["systems"]), {"melange", "mentat", "terrier"})
        self.assertTrue(report["outward"]["data push"]["ready"])                  # shims, domain and credentials are all present
        self.assertFalse(report["outward"]["netlify"]["ready"])                   # no site id / token: reported, not fatal
        self.assertEqual(env.tools_called(), [])                                  # nothing was invoked
        self.assertEqual(g("for-each-ref", cwd=env.fx.tmp / "platform-remote.git"), "")

    def test_plan_fails_when_a_gate_fails(self):
        env = RunEnv(self)
        (env.fx.melange / "fec" / "lf.py").write_bytes(b"print('dirty')\n")
        self.assertEqual(env.main("plan", *FLAGS), 1)
        self.assertIn("FAIL repos_clean", env.text)


class RunTests(Base):
    def test_without_confirm_everything_local_is_done_and_nothing_leaves(self):
        env = RunEnv(self)
        code = env.main("run", *FLAGS)
        self.assertEqual(code, 3, env.text)
        self.assertIn("pending confirmation: git push", env.text)
        rid = core.next_release_id([]).rsplit(".", 1)[0] + ".1"
        state = json.loads((env.releases() / rid / "state.json").read_text())
        self.assertEqual(export.rev(env.cfg["platform_bare"], "refs/tags/v" + rid), state["platform_commit"])
        snap = env.releases() / rid / "snapshot_manifest.json"
        doc = json.loads(snap.read_bytes())
        self.assertEqual(core.sha256_file(snap), state["snapshot"]["manifest_sha256"])
        self.assertEqual((doc["governance"], doc["profile_sha256"]), ("ruled", "d" * 64))       # the invited build's own state
        paths = [f["path"] for f in doc["files"]]
        self.assertEqual(paths, sorted(paths))
        self.assertIn("melange/Moneyball.html", paths)
        self.assertIn("mentat/state/s.json", paths)
        self.assertIn("terrier/co-05/scores.sqlite3", paths)
        self.assertIn(f"melange/downloads/{rid}/melange-desk-{rid}.html", paths)
        self.assertIn(f"melange/downloads/{rid}/SHA256SUMS", paths)
        self.assertIn("melange/downloads/latest/SHA256SUMS", paths)
        self.assertTrue((env.releases() / "staging" / rid / "farm" / "terrier" / "co-05" / "scores.sqlite3").is_file())
        self.assertEqual(env.tools_called(), [])                                  # no restic, sftp, gh, cloudflared
        self.assertEqual(g("for-each-ref", cwd=env.fx.tmp / "platform-remote.git"), "")      # nothing pushed
        self.assertEqual(g("for-each-ref", cwd=env.fx.tmp / "geo-remote.git"), "")
        self.assertFalse((env.fx.melange / ".pipeline_run.lock").exists())        # the writer lease was released
        self.assertIn("pocket skipped", env.text)                                 # no password file configured -> skipped, not fatal

    def test_a_desk_that_is_not_the_invited_build_reaches_neither_downloads_nor_the_farm(self):
        env = RunEnv(self)
        full = lock_envelope_bytes(b"full desk")
        index = env.invited["index"]
        index.write_bytes(full)                                              # deploy/index.html is a lock of the FULL desk
        env.invited["receipt"].write_text(json.dumps({"source": {"sha256": core.sha256_bytes(FULL_DESK)},
                                                      "local_deploy_artifact": {"sha256": core.sha256_bytes(full)}}))
        self.assertEqual(env.main("run", *FLAGS), 3, env.text)
        rid = core.next_release_id([]).rsplit(".", 1)[0] + ".1"
        self.assertIn("desk omitted", env.text)
        self.assertIn("melange-desk omitted", env.text)
        paths = [f["path"] for f in json.loads((env.releases() / rid / "snapshot_manifest.json").read_bytes())["files"]]
        self.assertFalse([p for p in paths if "melange-desk" in p], paths)
        self.assertIn(f"melange/downloads/{rid}/SHA256SUMS", paths)
        self.assertFalse(list((env.releases() / rid).rglob("index.html")))   # nothing was staged for Netlify either

    def test_a_pending_invited_build_stops_the_release_and_the_override_is_recorded(self):
        env = RunEnv(self)
        build = env.invited["invited"] / "INVITED_BUILD.json"
        build.write_text(build.read_text().replace('"ruled"', '"pending"'))
        self.assertEqual(env.main("run", *FLAGS), 1, env.text)
        self.assertIn("GATES_FAILED: invited_build", env.text)
        self.assertEqual([p.name for p in env.releases().iterdir() if p.name[0].isdigit()], ["2026.10.12.1"])
        self.assertFalse((env.releases() / "2026.10.12.1" / "snapshot_manifest.json").exists())      # nothing was captured
        self.assertEqual(env.main("resume", "2026.10.12.1", "--governance-override", *FLAGS), 3, env.text)
        state = json.loads((env.releases() / "2026.10.12.1" / "state.json").read_text())
        self.assertEqual(state["gates"]["invited_build"]["governance_override"], True)
        snap = json.loads((env.releases() / "2026.10.12.1" / "snapshot_manifest.json").read_bytes())
        self.assertEqual(snap["governance"], "pending")                                          # never relabelled "invited"

    def test_packages_and_the_farm_are_gated_before_anything_is_pushed(self):
        env = RunEnv(self)
        self.assertEqual(env.main("run", *FLAGS), 3, env.text)
        rid = "2026.10.12.1"
        by_stage = {c.args[2] if len(c.args) > 2 else "export": c for c in env.sec.call_args_list}
        package, farm = by_stage["packages"], by_stage["farm"]
        base = env.releases() / rid
        self.assertEqual([package.args[1], *package.args[3]], [base / "package", base / "netlify", base / "package" / "downloads"])
        self.assertEqual(farm.args[1], env.releases() / "staging" / rid / "farm")
        self.assertEqual(farm.kwargs, {"scan_cache": env.releases() / "scan_cache.sqlite"})      # beside hash_cache.sqlite, farm only
        self.assertEqual(package.kwargs, {})
        gates_seen = json.loads((base / "state.json").read_text())["gates"]
        self.assertTrue(gates_seen["security_packages"]["ok"] and gates_seen["security_farm"]["ok"])

    def test_a_failing_package_or_farm_gate_stops_the_release_before_any_push(self):
        for failing in ("packages", "farm"):
            env = RunEnv(self)
            env.sec.side_effect = lambda ops, target, stage="export", more=(), failing=failing, **_: {"ok": stage != failing, "detail": "boom"}
            self.assertEqual(env.main("run", *CONFIRM_ALL, *FLAGS), 1, env.text)
            self.assertIn(f"SECURITY_GATE: {failing}: boom", env.text)
            self.assertEqual(env.tools_called(), [], failing)                              # no restic, sftp, gh
            self.assertEqual(g("for-each-ref", cwd=env.fx.tmp / "platform-remote.git"), "", failing)   # nothing pushed

    def test_no_data_goes_out_while_the_census_is_pending_unless_overridden_and_recorded(self):
        env = RunEnv(self)
        sets = json.loads(env.sets.read_text())
        env.sets.write_text(json.dumps({**sets, "census_pending": True}))
        env.can_gh("2026.10.12.1")
        self.assertEqual(env.main("plan", *FLAGS), 1, env.text)
        self.assertIn("FAIL census", env.text)
        self.assertEqual(env.main("run", *CONFIRM_ALL, *FLAGS), 1, env.text)
        self.assertIn("GATES_FAILED: census", env.text)
        self.assertEqual(env.tools_called(), [])                                  # not even the git push: nothing leaves
        self.assertEqual(g("for-each-ref", cwd=env.fx.tmp / "platform-remote.git"), "")
        self.assertEqual(env.main("resume", "2026.10.12.1", "--census-override", *CONFIRM_ALL, *FLAGS), 0, env.text)
        doc = json.loads((env.remote / "inbox" / "manifest.json").read_bytes())
        self.assertEqual(doc["gates"]["census"]["census_override"], True)             # the signed manifest says it was overridden

    def test_each_outward_step_must_be_named_in_the_confirm_list(self):
        env = RunEnv(self)
        rid = "2026.10.12.1"
        env.can_gh(rid)
        platform = env.fx.tmp / "platform-remote.git"
        self.assertEqual(env.main("run", "--confirm-outward", "git", *FLAGS), 3, env.text)
        self.assertIn("pending confirmation: data push", env.text)
        self.assertEqual(g("rev-parse", "refs/tags/v" + rid, cwd=platform), json.loads((env.releases() / rid / "state.json").read_text())["platform_commit"])
        self.assertNotIn("restic", env.tools_called())                              # git was confirmed; the data push was not
        self.assertEqual(env.main("resume", rid, "--confirm-outward", "git,data", *FLAGS), 3, env.text)
        self.assertIn("pending confirmation: manifest push", env.text)
        self.assertIn("restic", env.tools_called())
        self.assertFalse((env.remote / "inbox").exists())                           # no manifest reached the VPS
        self.assertEqual(env.main("resume", rid, "--confirm-outward", " git , data,manifest ", *FLAGS), 3, env.text)
        self.assertIn("pending confirmation: publish GitHub releases", env.text)    # the draft releases stay drafts
        self.assertTrue((env.remote / "inbox" / "manifest.json").is_file())
        self.assertFalse([c for c in read_log(env.log) if c["tool"] == "gh" and "PATCH" in c["argv"]])
        self.assertEqual(env.main("resume", rid, *CONFIRM_ALL, *FLAGS), 0, env.text)
        self.assertEqual(len([c for c in read_log(env.log) if c["tool"] == "gh" and "PATCH" in c["argv"]]), 2)

    def test_the_confirm_list_is_validated_and_a_bare_flag_is_refused(self):
        env = RunEnv(self)
        for argv in (["run", "--confirm-outward"], ["run", "--confirm-outward", "git,publish"], ["run", "--confirm-outward", ""],
                     ["run", "--confirm-outward", "all"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as raised:
                    env.main(*argv, *FLAGS)
                self.assertEqual(raised.exception.code, 2)
        self.assertEqual(env.tools_called(), [])
        self.assertEqual(release.confirm_steps("git, data"), frozenset({"git", "data"}))

    def test_missing_config_after_confirm_stops_as_not_configured(self):
        env = RunEnv(self)
        cfg = dict(env.cfg, domain=None)
        self.assertEqual(env.main("run", *CONFIRM_ALL, *FLAGS, cfg=cfg), 4, env.text)
        self.assertIn("not configured", env.text)
        self.assertIn("domain", env.text)
        self.assertNotIn("restic", env.tools_called())

    def test_a_tag_already_on_another_commit_on_the_remote_is_a_hard_stop(self):
        env = RunEnv(self)
        self.assertEqual(env.main("run", *FLAGS), 3)
        rid = sorted(p.name for p in env.releases().iterdir() if p.name[0].isdigit())[0]
        remote = env.fx.tmp / "platform-remote.git"
        empty = g("hash-object", "-t", "tree", "-w", "--stdin", cwd=remote, input=b"")
        other = g("commit-tree", empty, "-m", "foreign", cwd=remote)
        g("update-ref", "refs/tags/v" + rid, other, cwd=remote)
        code = env.main("resume", rid, *CONFIRM_ALL, *FLAGS)
        self.assertEqual(code, 1, env.text)
        self.assertIn("TAG_CONFLICT", env.text)
        self.assertEqual(g("rev-parse", "refs/tags/v" + rid, cwd=remote), other)   # untouched
        self.assertNotIn("restic", env.tools_called())

    def test_full_release_then_data_only_release_end_to_end(self):
        env = RunEnv(self)
        rid = core.next_release_id([]).rsplit(".", 1)[0] + ".1"
        env.can_gh(rid)
        self.assertEqual(env.main("run", *CONFIRM_ALL, *FLAGS), 0, env.text)
        # git: platform repo and Geo are on their remotes with the release tag, no force
        platform = env.fx.tmp / "platform-remote.git"
        state = json.loads((env.releases() / rid / "state.json").read_text())
        self.assertEqual(g("rev-parse", "refs/tags/v" + rid, cwd=platform), state["platform_commit"])
        self.assertEqual(g("rev-parse", "refs/heads/main", cwd=platform), state["platform_commit"])
        self.assertEqual(g("rev-parse", "refs/tags/v" + rid, cwd=env.fx.tmp / "geo-remote.git"), env.fx.commits["terrier"])
        # inbox: contract order, manifest.json last, and what is there verifies
        order = [l for l in (env.remote / "order.log").read_text().split("\n") if l.startswith("rename")]
        self.assertEqual(order, ["rename snapshot_manifest.json", "rename manifest.json.sig", "rename manifest.json"])
        inbox = env.remote / "inbox"
        result = manifest.verify_manifest(inbox / "manifest.json", env.allowed, snapshot_path=inbox / "snapshot_manifest.json")
        doc = result["manifest"]
        self.assertEqual((doc["release_id"], doc["previous"], doc["kind"]), (rid, None, "full"))
        self.assertEqual(doc["platform_repo"], {"repo": "pjg93-222/melange-platform", "commit": state["platform_commit"], "tag": "v" + rid})
        self.assertEqual(doc["systems"]["melange"]["image"], "ghcr.io/pjg93-222/melange-desk@sha256:" + "1" * 64)
        self.assertEqual(doc["systems"]["terrier"]["image"], "ghcr.io/pjg93-222/terrier-web@sha256:" + "3" * 64)
        self.assertEqual(doc["systems"]["mentat"]["source"]["commit"], env.fx.commits["mentat"])
        self.assertEqual(doc["systems"]["terrier"]["source"]["commit"], env.fx.commits["terrier"])
        self.assertEqual(doc["data_snapshot"]["restic_snapshot_id"], "ab12cd34" * 8)
        # restic records the farm by its absolute path; the manifest carries it as restic stores it (/C/... on Windows)
        farm = str(env.releases() / "staging" / rid / "farm")
        self.assertEqual(doc["data_snapshot"]["restic_path"], data.restic_tree_path(farm))
        self.assertTrue(doc["data_snapshot"]["restic_path"].startswith("/" + farm[0]))
        self.assertEqual(doc["data_snapshot"]["manifest_sha256"], core.sha256_file(inbox / "snapshot_manifest.json"))
        self.assertIn("kit.zip", doc["packages"])
        self.assertTrue(all(gate["ok"] for gate in doc["gates"].values()))
        self.assertTrue(doc["gates"]["kernel_tests"]["skipped"])
        # secrets: argv of every tool is clean, env carried them
        calls = read_log(env.log)
        for call in calls:
            for secret in ("RESTIC-PW-xyz", PUSH_SECRET, PUSH_ID):
                self.assertNotIn(secret, json.dumps(call["argv"]))
        self.assertTrue(any(c["env"].get("RESTIC_PASSWORD") == "RESTIC-PW-xyz" for c in calls))
        # GitHub: both draft releases were published (PATCH), and the farm was cleaned up
        patches = [c for c in calls if c["tool"] == "gh" and "PATCH" in c["argv"]]
        self.assertEqual(len(patches), 2)
        self.assertFalse((env.releases() / "staging" / rid).exists())
        self.assertFalse((env.fx.melange / ".pipeline_run.lock").exists())
        # a second run of the same id is a no-op for the leaf facts: resume does not re-push or re-back-up
        restic_backups = sum(1 for c in calls if c["tool"] == "restic" and "backup" in c["argv"])
        self.assertEqual(restic_backups, 1)

        # --- data-only release: carries the previous images, no export, no CI, no git
        before = len(read_log(env.log))
        self.assertEqual(env.main("run", "--data-only", *CONFIRM_ALL, *FLAGS), 0, env.text)
        rid2 = rid.rsplit(".", 1)[0] + ".2"
        doc2 = json.loads((inbox / "manifest.json").read_bytes())
        self.assertEqual((doc2["release_id"], doc2["previous"], doc2["kind"]), (rid2, rid, "data-only"))
        self.assertEqual(doc2["systems"], doc["systems"])
        self.assertEqual(doc2["platform_repo"], doc["platform_repo"])
        self.assertNotEqual(doc2["data_snapshot"]["manifest_sha256"], "")
        new_calls = read_log(env.log)[before:]
        self.assertFalse([c for c in new_calls if c["tool"] == "gh"])
        manifest.verify_manifest(inbox / "manifest.json", env.allowed, current_id=rid)
        with self.assertRaises(core.ReleaseError):                                   # and the old one cannot come back
            manifest.verify_manifest(inbox / "manifest.json", env.allowed, current_id="9999.01.01.1")
        # data-only with no earlier completed release is refused
        fresh = RunEnv(self)
        self.assertEqual(fresh.main("run", "--data-only", *CONFIRM_ALL, *FLAGS), 1)
        self.assertIn("NO_BASE_RELEASE", fresh.text)

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows only")
    def test_release_with_netlify_publishes_once_and_confirms_the_receipt(self):
        env = RunEnv(self)
        rid = core.next_release_id([]).rsplit(".", 1)[0] + ".1"
        env.can_gh(rid)
        server = FakeNetlify()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        token_file = env.fx.tmp / "netlify.dpapi"
        core.store_dpapi_secret(token_file, "nfp_example_token")
        env.cfg["netlify"].update(site_id=SITE, api=server.api, token_file=str(token_file))
        index, receipt = env.invited["index"], env.invited["receipt"]
        self.assertEqual(env.main("run", "--skip-kernel", *CONFIRM_ALL), 0, env.text)
        self.assertTrue(server.restored)
        self.assertEqual(server.production["/index.html"], index.read_bytes())
        self.assertEqual(json.loads(server.production["/version.json"])["release_id"], rid)
        done = json.loads(receipt.read_text())
        self.assertTrue(done["contract"]["remote_deploy_confirmed"])
        self.assertEqual(done["contract"]["remote_deploy_id"], "d1")
        state = json.loads((env.releases() / rid / "state.json").read_text())
        self.assertEqual(state["netlify"]["index_sha256"], core.sha256_file(index))
        self.assertNotIn("nfp_example_token", json.dumps(read_log(env.log)))
        drafts = lambda: [c for c in server.calls if c[0] == "POST" and c[1].endswith("/deploys")]
        self.assertEqual(len(drafts()), 1)
        self.assertEqual(env.main("resume", rid, "--skip-kernel", *CONFIRM_ALL), 0, env.text)
        self.assertEqual(len(drafts()), 1)                                           # already live: no second draft
        self.assertEqual(server.production["/index.html"], env.invited["lock"])      # the invited desk's lock, nothing else

        # status: every configured surface is green; only the unconfigured ones (no Pocket, no launcher script) are not
        env.can_gh(rid, draft=False)
        (env.remote / "status").mkdir(exist_ok=True)
        (env.remote / "status" / "status.json").write_text(json.dumps({"release_id": rid}))
        doc = json.loads((env.releases() / rid / "manifest.json").read_bytes())
        seen = []
        real_get = release.web.http_get

        def fake_get(url, headers=None, **kw):
            if ".example.test" in url:
                seen.append((url, dict(headers or {})))
                system = {"melange": "melange", "mentat": "mentat"}.get(url.split("//")[1].split(".")[0], "terrier")
                # CI builds the desk and Mentat images from the platform commit (GIT_SHA=github.sha) and Terrier from Geo's
                sha = doc["systems"]["terrier"]["source"]["commit"] if system == "terrier" else doc["platform_repo"]["commit"]
                return 200, json.dumps({"git_sha": sha[:12]}).encode()
            return real_get(url, headers, **kw)

        with mock.patch.object(release.web, "http_get", fake_get):
            self.assertEqual(env.main("status", rid), 1)                              # exit 1 only because two rows are not configured
        text = "\n".join(env.lines)
        for name in ("github release", "vps status", "app melange", "app mentat", "app co05", "app or05", "app ct01", "netlify desk"):
            self.assertRegex(text, rf"\n\s+ok\s+{name}:", name)
        self.assertRegex(text, r"not configured\s+netlify pocket")
        self.assertRegex(text, r"not configured\s+launcher")
        self.assertEqual({h["CF-Access-Client-Id"] for _u, h in seen}, {"status-id.access"})
        self.assertEqual({h["CF-Access-Client-Secret"] for _u, h in seen}, {"STATUS-SECRET"})
        self.assertEqual(len(seen), 5)
        # a VPS still on the previous release is stale, not ok
        (env.remote / "status" / "status.json").write_text(json.dumps({"release_id": "2026.10.11.1"}))
        env.lines.clear()
        with mock.patch.object(release.web, "http_get", fake_get):
            env.main("status", rid)
        self.assertRegex("\n".join(env.lines), r"stale\s+vps status")

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows only")
    def test_a_production_that_does_not_verify_stops_the_release_before_anything_else_is_published(self):
        env = RunEnv(self)
        rid = core.next_release_id([]).rsplit(".", 1)[0] + ".1"
        env.can_gh(rid)
        server = FakeNetlify()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        server.version_lag = 10 ** 6                                          # production never serves this release's version.json
        core.store_dpapi_secret(env.fx.tmp / "netlify.dpapi", "nfp_example_token")
        env.cfg["netlify"].update(site_id=SITE, api=server.api, token_file=str(env.fx.tmp / "netlify.dpapi"))
        self.assertEqual(env.main("run", "--skip-kernel", *CONFIRM_ALL), 1, env.text)
        self.assertIn("NETLIFY_VERIFY", env.text)
        self.assertIn("is live and unverified", env.text)                        # published once; the message says so
        self.assertEqual(len([c for c in server.calls if c[1].endswith("/restore")]), 1)
        calls = read_log(env.log)
        self.assertFalse([c for c in calls if c["tool"] == "gh" and "PATCH" in c["argv"]])        # the draft GitHub releases stay drafts
        receipt = json.loads(env.invited["receipt"].read_text())
        self.assertFalse(receipt["contract"]["remote_deploy_confirmed"])                      # and nothing claims a verified deploy
        self.assertNotIn("netlify", json.loads((env.releases() / rid / "state.json").read_text()))

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows only")
    def test_netlify_is_skipped_not_fed_the_full_desk_when_there_is_no_invited_lock(self):
        env = RunEnv(self)
        rid = core.next_release_id([]).rsplit(".", 1)[0] + ".1"
        env.can_gh(rid)
        server = FakeNetlify()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        core.store_dpapi_secret(env.fx.tmp / "netlify.dpapi", "nfp_example_token")
        env.cfg["netlify"].update(site_id=SITE, api=server.api, token_file=str(env.fx.tmp / "netlify.dpapi"))
        full = lock_envelope_bytes(b"full desk")
        env.invited["index"].write_bytes(full)                               # locked, receipt-consistent, but of the full desk
        env.invited["receipt"].write_text(json.dumps({"source": {"sha256": core.sha256_bytes(FULL_DESK)},
                                                      "local_deploy_artifact": {"sha256": core.sha256_bytes(full)}}))
        self.assertEqual(env.main("run", "--skip-kernel", *CONFIRM_ALL), 0, env.text)
        self.assertIn("netlify skipped", env.text)
        self.assertEqual([c for c in server.calls if c[0] in ("POST", "PUT")], [])
        self.assertEqual(server.production, {})
        state = json.loads((env.releases() / rid / "state.json").read_text())
        self.assertNotIn("netlify", state)

    def test_a_release_the_workflow_did_not_make_stops_the_release_before_any_manifest(self):
        env = RunEnv(self)
        rid = core.next_release_id([]).rsplit(".", 1)[0] + ".1"
        env.can_gh(rid)
        path = env.gh / "api_repos_pjg93-222_melange-platform_releases.json"
        path.write_text(path.read_text().replace('"author": {"login": "github-actions[bot]"}', '"author": {"login": "someone"}'))
        self.assertEqual(env.main("run", *CONFIRM_ALL, *FLAGS), 1, env.text)
        self.assertIn("PROVENANCE", env.text)
        self.assertFalse((env.remote / "inbox").exists())                            # nothing was signed or pushed to the VPS

    def test_without_the_vps_trust_file_nothing_is_signed_or_pushed(self):
        env = RunEnv(self)
        rid = core.next_release_id([]).rsplit(".", 1)[0] + ".1"
        env.can_gh(rid)
        env.cfg["signing"].update(allowed_signers=str(env.fx.tmp / "no_such_allowed_signers"))
        self.assertEqual(env.main("run", *CONFIRM_ALL, *FLAGS), 4, env.text)               # not configured
        self.assertIn("signing.allowed_signers", env.text)
        self.assertFalse((env.releases() / rid / "manifest.json").exists())
        self.assertFalse((env.releases() / rid / "manifest.json.sig").exists())
        self.assertFalse((env.remote / "inbox").exists())

    def test_ci_that_did_not_go_green_stops_the_release_before_any_manifest(self):
        env = RunEnv(self)
        rid = core.next_release_id([]).rsplit(".", 1)[0] + ".1"
        env.can_gh(rid)
        (env.gh / "run_list.json").write_text(json.dumps([{"databaseId": 1, "status": "completed", "conclusion": "failure"}]))
        self.assertEqual(env.main("run", *CONFIRM_ALL, *FLAGS), 1, env.text)
        self.assertIn("CI_FAILED", env.text)
        self.assertFalse((env.remote / "inbox").exists())                            # no manifest ever reached the VPS


class ProvenanceTests(Base):
    """verify_provenance with a fake `gh api`: the digests must be the release workflow's own for exactly this commit."""
    COMMIT, TAG, REPO = "a" * 40, "v2026.10.12.1", "pjg93-222/melange-platform"

    def setUp(self):
        self.run = {"id": 1, "head_sha": self.COMMIT, "head_branch": self.TAG, "event": "push", "conclusion": "success",
                    "run_started_at": "2026-10-12T10:00:00Z", "updated_at": "2026-10-12T10:20:00Z"}
        bot = {"login": "github-actions[bot]"}
        self.release = {"created_at": "2026-10-12T10:15:00Z", "author": bot, "assets": [{"name": "digests.json", "uploader": bot}, {"name": "kit.zip", "uploader": bot}]}
        self.doc = {"tag": self.TAG, "commit": self.COMMIT, "images": {}}
        self.calls = []

    def check(self, runs=None, **change):
        runs = [self.run] if runs is None else runs

        def fake(*argv):
            self.calls.append(argv)
            if isinstance(runs, Exception):
                raise runs
            return {"workflow_runs": runs}
        release_doc = {**self.release, **change.pop("release", {})}
        with mock.patch.object(release, "run_json", fake):
            release.verify_provenance(self.REPO, self.COMMIT, self.TAG, release_doc, change.pop("doc", self.doc))

    def refused(self, why, **kw):
        with self.assertRaises(core.ReleaseError, msg=why) as raised:
            self.check(**kw)
        self.assertEqual(raised.exception.code, "PROVENANCE", why)

    def test_the_genuine_release_passes_and_asks_the_runs_for_exactly_that_commit(self):
        self.check()
        self.assertEqual(self.calls, [("gh", "api", f"repos/{self.REPO}/actions/runs?head_sha={self.COMMIT}")])

    def test_a_digests_file_for_another_commit_or_tag_is_refused(self):
        self.refused("commit", doc={**self.doc, "commit": "b" * 40})
        self.refused("tag", doc={**self.doc, "tag": "v2026.10.11.9"})
        self.refused("not a mapping", doc=["digests"])

    def test_no_successful_push_run_of_this_tag_at_this_commit_is_refused(self):
        for label, change in (("other commit", {"head_sha": "b" * 40}), ("other tag", {"head_branch": "main"}),
                              ("a pull request", {"event": "pull_request"}), ("failed", {"conclusion": "failure"}),
                              ("unfinished", {"conclusion": None})):
            self.refused(label, runs=[{**self.run, **change}])
        self.refused("no runs at all", runs=[])

    def test_a_release_that_the_run_did_not_make_is_refused(self):
        human = {"login": "someone"}
        self.refused("release made by a person", release={"author": human})
        self.refused("an asset swapped by a person", release={"assets": [{"uploader": {"login": "github-actions[bot]"}}, {"uploader": human}]})
        self.refused("no assets", release={"assets": []})
        self.refused("made before the run", release={"created_at": "2026-10-12T09:00:00Z"})
        self.refused("made after the run", release={"created_at": "2026-10-12T11:00:00Z"})

    def test_what_cannot_be_read_fails_closed(self):
        self.refused("gh failed", runs=core.ReleaseError("CMD_FAILED", "gh api exited 1"))
        self.refused("no author", release={"author": None})
        self.refused("no timestamps", runs=[{k: v for k, v in self.run.items() if k != "updated_at"}])
        self.refused("no created_at", release={"created_at": None})


class StatusTests(Base):
    def test_status_with_nothing_configured_reports_not_configured_and_exits_nonzero(self):
        cfg = core.load_config(overrides={"releases_dir": str(self.tmpdir() / "r"), "domain": None, "platform_bare": str(self.tmpdir() / "b.git")})
        rid = "2026.10.12.1"
        root = Path(cfg["releases_dir"]) / rid
        root.mkdir(parents=True)
        (root / "manifest.json").write_text(json.dumps({"systems": {}, "packages": {}}))
        (root / "manifest.json.sig").write_text("sig")
        lines = []
        with mock.patch.object(release, "gh_release", return_value=None):          # no network: GitHub answers "nothing"
            code = release.main(["status", rid], cfg=cfg, emit=core.noop_emit, cred=cred, say=lines.append)
        text = "\n".join(lines)
        self.assertEqual(code, 1)
        for needle in ("vps status", "app melange", "app mentat", "app co05", "app or05", "app ct01", "netlify desk", "launcher"):
            self.assertTrue(any(needle in line and "not configured" in line for line in lines), needle + "\n" + text)
        self.assertTrue(any("github release" in line and "fail" in line for line in lines))

    def test_status_with_no_release_at_all_fails_cleanly(self):
        cfg = core.load_config(overrides={"releases_dir": str(self.tmpdir() / "r"), "platform_bare": str(self.tmpdir() / "b.git")})
        lines = []
        self.assertEqual(release.main(["status"], cfg=cfg, emit=core.noop_emit, say=lines.append), 1)
        self.assertIn("no completed release", "\n".join(lines))


class GlassboxTests(Base):
    @unittest.skipUnless((REAL_LIVE / "fec" / "surfaces" / "glassbox_emit.py").is_file(), "live tree not present")
    def test_events_use_the_house_stream_with_the_release_source(self):
        events = self.tmpdir()
        with mock.patch.dict(os.environ, {"GLASSBOX_EVENTS_DIR": str(events)}):
            emit = core.Emitter({"melange_live": str(REAL_LIVE)}, run_id="2026.10.12.1")
            emit("release.step", "started", "gates", stage="gates")
            emit("release.plan", "completed", "plan ready")
        written = [json.loads(p.read_text(encoding="utf-8")) for p in events.glob("evt-*.json")]
        self.assertEqual(sorted(e["kind"] for e in written), ["release.plan", "release.step"])
        step = next(e for e in written if e["kind"] == "release.step")
        self.assertEqual((step["source"], step["status"], step["run_id"], step["stage"]), ("melange-release", "started", "2026.10.12.1", "gates"))

    def test_a_broken_emitter_never_breaks_a_release(self):
        def boom(*_a, **_k):
            raise RuntimeError("stream unavailable")
        core.Emitter({}, fn=boom)("release.step", "started", "x")                      # swallowed
        core.Emitter({"melange_live": "nowhere"})("release.step", "started", "x")      # missing module: swallowed
        with self.assertRaises(core.Pending):
            core.step(core.Emitter({}, fn=boom), "outward", lambda: (_ for _ in ()).throw(core.Pending("x")))


class SmallCommandTests(Base):
    def test_init_signing_prints_the_command_and_generates_nothing(self):
        root = self.tmpdir()
        cfg = core.load_config(overrides={"signing": {"key": str(root / "k")}})
        lines = []
        self.assertEqual(release.main(["init-signing"], cfg=cfg, emit=core.noop_emit, say=lines.append), 0)
        text = "\n".join(lines)
        self.assertIn("ssh-keygen -t ed25519", text)
        self.assertIn("allowed_signers", text)
        self.assertIn('namespaces="melange-release"', text)
        self.assertEqual(list(root.iterdir()), [])                                  # no key was generated

    def test_verify_command_reports_refusals(self):
        root = self.tmpdir()
        subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(root / "k")], check=True)
        allowed = root / "allowed_signers"
        allowed.write_text(f'release@melange namespaces="melange-release" {(root / "k.pub").read_text().strip()}\n')
        cfg = core.load_config(overrides={"signing": {"key": str(root / "k"), "agent": False}})
        digests = {k: "sha256:" + HEX for k in manifest.IMAGE_NAMES}
        doc = manifest.build_manifest(cfg, release_id="2026.10.12.5", previous=None, kind="full", created_utc="2026-10-12T00:00:00Z",
                                      platform_commit="a" * 40, tag="v2026.10.12.5", commits={k: "a" * 40 for k in digests}, digests=digests,
                                      snapshot={"restic_snapshot_id": "abc", "restic_path": "/C/farm", "manifest_sha256": HEX, "files": 1, "bytes": 1},
                                      packages={}, gates={"x": {"ok": True, "detail": ""}})
        path = root / "manifest.json"
        core.atomic_write(path, core.canonical_json(doc))
        manifest.sign_manifest(cfg, path)
        out = []
        say = out.append
        self.assertEqual(release.main(["verify", str(path), "--allowed-signers", str(allowed)], cfg=cfg, emit=core.noop_emit, say=say), 0)
        self.assertEqual(release.main(["verify", str(path), "--allowed-signers", str(allowed), "--current", "2026.10.12.5"], cfg=cfg,
                                      emit=core.noop_emit, say=say), 0)
        self.assertIn("NOOP", "\n".join(out))
        self.assertEqual(release.main(["verify", str(path), "--allowed-signers", str(allowed), "--current", "2026.10.12.9"], cfg=cfg,
                                      emit=core.noop_emit, say=say), 1)
        self.assertIn("DOWNGRADE", "\n".join(out))


if __name__ == "__main__":
    unittest.main()

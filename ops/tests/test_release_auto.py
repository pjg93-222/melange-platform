"""Automatic propagation: `release.py auto`, the `images` map in the manifest, ops/release_lib/current.py.
Same fakes as test_release_status / test_release_images (local git remotes, stubbed security gate, gh/restic/sftp shims, a throwaway
ed25519 key); the clock, Healthchecks, the Mentat consoles and the ssh-agent are injected. No network."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from ops.tests.test_release_common import Base, g, read_log
from ops.tests.test_release_images import ImagesEnv
from ops.tests.test_release_status import CONFIRM_ALL, FLAGS, KEYGEN, cred
from ops.release_lib import auto, core, current, data, export, gates, manifest
from ops import release

HEX = "c" * 64
T0 = time.time() + 7200                                       # "now" for the tests: later than every file the fixtures wrote
CONFIRM = "git,geo,data,manifest,netlify,undraft"
HEALTHCHECKS = "https://hc.invalid/check-id"
COMPUTE = "ghcr.io/pjg93-222/compute-mentat@sha256:" + "4" * 64
TERRIER_BUILD = "ghcr.io/pjg93-222/terrier-build@sha256:" + "5" * 64


class DecideTests(unittest.TestCase):
    A, B = {"melange": "a" * 40, "mentat": "b" * 40, "terrier": "c" * 40}, {"melange": "d" * 40, "mentat": "b" * 40, "terrier": "c" * 40}

    def decide(self, heads, propagated, st=None, *, now=10_000.0, newest=0.0, cap=6, **kw):
        return auto.decide(heads, propagated, st or {}, now=now, newest=newest, quiet_s=1200, cap=cap, **kw)[0]

    def test_unchanged_heads_are_idle_even_inside_the_quiet_period(self):
        self.assertEqual(self.decide(self.A, self.A, newest=9_999.0), "idle")

    def test_a_change_waits_until_the_newest_commit_is_older_than_the_quiet_period(self):
        self.assertEqual(self.decide(self.B, self.A, newest=10_000 - 1199), "wait")
        self.assertEqual(self.decide(self.B, self.A, newest=10_000 - 1200), "go")
        self.assertEqual(self.decide(self.B, self.A, newest=10_000 + 5), "wait")           # a commit from the future: clock skew is not "quiet"

    def test_the_cap_counts_the_last_24_hours_only(self):
        st = {"releases": [10_000 - 100, 10_000 - 200, 10_000 - 86_401]}
        self.assertEqual(self.decide(self.B, self.A, st, cap=3), "go")
        self.assertEqual(self.decide(self.B, self.A, st, cap=2), "capped")

    def test_heads_that_failed_are_not_tried_again_until_forced_or_changed(self):
        st = {"failed": {"heads": self.B, "code": "CI_FAILED", "message": "red"}}
        self.assertEqual(self.decide(self.B, self.A, st), "failed")
        self.assertEqual(self.decide(self.B, self.A, st, force_retry=True), "go")
        self.assertEqual(self.decide({**self.B, "mentat": "e" * 40}, self.A, st), "go")     # a new commit

    def test_force_retry_does_not_lift_the_cap_or_the_quiet_period(self):
        self.assertEqual(self.decide(self.B, self.A, {"releases": [9_990] * 6}, force_retry=True), "capped")
        self.assertEqual(self.decide(self.B, self.A, newest=9_999.0, force_retry=True), "wait")


class SignedBase(Base):
    """A throwaway signing key, its trust file and a config pointing at them."""

    def setUp(self):
        self.dir = self.tmpdir()
        subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "release@melange", "-f", str(self.dir / "key")], check=True)
        self.allowed = self.dir / "allowed_signers"
        self.allowed.write_text(f'release@melange namespaces="melange-release" {(self.dir / "key.pub").read_text().strip()}\n')
        self.cfg = core.load_config(overrides={"signing": {"key": str(self.dir / "key"), "agent": False, "allowed_signers": str(self.allowed)},
                                               "releases_dir": str(self.dir / "releases")})

    def doc(self, **over):
        args = dict(release_id="2026.10.12.2", previous="2026.10.12.1", kind="code-only", created_utc="2026-10-12T00:00:00Z",
                    platform_commit="a" * 40, tag="v2026.10.12.2", commits={k: "b" * 40 for k in manifest.IMAGE_NAMES},
                    digests={k: "sha256:" + HEX for k in manifest.IMAGE_NAMES},
                    snapshot={"restic_snapshot_id": "abcd1234", "restic_path": "/C/farm", "manifest_sha256": HEX, "files": 1, "bytes": 2},
                    packages={}, gates={"security_gate": {"ok": True, "detail": "x"}}, extra_images={"compute-mentat": COMPUTE})
        args.update(over)
        return manifest.build_manifest(self.cfg, **args)


class ManifestImagesTests(SignedBase):
    def test_images_names_every_image_and_agrees_with_systems_which_the_vps_updater_keeps_reading(self):
        doc = self.doc(image_commits={"compute-mentat": "e" * 40})
        self.assertEqual(sorted(doc["images"]), ["compute-mentat", "melange-desk", "mentat-console", "terrier-web"])
        for system, name in manifest.IMAGE_NAMES.items():
            self.assertEqual(doc["images"][name], doc["systems"][system]["image"])
        self.assertEqual(doc["images"]["compute-mentat"], COMPUTE)
        self.assertEqual(doc["image_commits"], {"compute-mentat": "e" * 40})
        self.assertEqual(doc["kind"], "code-only")
        self.assertEqual(doc["schema"], "melange-platform-release-v1")                 # the schema id did not change

    def test_a_manifest_without_images_still_validates_so_older_releases_stay_usable(self):
        doc = self.doc()
        del doc["images"]
        manifest.validate(doc)
        self.assertEqual(current.images_of(doc), {n: doc["systems"][s]["image"] for s, n in manifest.IMAGE_NAMES.items()})

    def test_bad_images_are_refused(self):
        for label, change in (("not pinned", lambda d: d["images"].update({"office-calc": "ghcr.io/pjg93-222/office-calc:latest"})),
                              ("name differs from the repository", lambda d: d["images"].update({"office-calc": COMPUTE})),
                              ("disagrees with systems", lambda d: d["images"].update({"melange-desk": "ghcr.io/pjg93-222/melange-desk@sha256:" + "9" * 64})),
                              ("not a mapping", lambda d: d.update(images=["x"])),
                              ("commit for an unknown image", lambda d: d.update(image_commits={"nope": "a" * 40})),
                              ("bad commit", lambda d: d.update(image_commits={"compute-mentat": "short"})),
                              ("unknown kind", lambda d: d.update(kind="whatever"))):
            with self.subTest(label), self.assertRaises(core.ReleaseError) as raised:
                doc = self.doc()
                change(doc)
                manifest.validate(doc)
            self.assertEqual(raised.exception.code, "MANIFEST_INVALID")

    def test_the_images_map_is_inside_the_signature(self):
        path = self.dir / "manifest.json"
        core.atomic_write(path, core.canonical_json(self.doc()))
        manifest.sign_manifest(self.cfg, path)
        self.assertEqual(manifest.verify_manifest(path, self.allowed)["action"], "apply")
        doc = json.loads(path.read_bytes())
        doc["images"]["compute-mentat"] = "ghcr.io/pjg93-222/compute-mentat@sha256:" + "6" * 64
        core.atomic_write(path, core.canonical_json(doc))
        with self.assertRaises(core.ReleaseError) as raised:
            manifest.verify_manifest(path, self.allowed)
        self.assertEqual(raised.exception.code, "SIGNATURE_BAD")


class CurrentTests(SignedBase):
    """current.image_for: an image comes back only from a manifest that verifies, else nothing."""

    def publish(self, doc=None):
        release_dir = self.dir / "releases" / "2026.10.12.2"
        release_dir.mkdir(parents=True, exist_ok=True)
        path = release_dir / "manifest.json"
        core.atomic_write(path, core.canonical_json(doc or self.doc()))
        manifest.sign_manifest(self.cfg, path)
        return current.publish(self.cfg, release_dir)

    def test_a_published_manifest_answers_by_name(self):
        self.publish()
        self.assertEqual(current.image_for("compute-mentat", self.cfg), COMPUTE)
        self.assertEqual(current.image_for("melange-desk", self.cfg), "ghcr.io/pjg93-222/melange-desk@sha256:" + HEX)

    def test_no_current_manifest_no_answer(self):
        with self.assertRaises(core.ReleaseError) as raised:
            current.image_for("compute-mentat", self.cfg)
        self.assertEqual(raised.exception.code, "NO_CURRENT")

    def test_an_unknown_name_is_refused_with_the_list_of_known_ones(self):
        self.publish()
        with self.assertRaises(core.ReleaseError) as raised:
            current.image_for("office-calc", self.cfg)
        self.assertEqual(raised.exception.code, "NO_SUCH_IMAGE")
        self.assertIn("compute-mentat", raised.exception.message)

    def test_a_tampered_or_unsigned_or_wrongly_signed_manifest_is_refused(self):
        self.publish()
        manifest_file, sig = current.current_dir(self.cfg) / "manifest.json", current.current_dir(self.cfg) / "manifest.json.sig"
        good = manifest_file.read_bytes()
        doc = json.loads(good)
        doc["images"]["compute-mentat"] = "ghcr.io/pjg93-222/compute-mentat@sha256:" + "6" * 64
        manifest_file.write_bytes(core.canonical_json(doc))                             # edited by hand
        with mock.patch.object(current.time, "sleep"), self.assertRaises(core.ReleaseError) as raised:
            current.image_for("compute-mentat", self.cfg)
        self.assertEqual(raised.exception.code, "SIGNATURE_BAD")
        manifest_file.write_bytes(good)
        saved = sig.read_bytes()
        sig.unlink()                                                                    # unsigned
        with mock.patch.object(current.time, "sleep"), self.assertRaises(core.ReleaseError):
            current.image_for("compute-mentat", self.cfg)
        sig.write_bytes(saved)
        other = self.dir / "other"                                                      # signed by a key the trust file does not hold
        subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "release@melange", "-f", str(other)], check=True)
        stranger = core.load_config(overrides={"signing": {"key": str(other), "agent": False, "allowed_signers": str(self.allowed)},
                                               "releases_dir": str(self.dir / "releases")})
        manifest.sign_manifest(stranger, manifest_file)
        with mock.patch.object(current.time, "sleep"), self.assertRaises(core.ReleaseError) as raised:
            current.image_for("compute-mentat", self.cfg)
        self.assertEqual(raised.exception.code, "SIGNATURE_BAD")

    def test_without_the_trust_file_nothing_verifies(self):
        self.publish()
        cfg = core.load_config(overrides={"signing": {"allowed_signers": str(self.dir / "missing")}, "releases_dir": str(self.dir / "releases")})
        with self.assertRaises(core.NotConfigured):
            current.image_for("compute-mentat", cfg)

    def test_publishing_refuses_a_manifest_that_does_not_verify_and_leaves_current_alone(self):
        self.publish()
        before = (current.current_dir(self.cfg) / "manifest.json").read_bytes()
        bad = self.dir / "releases" / "2026.10.12.3"
        bad.mkdir()
        core.atomic_write(bad / "manifest.json", core.canonical_json(self.doc(release_id="2026.10.12.3", tag="v2026.10.12.3")))
        core.atomic_write(bad / "manifest.json.sig", b"not a signature")
        with self.assertRaises(core.ReleaseError):
            current.publish(self.cfg, bad)
        self.assertEqual((current.current_dir(self.cfg) / "manifest.json").read_bytes(), before)

    def test_the_cli_prints_the_image_or_exits_nonzero_with_the_reason(self):
        self.publish()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(core, "load_config", return_value=self.cfg), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(current.main(["image", "compute-mentat"]), 0)
            self.assertEqual(current.main(["image", "nope"]), 1)
            self.assertEqual(current.main([]), 2)
        self.assertEqual(out.getvalue().strip(), COMPUTE)
        self.assertIn("NO_SUCH_IMAGE", err.getvalue())

    def test_run_by_path_with_nothing_published_it_fails_closed(self):
        """What a PowerShell wrapper does: `python <repo>\\ops\\release_lib\\current.py image X`."""
        env = {**__import__("os").environ, "LOCALAPPDATA": str(self.dir / "no-appdata")}
        script = Path(release.__file__).resolve().parent / "release_lib" / "current.py"
        run = subprocess.run([sys.executable, str(script), "image", "compute-mentat"], capture_output=True, text=True, env=env, cwd=str(self.dir))
        self.assertEqual((run.returncode, run.stdout), (1, ""))
        self.assertIn("current:", run.stderr)


class ExtraImagesTests(Base):
    def setUp(self):
        self.cfg = core.load_config()

    def test_extra_images_are_the_names_beyond_the_three_app_images(self):
        doc = {"images": {"melange-desk": "sha256:" + HEX, "terrier-web": "ghcr.io/pjg93-222/terrier-web@sha256:" + HEX,
                          "terrier-build": TERRIER_BUILD, "compute-mentat": {"digest": COMPUTE}}}
        self.assertEqual(release.extra_images(self.cfg, doc), {"terrier-build": TERRIER_BUILD, "compute-mentat": COMPUTE})
        self.assertEqual(release.extra_images(self.cfg, {}), {})

    def test_a_foreign_registry_a_mismatched_name_or_a_floating_tag_is_refused(self):
        for bad in ("docker.io/library/compute-mentat@sha256:" + HEX, "ghcr.io/pjg93-222/other@sha256:" + HEX,
                    "ghcr.io/pjg93-222/compute-mentat:latest", "sha256:" + HEX):
            with self.subTest(bad=bad), self.assertRaises(core.ReleaseError) as raised:
                release.extra_images(self.cfg, {"images": {"compute-mentat": bad}})
            self.assertEqual(raised.exception.code, "DIGESTS_BAD")

    def test_two_builds_of_one_image_that_disagree_are_a_conflict(self):
        into = {"compute-mentat": COMPUTE}
        release.merge_images(into, {"compute-mentat": COMPUTE, "terrier-build": TERRIER_BUILD})
        with self.assertRaises(core.ReleaseError) as raised:
            release.merge_images(into, {"compute-mentat": COMPUTE.replace("4" * 64, "6" * 64)})
        self.assertEqual(raised.exception.code, "DIGEST_CONFLICT")

    def test_a_reused_image_keeps_the_commit_it_was_built_from(self):
        old, new, geo = "a" * 40, "b" * 40, "c" * 40
        base = {"images": {"melange-desk": "ghcr.io/pjg93-222/melange-desk@sha256:" + "1" * 64, "compute-mentat": COMPUTE},
                "systems": {"terrier": {"source": {"commit": geo}}}, "platform_repo": {"commit": old}, "image_commits": {"compute-mentat": "d" * 40}}
        now = {"melange-desk": "ghcr.io/pjg93-222/melange-desk@sha256:" + "8" * 64, "compute-mentat": COMPUTE, "terrier-build": TERRIER_BUILD}
        self.assertEqual(release.image_commits(now, base, new, geo),
                         {"melange-desk": new, "compute-mentat": "d" * 40, "terrier-build": geo})


class ImageHashesTests(Base):
    def test_inputs_that_cannot_be_hashed_here_are_a_note_not_a_failed_release(self):
        """CI hashes on its own and fails its step if it cannot: the laptop's copy is only the report."""
        lines = []
        ctx, plan = mock.Mock(say=lines.append), {"entries": {"ops/image_inputs.json": ("100644", "x")}}
        with mock.patch.object(release, "blob_reader", return_value=lambda path: '{"schema": 1, "images": {}}'),                 mock.patch.object(release.inputs, "all_hashes", side_effect=release.inputs.InputsError("web: 'mentat/app' matches nothing")):
            self.assertEqual(release.image_hashes(ctx, plan), {})
        self.assertIn("not tracked", lines[0])
        self.assertEqual(release.image_hashes(ctx, {"entries": {}}), {})                    # no image_inputs.json in the export yet


class AutoEnv(ImagesEnv):
    """ImagesEnv + a completed base release (written directly: a signed manifest and its snapshot_manifest.json) + an injected clock,
    Healthchecks, Mentat consoles and ssh-agent."""

    def __init__(self, test):
        super().__init__(test)
        fx, self.test = self.fx, test
        launcher = fx.mentat / "mentat" / "app"
        launcher.mkdir(parents=True, exist_ok=True)
        (launcher / "Start-MentatApp.ps1").write_text("# launcher\n")
        g("add", "-A", cwd=fx.mentat)
        g("commit", "-qm", "launcher", cwd=fx.mentat)
        self.now, self.pings, self.launches, self.events, self.consoles, self.agent = T0, [], [], [], {}, ""
        def record(kind, status, summary, **kw):                       # a function, not a bound method: release.py sets run_id on its emitter
            self.events.append((kind, status, summary))
        self.recorder = record
        self.hooks = auto.Hooks(ping=self.pings.append, health=self.health, launch=self.launch, agent_keys=lambda: self.agent)
        self.cfg["auto"] = {"quiet_minutes": 20, "max_per_day": 6, "mentat_restart": True, "mentat_port": 8802, "healthchecks_url": HEALTHCHECKS}
        self.base_id = self.fake_base()
        self.rid = f"{self.base_id.rsplit('.', 1)[0]}.2"

    # -- the world
    def head(self, repo, ref="HEAD"):
        return g("rev-parse", ref, cwd=repo)

    def fake_base(self):
        rid = core.next_release_id([])
        folder = self.releases() / rid
        folder.mkdir(parents=True)
        snapshot = folder / "snapshot_manifest.json"
        snapshot.write_bytes(data.snapshot_manifest(rid, [], "ruled", "d" * 64))
        digests = {"melange": "sha256:" + "1" * 64, "mentat": "sha256:" + "2" * 64, "terrier": "sha256:" + "3" * 64}
        doc = manifest.build_manifest(
            self.cfg, release_id=rid, previous=None, kind="full", created_utc="2026-10-12T00:00:00Z", platform_commit="a" * 40, tag="v" + rid,
            commits={"melange": self.head(self.fx.melange), "mentat": self.head(self.fx.mentat), "terrier": self.geo_commit}, digests=digests,
            snapshot={"restic_snapshot_id": "ab12cd34" * 8, "restic_path": "/C/farm", "manifest_sha256": core.sha256_file(snapshot), "files": 0, "bytes": 0},
            packages={}, gates={"invited_build": {"ok": True, "detail": "x", "governance": "ruled", "profile_sha256": "d" * 64}},
            extra_images={"terrier-build": TERRIER_BUILD})
        core.atomic_write(folder / "manifest.json", core.canonical_json(doc))
        manifest.sign_manifest(self.cfg, folder / "manifest.json")
        return rid

    def commit(self, repo, rel, text, when=None):
        path = Path(repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        g("add", "-A", cwd=repo)
        stamp = f"{int(when if when is not None else T0 - 7200)} +0000"
        g("commit", "-qm", "change", cwd=repo, env={"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp})
        return self.head(repo)

    def melange_change(self, text="print('changed')\n", **kw):
        return self.commit(self.fx.melange, "fec/lf.py", text, **kw)

    def can_ci(self, rid=None, compute=True):
        """gh knows a green CI for `rid`, and the compute workflow's artifact."""
        rid = rid or self.rid
        self.can_gh(rid)
        repo = "api_repos_pjg93-222_melange-platform_actions_runs_1_artifacts"
        (self.gh / f"{repo}.json").write_text(json.dumps({"artifacts": [{"name": "digest-melange-desk", "expired": False}] +
                                                          ([{"name": "compute-images-digests", "expired": False}] if compute else [])}))
        (self.gh / "artifact_compute-images-digests.json").write_text(json.dumps(
            {"tag": "v" + rid, "commit": "@PLATFORM_COMMIT@", "images": {"compute-mentat": COMPUTE}}))

    # -- injected hooks
    def health(self, url):
        return self.consoles.get(int(url.split(":")[2].split("/")[0]))

    def launch(self, argv):
        self.launches.append(list(argv))
        return 0

    def console(self, **over):
        return {"application": "MENTAT", "project": str(Path(self.cfg["mentat_live"]) / "mentat"), "data_root": r"C:\data\console",
                "read_only": False, "busy": False, "pid": 4242, "code_sha256": "a", "on_disk_code_sha256_now": "b",
                "assets_sha256": "c", "on_disk_assets_sha256_now": "c", **over}


    def auto(self, *argv, confirm=CONFIRM):
        return release.main(["auto", *argv, *(["--confirm-outward", confirm] if confirm else [])], cfg=self.cfg, emit=self.recorder, cred=cred,
                            sleep=lambda _s: None, say=self.lines.append, now=lambda: self.now, hooks=self.hooks)

    # -- what happened
    def step_order(self):
        return [s for k, st, s in self.events if k == "release.step" and st == "completed"]

    def patches(self):
        return len([c for c in read_log(self.log) if c["tool"] == "gh" and "PATCH" in c["argv"]])

    def gh_count(self):
        return len([c for c in read_log(self.log) if c["tool"] == "gh"])

    def auto_state(self):
        return json.loads((self.releases() / "auto_state.json").read_text())

    def manifest_of(self, rid):
        return json.loads((self.releases() / rid / "manifest.json").read_bytes())


class AutoTests(Base):
    def test_nothing_changed_is_a_quick_no_op_that_still_pings_healthchecks(self):
        env = AutoEnv(self)
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("auto: idle", env.text)
        self.assertEqual(env.tools_called(), [])
        self.assertEqual(env.pings, [HEALTHCHECKS])
        self.assertEqual([p.name for p in env.releases().iterdir() if p.name[0].isdigit()], [env.base_id])      # no id was spent

    def test_a_melange_change_becomes_a_code_release_published_everywhere_in_order(self):
        env = AutoEnv(self)
        head = env.melange_change()
        env.can_ci()
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn(f"auto code release {env.rid} published", env.text)
        self.assertIn("terrier unchanged, reused", env.text)
        doc = env.manifest_of(env.rid)
        self.assertEqual((doc["kind"], doc["previous"]), ("code-only", env.base_id))
        self.assertEqual(doc["data_snapshot"], env.manifest_of(env.base_id)["data_snapshot"])               # same data, new code
        self.assertEqual(doc["systems"]["melange"]["source"]["commit"], head)
        self.assertEqual(doc["images"]["compute-mentat"], COMPUTE)                                           # from the compute workflow's artifact
        self.assertEqual(doc["images"]["terrier-build"], TERRIER_BUILD)                                      # Geo did not move: carried
        self.assertEqual(doc["images"]["melange-desk"], "ghcr.io/pjg93-222/melange-desk@sha256:" + "1" * 64)
        self.assertEqual(doc["gates"]["invited_build"]["carried_from"], env.base_id)
        self.assertIn("code_only", doc["gates"])
        platform = doc["platform_repo"]["commit"]
        self.assertEqual(doc["image_commits"]["melange-desk"], "a" * 40)                                      # same digest as before: built before
        self.assertEqual(doc["image_commits"]["compute-mentat"], platform)                                    # new to the manifest: this build
        self.assertEqual(manifest.verify_manifest(env.releases() / env.rid / "manifest.json", env.allowed,
                                                  snapshot_path=env.releases() / env.rid / "snapshot_manifest.json")["action"], "apply")
        # published: laptop, VPS inbox, GitHub, in that order; Geo untouched
        self.assertEqual((current.current_dir(env.cfg) / "manifest.json").read_bytes(), (env.releases() / env.rid / "manifest.json").read_bytes())
        self.assertEqual(current.image_for("compute-mentat", env.cfg), COMPUTE)
        inbox = env.remote / "inbox"
        self.assertEqual((inbox / "manifest.json").read_bytes(), (env.releases() / env.rid / "manifest.json").read_bytes())
        self.assertEqual([l for l in (env.remote / "order.log").read_text().split("\n") if l.startswith("rename")],
                         ["rename snapshot_manifest.json", "rename manifest.json.sig", "rename manifest.json"])
        self.assertEqual(g("rev-parse", "refs/tags/v" + env.rid, cwd=env.platform_remote), platform)
        self.assertEqual(g("for-each-ref", cwd=env.geo_remote), "")
        self.assertEqual(env.patches(), 1)                                                                    # the platform release only
        order = env.step_order()
        self.assertLess(order.index("export"), order.index("git push"))
        self.assertLess(order.index("git push"), order.index("ci"))
        self.assertLess(order.index("ci"), order.index("manifest"))
        self.assertLess(order.index("manifest"), order.index("current"))
        self.assertLess(order.index("current"), order.index("inbox"))
        self.assertLess(order.index("inbox"), order.index("undraft"))
        self.assertEqual(env.auto_state()["propagated"]["melange"], head)
        self.assertEqual(env.pings, [HEALTHCHECKS])
        # nothing more to do, and nothing is touched on the second run
        calls = len(read_log(env.log))
        self.assertEqual(env.auto(), 0, env.text)
        self.assertEqual(len(read_log(env.log)), calls)
        self.assertEqual(env.pings, [HEALTHCHECKS, HEALTHCHECKS])

    def test_a_commit_that_releases_nothing_is_marked_propagated_without_a_release(self):
        env = AutoEnv(self)
        env.melange_change()
        env.can_ci()
        self.assertEqual(env.auto(), 0, env.text)
        calls = len(read_log(env.log))
        head = env.commit(env.fx.melange, "fec/docs/NOTES.md", "working notes\n")                            # fec/docs is not exported
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("no exported change", env.text)
        self.assertEqual(env.auto_state()["propagated"]["melange"], head)
        self.assertEqual(len(read_log(env.log)), calls)
        self.assertEqual([p.name for p in env.releases().iterdir() if p.name[0].isdigit()], [env.base_id, env.rid])

    def test_terrier_moving_pushes_geo_and_waits_for_its_ci(self):
        env = AutoEnv(self)
        g("checkout", "-q", "deploy/terrier-hosted", cwd=env.fx.terrier)
        hosted = env.commit(env.fx.terrier, "Dockerfile", "FROM hosted2\n")
        g("checkout", "-q", "claude/walk-list", cwd=env.fx.terrier)
        env.can_ci()
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("terrier changed", env.text)
        refs = g("for-each-ref", "--format=%(refname) %(objectname)", cwd=env.geo_remote).splitlines()
        self.assertEqual(sorted(refs), sorted([f"refs/heads/deploy/terrier-hosted {hosted}", f"refs/tags/v{env.rid} {hosted}"]))
        self.assertEqual(env.manifest_of(env.rid)["systems"]["terrier"]["source"]["commit"], hosted)
        self.assertEqual(env.patches(), 2)
        self.assertEqual(env.auto_state()["propagated"]["terrier"], hosted)

    def test_a_change_still_being_edited_waits(self):
        env = AutoEnv(self)
        env.melange_change(when=T0 - 5 * 60)
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("auto: wait", env.text)
        self.assertEqual(env.tools_called(), [])
        self.assertFalse((env.releases() / env.rid).exists())
        env.now = T0 + 16 * 60                                                                                # 21 minutes after the commit
        env.can_ci()
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("published", env.text)

    def test_at_most_max_per_day_releases_in_24_hours(self):
        env = AutoEnv(self)
        env.cfg["auto"]["max_per_day"] = 1
        env.melange_change("print(1)\n")
        env.can_ci()
        self.assertEqual(env.auto(), 0, env.text)
        env.melange_change("print(2)\n")
        before = len(read_log(env.log))
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("auto: capped", env.text)
        self.assertEqual(len(read_log(env.log)), before)
        env.now += 86_401                                                                                     # a day later the second one goes
        env.rid = env.rid.rsplit(".", 1)[0] + ".3"
        env.can_ci()
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn(f"auto code release {env.rid} published", env.text)

    def test_a_failure_is_not_retried_on_the_same_heads_until_forced_or_changed(self):
        env = AutoEnv(self)
        env.melange_change()
        env.can_ci()
        (env.gh / "run_list.json").write_text(json.dumps([{"databaseId": 7, "status": "completed", "conclusion": "failure"}]))
        self.assertEqual(env.auto(), 1, env.text)
        self.assertIn("CI_FAILED", env.text)
        self.assertEqual(env.pings, [HEALTHCHECKS + "/fail"])
        self.assertFalse((env.releases() / env.rid / "manifest.json").exists())                               # nothing signed or published
        self.assertFalse((env.remote / "inbox").exists())
        self.assertFalse((current.current_dir(env.cfg)).exists())
        self.assertEqual(env.patches(), 0)
        self.assertEqual(env.auto_state()["failed"]["code"], "CI_FAILED")
        calls = len(read_log(env.log))
        self.assertEqual(env.auto(), 0, env.text)                                                             # same heads: skipped, quietly
        self.assertIn("auto: failed", env.text)
        self.assertEqual(len(read_log(env.log)), calls)
        self.assertEqual(env.pings, [HEALTHCHECKS + "/fail"])                                                 # one alert, not one per run
        env.can_ci(env.rid.rsplit(".", 1)[0] + ".3")                                                          # CI is green now
        env.rid = env.rid.rsplit(".", 1)[0] + ".3"
        self.assertEqual(env.auto("--force-retry"), 0, env.text)
        self.assertIn("published", env.text)
        self.assertNotIn("failed", env.auto_state())

    def test_a_new_commit_after_a_failure_is_tried(self):
        env = AutoEnv(self)
        env.melange_change("print(1)\n")
        env.can_ci()
        (env.gh / "run_list.json").write_text(json.dumps([{"databaseId": 7, "status": "completed", "conclusion": "failure"}]))
        self.assertEqual(env.auto(), 1, env.text)
        env.melange_change("print(2)\n")
        rid3 = env.rid.rsplit(".", 1)[0] + ".3"
        env.can_ci(rid3)
        env.rid = rid3
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("published", env.text)

    def test_an_unreachable_ssh_agent_stops_before_anything_is_spent_and_alerts_once(self):
        env = AutoEnv(self)
        env.cfg["signing"]["agent"] = True
        env.agent = None
        env.melange_change()
        env.can_ci()
        self.assertEqual(env.auto(), 4, env.text)
        self.assertIn("ssh-agent is not reachable", env.text)
        self.assertEqual(env.tools_called(), [])
        self.assertIsNone(export.rev(env.cfg["platform_bare"], "refs/tags/v" + env.rid)) if Path(env.cfg["platform_bare"]).exists() else None
        self.assertFalse((env.releases() / env.rid).exists())                                                 # no id spent, no tag, no push
        self.assertEqual(env.pings, [HEALTHCHECKS + "/fail"])
        self.assertEqual(env.auto(), 4, env.text)
        self.assertEqual(env.pings, [HEALTHCHECKS + "/fail"])                                                 # still one alert
        env.agent = "ssh-ed25519 AAAA-some-other-key release@other"                                           # agent up, key not loaded
        self.assertEqual(env.auto(), 4, env.text)
        self.assertIn("not loaded in ssh-agent", env.text)
        pub = (env.fx.tmp / "sign_key.pub").read_text()
        env.agent = pub
        self.assertIsNone(auto.signing_unavailable(env.cfg, env.hooks))
        self.assertEqual(env.auto("--plan"), 0, env.text)
        self.assertIn("signing: ready", env.text)

    def test_an_agent_that_vanishes_during_ci_leaves_the_release_resumable_not_failed(self):
        env = AutoEnv(self)
        env.cfg["signing"]["agent"] = True
        pub = (env.fx.tmp / "sign_key.pub").read_text()
        answers = iter([pub, None, None])
        env.hooks.agent_keys = lambda: next(answers, None)
        env.melange_change()
        env.can_ci()
        self.assertEqual(env.auto(), 4, env.text)                                                             # pushed and green, but cannot sign
        self.assertIn("ssh-agent is not reachable", env.text)
        self.assertNotIn("failed", env.auto_state())
        self.assertEqual(env.auto_state()["inflight"]["id"], env.rid)
        self.assertFalse((env.releases() / env.rid / "manifest.json").exists())
        self.assertFalse((env.remote / "inbox").exists())                                                     # nothing published unverified
        self.assertFalse(current.current_dir(env.cfg).exists())
        self.assertEqual(env.patches(), 0)
        env.cfg["signing"]["agent"] = False                                                                   # key loaded again: sign from the file
        tags = g("for-each-ref", "--format=%(refname) %(objectname)", cwd=env.platform_remote)
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn(f"auto code release {env.rid} published", env.text)                                     # the same id, resumed
        self.assertEqual(g("for-each-ref", "--format=%(refname) %(objectname)", cwd=env.platform_remote), tags)
        self.assertNotIn("inflight", env.auto_state())

    def test_pause_stops_auto_until_unpaused(self):
        env = AutoEnv(self)
        env.melange_change()
        env.can_ci()
        self.assertEqual(env.auto("--pause", confirm=None), 0, env.text)
        self.assertTrue((env.releases() / "auto.pause").is_file())
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("auto is paused", env.text)
        self.assertEqual(env.tools_called(), [])
        self.assertEqual(env.auto("--unpause", confirm=None), 0, env.text)
        self.assertFalse((env.releases() / "auto.pause").exists())
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("published", env.text)

    def test_without_confirm_outward_auto_stops_at_the_first_push(self):
        env = AutoEnv(self)
        env.melange_change()
        self.assertEqual(env.auto(confirm=None), 3, env.text)
        self.assertIn("pending confirmation: git push", env.text)
        self.assertEqual(env.tools_called(), [])
        self.assertEqual(g("for-each-ref", cwd=env.platform_remote), "")
        self.assertEqual(env.auto_state()["inflight"]["id"], env.rid)                                         # resumes on the next confirmed run

    def test_the_vps_push_is_skipped_when_it_is_not_configured_and_github_is_still_published(self):
        env = AutoEnv(self)
        env.cfg["domain"] = None
        env.melange_change()
        env.can_ci()
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("VPS push skipped", env.text)
        self.assertFalse((env.remote / "inbox").exists())
        self.assertEqual(env.patches(), 1)
        self.assertTrue((current.current_dir(env.cfg) / "manifest.json").is_file())

    def test_the_plan_changes_nothing(self):
        env = AutoEnv(self)
        env.melange_change()
        self.assertEqual(env.auto("--plan", confirm=None), 0, env.text)
        self.assertIn("auto: go", env.text)
        self.assertEqual(env.tools_called(), [])
        self.assertFalse((env.releases() / "auto_state.json").exists())
        self.assertEqual(env.pings, [])

    def test_a_hidden_second_instance_does_nothing(self):
        env = AutoEnv(self)
        with auto.single_instance(env.releases() / auto.LOCK) as mine:
            self.assertTrue(mine)
            self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("another auto run is active", env.text)


class MentatRestartTests(Base):
    def run_release(self, env):
        env.commit(env.fx.mentat, "mentat/app/server.py", "print('new server')\n")
        env.can_ci()
        self.assertEqual(env.auto(), 0, env.text)

    def test_an_idle_stale_console_is_replaced_through_the_launcher_after_a_mentat_release(self):
        env = AutoEnv(self)
        env.consoles[8802] = env.console()
        self.run_release(env)
        self.assertEqual(len(env.launches), 1)
        argv = env.launches[0]
        self.assertEqual(argv[0], "powershell.exe")
        self.assertEqual(argv[argv.index("-File") + 1], str(Path(env.cfg["mentat_live"]) / "mentat" / "app" / "Start-MentatApp.ps1"))
        self.assertEqual(argv[argv.index("-Port") + 1], "8802")
        self.assertEqual(argv[argv.index("-DataRoot") + 1], r"C:\data\console")
        self.assertIn("-NoBrowser", argv)
        self.assertNotIn("mentat_restart", env.auto_state())

    def test_a_busy_read_only_current_or_foreign_console_is_never_restarted_and_a_busy_one_is_retried(self):
        env = AutoEnv(self)
        env.consoles = {8802: env.console(busy=True), 8803: env.console(read_only=True), 8804: env.console(on_disk_code_sha256_now="a"),
                        8805: env.console(project=r"C:\somewhere\else")}
        self.run_release(env)
        self.assertEqual(env.launches, [])
        self.assertTrue(env.auto_state()["mentat_restart"])                                                  # still owed
        env.consoles[8802] = env.console(busy=False)                                                          # it finished its work
        self.assertEqual(env.auto(), 0, env.text)
        self.assertEqual([a[a.index("-Port") + 1] for a in env.launches], ["8802"])
        self.assertNotIn("mentat_restart", env.auto_state())

    def test_it_can_be_switched_off(self):
        env = AutoEnv(self)
        env.cfg["auto"]["mentat_restart"] = False
        env.consoles[8802] = env.console()
        self.run_release(env)
        self.assertEqual(env.launches, [])

    def test_melange_and_terrier_changes_only_say_a_restart_is_needed(self):
        env = AutoEnv(self)
        env.melange_change()
        env.can_ci()
        self.assertEqual(env.auto(), 0, env.text)
        needed = [e for e in env.events if e[0] == "release.restart_needed"]
        self.assertEqual(len(needed), 1)
        self.assertIn("Melange console (8841)", needed[0][2])
        self.assertEqual(env.launches, [])


class DataTests(Base):
    def stub_kernel(self):
        patch = mock.patch.object(gates, "kernel_tests", return_value={"ok": True, "detail": "stub"})
        patch.start()
        self.addCleanup(patch.stop)

    def refresh(self, env, stamp):
        (env.fx.melange / "data" / "engine" / "weekly_refresh_status.json").write_text(json.dumps({"last_outcome": "COMPLETE", "last_update": stamp}))

    def test_a_completed_refresh_triggers_one_data_only_release_and_a_pending_governance_blocks_it(self):
        self.stub_kernel()
        env = AutoEnv(self)
        first, second = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 - days * 86400)) for days in (3, 2))   # the refresh is long over: quiet
        self.refresh(env, first)
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("data baseline recorded", env.text)                                                     # what exists now is the baseline
        self.assertEqual(env.tools_called(), [])
        self.assertEqual(env.auto(), 0, env.text)
        self.assertEqual(env.tools_called(), [])                                                              # and it is not re-released
        self.refresh(env, second)
        build = env.invited["invited"] / "INVITED_BUILD.json"
        ruled = build.read_text()
        build.write_text(ruled.replace('"ruled"', '"pending"'))
        self.assertEqual(env.auto(), 0, env.text)
        self.assertIn("data release not started", env.text)                                                   # a refusal is a note, not a failure
        self.assertIn("invited_build", env.text)
        self.assertNotIn("data_failed", env.auto_state())
        self.assertFalse((env.releases() / env.rid).exists())
        build.write_text(ruled)
        self.assertEqual(env.auto(), 0, env.text)
        doc = env.manifest_of(env.rid)
        self.assertEqual((doc["kind"], doc["previous"]), ("data-only", env.base_id))
        self.assertEqual(doc["systems"], env.manifest_of(env.base_id)["systems"])                             # images carried
        self.assertEqual(doc["images"], env.manifest_of(env.base_id)["images"])
        self.assertEqual((current.current_dir(env.cfg) / "manifest.json").read_bytes(), (env.releases() / env.rid / "manifest.json").read_bytes())
        self.assertIn("netlify not in this data release", env.text)                                           # not configured here: skipped with a note
        self.assertEqual(env.auto_state()["data"]["refresh"], second)
        before = len(read_log(env.log))
        self.assertEqual(env.auto(), 0, env.text)
        self.assertEqual(len(read_log(env.log)), before)                                                      # once per refresh


class PublishCurrentTests(Base):
    def test_a_manual_full_run_also_makes_its_manifest_current_and_rollback_points_back(self):
        env = AutoEnv(self)
        rid = env.rid
        env.can_gh(rid)
        self.assertEqual(env.main("run", *CONFIRM_ALL, *FLAGS), 0, env.text)
        cfg = env.cfg
        self.assertEqual(current.load(cfg)["release_id"], rid)
        self.assertEqual(env.main("current", "--use", env.base_id), 0, env.text)
        self.assertEqual(current.load(cfg)["release_id"], env.base_id)
        self.assertIn(env.base_id, env.text)
        self.assertEqual(env.main("current", "--use", "2000.01.01.1"), 1, env.text)
        self.assertEqual(current.load(cfg)["release_id"], env.base_id)                                        # unchanged by the refusal
        self.assertEqual(env.main("current"), 0, env.text)
        self.assertIn("melange-desk", env.text)


if __name__ == "__main__":
    unittest.main()

"""Manifest: canonical bytes, ssh-keygen sign/verify (temp ed25519 key), tamper, downgrade, no-op, lock envelope,
reader downloads, Pocket gating."""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

from ops.tests.test_release_common import Base, REAL_LIVE, lock_envelope_bytes, write_invited
from ops.release_lib import core, data, manifest

KEYGEN = manifest.keygen()
HEX = "a" * 64
COMMIT = "b" * 40


def envelope(salt=16, iv=12, ct=4096, n=250000) -> bytes:
    """What lock_dashboard writes, as far as both lock checks can tell: AES-GCM page, incompressible ciphertext."""
    b64 = lambda raw: base64.b64encode(raw).decode()
    body = b"".join(hashlib.sha256(b"ct" + bytes([i])).digest() for i in range(ct // 32 + 1))[:ct]
    return (f'<html><script>var D={{s:"{b64(bytes([1]) * salt)}",i:"{b64(bytes([1]) * iv)}",c:"{b64(body)}",n:{n}}};'
            "/* AES-GCM */</script></html>").encode()


class ManifestTests(Base):
    def setUp(self):
        self.dir = self.tmpdir()
        self.key = self.dir / "key"
        subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "release@melange", "-f", str(self.key)], check=True)
        pub = (self.dir / "key.pub").read_text().strip()
        self.allowed = self.dir / "allowed_signers"
        self.allowed.write_text(f'release@melange namespaces="melange-release" {pub}\n')
        self.cfg = core.load_config(overrides={"signing": {"key": str(self.key), "agent": False}})

    def doc(self, release_id="2026.10.12.2", previous="2026.10.12.1", snapshot_sha=HEX):
        digests = {k: "sha256:" + HEX for k in manifest.IMAGE_NAMES}
        return manifest.build_manifest(
            self.cfg, release_id=release_id, previous=previous, kind="full", created_utc="2026-10-12T00:00:00Z",
            platform_commit=COMMIT, tag="v" + release_id, commits={k: COMMIT for k in manifest.IMAGE_NAMES}, digests=digests,
            snapshot={"restic_snapshot_id": "abcd1234", "restic_path": "/C/farm", "manifest_sha256": snapshot_sha, "files": 3, "bytes": 99},
            packages={"kit.zip": {"sha256": HEX, "bytes": 5}}, gates={"disk": {"ok": True, "detail": "x"}})

    def write(self, doc, name="manifest.json") -> Path:
        path = self.dir / name
        core.atomic_write(path, core.canonical_json(doc))
        return path

    def test_contract_shape_and_canonical_bytes(self):
        doc = self.doc()
        self.assertEqual(doc["schema"], "melange-platform-release-v1")
        self.assertEqual(doc["platform_repo"], {"repo": "pjg93-222/melange-platform", "commit": COMMIT, "tag": "v2026.10.12.2"})
        self.assertEqual(doc["systems"]["terrier"]["services"], ["terrier-co05", "terrier-or05", "terrier-ct01"])
        self.assertEqual(doc["systems"]["melange"]["source"], {"repo": "local:melange-systems", "ref": "main", "commit": COMMIT})
        self.assertEqual(doc["systems"]["mentat"]["source"]["ref"], "codex/mentat-crypto")
        self.assertEqual(doc["systems"]["terrier"]["source"]["repo"], "pjg93-222/Geo")
        self.assertTrue(doc["systems"]["mentat"]["image"].startswith("ghcr.io/pjg93-222/mentat-console@sha256:"))
        self.assertEqual(doc["signer"], "release@melange")
        blob = core.canonical_json({"b": "é", "a": [1, 2]})
        self.assertEqual(blob, b'{"a":[1,2],"b":"\xc3\xa9"}')                       # sorted keys, no spaces, UTF-8 not escaped

    def test_sign_then_verify_ok_and_tamper_fails(self):
        path = self.write(self.doc())
        manifest.sign_manifest(self.cfg, path)
        ok = manifest.verify_manifest(path, self.allowed)
        self.assertEqual(ok["action"], "apply")
        path.write_bytes(path.read_bytes().replace(b'"files":3', b'"files":4'))     # one byte flipped
        with self.assertRaises(core.ReleaseError) as raised:
            manifest.verify_manifest(path, self.allowed)
        self.assertEqual(raised.exception.code, "SIGNATURE_BAD")

    def test_wrong_signer_is_refused(self):
        path = self.write(self.doc())
        manifest.sign_manifest(self.cfg, path)
        other = self.dir / "other"
        subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
        self.allowed.write_text(f'release@melange namespaces="melange-release" {(self.dir / "other.pub").read_text().strip()}\n')
        with self.assertRaises(core.ReleaseError) as raised:
            manifest.verify_manifest(path, self.allowed)
        self.assertEqual(raised.exception.code, "SIGNATURE_BAD")

    def test_downgrade_refused_equal_is_noop_newer_applies(self):
        path = self.write(self.doc("2026.10.12.2"))
        manifest.sign_manifest(self.cfg, path)
        with self.assertRaises(core.ReleaseError) as raised:
            manifest.verify_manifest(path, self.allowed, current_id="2026.10.12.10")        # numeric, not lexical
        self.assertEqual(raised.exception.code, "DOWNGRADE")
        self.assertEqual(manifest.verify_manifest(path, self.allowed, current_id="2026.10.12.2")["action"], "noop")
        self.assertEqual(manifest.verify_manifest(path, self.allowed, current_id="2026.10.12.1")["action"], "apply")

    def test_snapshot_manifest_hash_is_bound(self):
        snap = self.dir / "snapshot_manifest.json"
        snap.write_bytes(data.snapshot_manifest("2026.10.12.2", [{"path": "m/a", "size": 1, "sha256": HEX, "mtime_ns": 5}], "ruled", HEX))
        path = self.write(self.doc(snapshot_sha=core.sha256_file(snap)))
        manifest.sign_manifest(self.cfg, path)
        self.assertEqual(manifest.verify_manifest(path, self.allowed, snapshot_path=snap)["action"], "apply")
        snap.write_bytes(snap.read_bytes() + b" ")
        with self.assertRaises(core.ReleaseError) as raised:
            manifest.verify_manifest(path, self.allowed, snapshot_path=snap)
        self.assertEqual(raised.exception.code, "SNAPSHOT_MISMATCH")

    def test_validation_rejects_unpinned_images_and_bad_ids(self):
        good = self.doc()
        for mutate in (lambda d: d["systems"]["mentat"].update(image="ghcr.io/pjg93-222/mentat-console:latest"),
                       lambda d: d.update(previous="2026.10.12.2"),
                       lambda d: d.update(kind="partial"),
                       lambda d: d["systems"]["melange"]["source"].update(commit="abc"),
                       lambda d: d.update(gates={}),
                       lambda d: d["data_snapshot"].pop("restic_snapshot_id"),
                       lambda d: d["data_snapshot"].pop("restic_path"),
                       lambda d: d["data_snapshot"].update(restic_path="C:/Users/x/farm"),
                       lambda d: d["data_snapshot"].update(restic_path="/C/../x")):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            with self.assertRaises(core.ReleaseError) as raised:
                manifest.validate(bad)
            self.assertEqual(raised.exception.code, "MANIFEST_INVALID")

    def test_validation_refuses_a_trailing_newline_or_a_non_ascii_digit_on_every_checked_shape(self):
        """A `$` anchor accepts a final newline and a digit class accepts any Unicode digit; the VPS (fullmatch, [0-9]) refuses both."""
        good = self.doc()
        nl = "\n"
        for mutate in (lambda d: d.update(release_id="2026.10.12.2" + nl),
                       lambda d: d.update(release_id="٢026.10.12.2"),
                       lambda d: d["platform_repo"].update(tag="v2026.10.12.2" + nl),
                       lambda d: d["platform_repo"].update(commit=COMMIT + nl),
                       lambda d: d["systems"]["mentat"].update(image=d["systems"]["mentat"]["image"] + nl),
                       lambda d: d["systems"]["melange"]["source"].update(commit=COMMIT + nl),
                       lambda d: d["data_snapshot"].update(manifest_sha256=HEX + nl),
                       lambda d: d["data_snapshot"].update(restic_path="/C/farm" + nl),
                       lambda d: d["packages"]["kit.zip"].update(sha256=HEX + nl)):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            with self.assertRaises(core.ReleaseError):
                manifest.validate(bad)
        for rid in ("2026.10.12.1" + nl, "٢026.10.12.1"):
            with self.assertRaises(core.ReleaseError) as raised:
                core.parse_id(rid)
            self.assertEqual(raised.exception.code, "BAD_ID")
        self.assertEqual(core.parse_id("2026.10.12.1"), (2026, 10, 12, 1))

    def test_lock_envelope_parsing(self):
        locked = self.dir / "locked.html"
        locked.write_bytes(envelope())
        self.assertTrue(manifest.is_lock_envelope(locked))
        smuggled = envelope() + b"<pre>" + b"the full dashboard, readable by anyone " * 3000 + b"</pre>"
        for name, body in (("plain.html", b"<html>Moneyball dashboard</html>"), ("shortsalt.html", envelope(salt=8)),
                           ("fewiters.html", envelope(n=10)), ("noct.html", envelope(ct=8)),
                           ("plaintext_appended.html", smuggled),                       # security_gate's wrapper cap, not just the regex
                           ("no_aes_marker.html", envelope().replace(b"AES-GCM", b"plain")),
                           ("compressible_ct.html", envelope().replace(envelope().split(b'c:"')[1].split(b'"')[0], b"A" * 5464))):
            (self.dir / name).write_bytes(body)
            self.assertFalse(manifest.is_lock_envelope(self.dir / name), name)

    def test_downloads_only_locked_files_sums_and_no_email_package(self):
        desk, plain_pocket = self.dir / "desk.html", self.dir / "pocket.html"
        desk.write_bytes(envelope())
        plain_pocket.write_bytes(b"<html>plain pocket</html>")
        result = manifest.build_downloads(self.cfg, "2026.10.12.2", self.dir / "pkg", desk, plain_pocket)
        names = sorted(Path(f).name for f, _d in result["extra"])
        self.assertEqual(names, ["SHA256SUMS", "SHA256SUMS", "melange-desk-2026.10.12.2.html", "melange-desk.html"])   # <id>/ and latest/
        self.assertTrue(any("mentat-pocket omitted" in n for n in result["notes"]))
        sums = (self.dir / "pkg" / "downloads" / "2026.10.12.2" / "SHA256SUMS").read_text()
        self.assertEqual(sums, f"{core.sha256_file(desk)}  melange-desk-2026.10.12.2.html\n")
        self.assertEqual(result["extra"][0][1], "melange/downloads/2026.10.12.2/melange-desk-2026.10.12.2.html")
        # desk_server serves /downloads/latest/<name> from a real downloads/latest/ directory (a snapshot holds no symlinks)
        self.assertEqual([d for _f, d in result["extra"][2:]],
                         ["melange/downloads/latest/melange-desk.html", "melange/downloads/latest/SHA256SUMS"])
        latest = self.dir / "pkg" / "downloads" / "latest"
        self.assertEqual((latest / "melange-desk.html").read_bytes(), desk.read_bytes())
        self.assertEqual((latest / "SHA256SUMS").read_text(), f"{core.sha256_file(desk)}  melange-desk.html\n")
        # the email package can never be shipped, even if a workbook entry points into it
        mail = self.dir / "outputs" / "email_package"
        mail.mkdir(parents=True)
        (mail / "book.xlsx").write_bytes(b"x")
        cfg = dict(self.cfg, downloads={"workbooks": [str(mail / "book.xlsx")]})
        with self.assertRaises(core.ReleaseError) as raised:
            manifest.build_downloads(cfg, "2026.10.12.3", self.dir / "pkg", desk, None)
        self.assertEqual(raised.exception.code, "DENYLIST")
        # a secret-named file handed to the shared resolver as an "extra" is refused too
        secret = self.dir / "dashboard_password.txt"
        secret.write_text("x")
        with self.assertRaises(core.ReleaseError) as raised:
            data.resolve_files(self.cfg, {"systems": {}}, extra=[(secret, "melange/downloads/x/dashboard_password.txt")])
        self.assertEqual(raised.exception.code, "DENYLIST")


class InvitedDeskTests(Base):
    """D-171/R3: only a lock of the invited build's Moneyball.html is ever pinned for downloads and Netlify."""

    def setUp(self):
        self.live = self.tmpdir() / "live"
        self.live.mkdir()
        self.fx = write_invited(self.live)
        self.cfg = {"melange_live": str(self.live)}
        self.out = self.tmpdir() / "stage" / "index.html"

    def test_pins_the_exact_bytes_of_the_invited_lock(self):
        got = manifest.pin_invited_desk(self.cfg, self.out)
        self.assertEqual(self.out.read_bytes(), self.fx["lock"])
        self.assertEqual(got["sha256"], core.sha256_bytes(self.fx["lock"]))
        self.assertEqual(got["source_sha256"], core.sha256_bytes(self.fx["html"]))

    def refuses(self, why):
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_bytes(b"left by an earlier attempt")
        with self.assertRaises(manifest.NoInvitedLock, msg=why):
            manifest.pin_invited_desk(self.cfg, self.out)
        self.assertFalse(self.out.exists(), why)                                     # a refusal leaves nothing to upload

    def test_a_lock_of_the_full_desk_is_never_pinned(self):
        full = lock_envelope_bytes(b"full desk")
        self.fx["index"].write_bytes(full)
        self.fx["receipt"].write_text(json.dumps({"source": {"sha256": core.sha256_bytes(b"the full Moneyball.html")},
                                                  "local_deploy_artifact": {"sha256": core.sha256_bytes(full)}}))
        self.refuses("receipt names another source")

    def test_other_broken_facts_are_refused_too(self):
        self.fx["index"].write_bytes(b"<html>plain</html>")
        self.refuses("not a lock envelope")
        self.fx["index"].write_bytes(self.fx["lock"] + b" ")
        self.refuses("receipt does not match the lock")
        self.fx["index"].write_bytes(self.fx["lock"])
        (self.fx["invited"] / "Moneyball.html").write_bytes(b"<html>edited after the build</html>")
        self.refuses("invited Moneyball.html differs from INVITED_BUILD.json and the receipt")
        (self.fx["invited"] / "INVITED_BUILD.json").unlink()
        self.refuses("no INVITED_BUILD.json")
        self.fx["receipt"].unlink()
        self.refuses("no receipt")


class PocketTests(Base):
    def make(self, selfcheck_exit):
        root = self.tmpdir()
        tools = root / "mentat" / "mentat" / "tools" / "pocket"
        tools.mkdir(parents=True)
        (tools / "selfcheck.py").write_text(
            "import sys\nprint('ALL CHECKS PASSED' if %d == 0 else '2 CHECK(S) FAILED')\nsys.exit(%d)\n" % (selfcheck_exit, selfcheck_exit))
        pocket = root / "reports" / "pocket"
        pocket.mkdir(parents=True)
        (pocket / "mentat-pocket.html").write_text("<html>POCKET-PLAINTEXT-MARKER " + "x" * 200 + "</html>")      # >= 48 bytes of ciphertext
        cfg = core.load_config(overrides={"mentat_live": str(root / "mentat"), "melange_live": str(REAL_LIVE),
                                          "pocket": {"dir": str(pocket), "console_port": None}})
        return cfg, root

    def test_failed_selfcheck_stops_the_release(self):
        cfg, root = self.make(1)
        with self.assertRaises(core.ReleaseError) as raised:
            manifest.build_pocket(cfg, root / "out" / "index.html", core.Secret("pw"))
        self.assertEqual(raised.exception.code, "POCKET_SELFCHECK")
        self.assertFalse((root / "out" / "index.html").exists())

    @unittest.skipUnless((REAL_LIVE / "fec" / "surfaces" / "lock_dashboard.py").is_file(), "live tree not present")
    def test_passing_selfcheck_is_locked_with_lock_dashboard(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography not installed")
        cfg, root = self.make(0)
        out = manifest.build_pocket(cfg, root / "out" / "index.html", core.Secret("correct horse"))
        self.assertTrue(manifest.is_lock_envelope(root / "out" / "index.html"))
        self.assertNotIn(b"POCKET-PLAINTEXT-MARKER", (root / "out" / "index.html").read_bytes())      # plaintext is not in the file
        self.assertEqual(out["sha256"], core.sha256_file(root / "out" / "index.html"))

    def test_unbuilt_pocket_is_not_configured_not_a_crash(self):
        cfg, root = self.make(0)
        (Path(cfg["pocket"]["dir"]) / "mentat-pocket.html").unlink()
        with self.assertRaises(core.NotConfigured):
            manifest.build_pocket(cfg, root / "o.html", core.Secret("pw"))

    def test_password_file_is_required_and_never_printed(self):
        with self.assertRaises(core.NotConfigured):
            manifest.dashboard_password({"dashboard_password_file": str(self.tmpdir() / "missing")})
        path = self.tmpdir() / "pw.txt"
        path.write_text("s3cret\n")
        secret = manifest.dashboard_password({"dashboard_password_file": str(path)})
        self.assertEqual(secret.reveal(), "s3cret")
        self.assertNotIn("s3cret", repr(secret) + str(secret) + f"{secret}")


if __name__ == "__main__":
    unittest.main()

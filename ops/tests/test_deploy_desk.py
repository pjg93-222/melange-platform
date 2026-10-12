"""ops/deploy_desk.py offline: preflight, staging, dry run, and the full flow against the loopback fake Netlify."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

from ops.tests.test_release_common import Base
from ops.tests.test_release_netlify import SITE, TOKEN, FakeNetlify
from ops import deploy_desk
from ops.release_lib import core, web


def envelope() -> bytes:
    b64 = lambda n: base64.b64encode(os.urandom(n)).decode()
    return f'<input type=password><script>var D={{s:"{b64(16)}",i:"{b64(12)}",c:"{b64(4096)}",n:250000}};AES-GCM</script>'.encode()


class DeployDeskTests(Base):
    def setUp(self):
        self.root = self.tmpdir()
        self.built = core.invited_dir({"melange_live": self.root})            # outputs/invited_build: where the builder writes it
        self.built.mkdir(parents=True)
        (self.root / "deploy").mkdir()
        (self.root / "data" / "engine").mkdir(parents=True)
        self.index = self.root / "deploy" / "index.html"
        self.index.write_bytes(envelope())
        self.invited(governance="pending")
        self.receipt(index_sha=core.sha256_file(self.index), source_sha="a" * 64)
        self.stage = self.tmpdir()
        self.gate = mock.patch.object(deploy_desk, "run_gate").start()
        self.addCleanup(mock.patch.stopall)

    def invited(self, governance):
        (self.built / "INVITED_BUILD.json").write_text(json.dumps(
            {"governance": governance, "outputs": {"Moneyball.html": {"sha256": "a" * 64}}}), encoding="utf-8")

    def receipt(self, index_sha, source_sha):
        (self.root / "data" / "engine" / "dashboard_deployment_receipt.json").write_text(json.dumps(
            {"status": "LOCAL_DEPLOY_ARTIFACT_READY_NOT_REMOTE", "source": {"sha256": source_sha},
             "local_deploy_artifact": {"sha256": index_sha}, "contract": {"remote_deploy_confirmed": False}}),
            encoding="utf-8")

    def argv(self, *extra):
        return ["--site-id", SITE, "--root", str(self.root), "--stage-dir", str(self.stage),
                "--allow-pending-governance", *extra]

    def test_preflight_refusals(self):
        with self.assertRaises(core.ReleaseError) as cm:
            deploy_desk.preflight(self.root, allow_pending=False)
        self.assertEqual(cm.exception.code, "GOVERNANCE_PENDING")
        self.receipt(index_sha="0" * 64, source_sha="a" * 64)
        with self.assertRaises(core.ReleaseError) as cm:
            deploy_desk.preflight(self.root, allow_pending=True)
        self.assertEqual(cm.exception.code, "RECEIPT_MISMATCH")
        self.receipt(index_sha=core.sha256_file(self.index), source_sha="b" * 64)
        with self.assertRaises(core.ReleaseError) as cm:
            deploy_desk.preflight(self.root, allow_pending=True)
        self.assertEqual(cm.exception.code, "NOT_INVITED")
        self.index.write_bytes(b"<html>plain desk</html>")
        with self.assertRaises(core.ReleaseError) as cm:
            deploy_desk.preflight(self.root, allow_pending=True)
        self.assertEqual(cm.exception.code, "NOT_LOCKED")

    def test_stage_maps_exact_bytes_and_is_repeatable(self):
        facts = deploy_desk.preflight(self.root, allow_pending=True)
        for _ in range(2):
            files = deploy_desk.stage(facts, self.stage)
        self.assertEqual(sorted(files), ["/index.html", "/version.json"])
        self.assertEqual(files["/index.html"], self.stage / "deploy" / "index.html")   # the gate's envelope check needs deploy/
        self.assertEqual(core.sha256_file(files["/index.html"]), facts["index_sha256"])
        version = json.loads(files["/version.json"].read_text(encoding="utf-8"))
        self.assertEqual(version, {"release_id": "desk-" + facts["index_sha256"][:16], "index_sha256": facts["index_sha256"]})

    def test_dry_run_never_asks_for_the_token(self):
        prompt = mock.Mock(side_effect=AssertionError("token asked in a dry run"))
        self.assertEqual(deploy_desk.main(self.argv("--dry-run"), prompt=prompt), 0)
        self.gate.assert_called_once_with(self.stage)

    def test_gate_failure_stops_before_the_token(self):
        self.gate.side_effect = core.ReleaseError("SECURITY_GATE", "x")
        prompt = mock.Mock(side_effect=AssertionError("token asked after a failed gate"))
        self.assertEqual(deploy_desk.main(self.argv(), prompt=prompt), 1)

    def test_full_flow_publishes_verifies_and_confirms_the_receipt(self):
        server = FakeNetlify()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with mock.patch.object(core, "utc_now", return_value="2026-10-12T00:00:00Z"):
            self.assertEqual(deploy_desk.main(self.argv(), prompt=lambda _: TOKEN, api_url=server.api), 0)
        self.assertTrue(server.restored)
        self.assertEqual(server.production["/index.html"], self.index.read_bytes())
        receipt = json.loads((self.root / "data" / "engine" / "dashboard_deployment_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "REMOTE_DEPLOY_CONFIRMED")
        self.assertEqual(receipt["contract"]["remote_deploy_id"], "d1")
        self.assertTrue(all(auth == "Bearer " + TOKEN for _, path, auth in server.calls if path.startswith("/api/")))
        self.assertNotIn(TOKEN, " ".join(sys.argv))
        for folder in (self.root, self.stage):
            for path in folder.rglob("*"):
                if path.is_file():
                    self.assertNotIn(TOKEN.encode(), path.read_bytes(), path)
        # a re-run finds this desk live: it verifies and confirms, and creates no second deploy
        posts = len([c for c in server.calls if c[0] == "POST"])
        real = web.Netlify._call

        def with_published(api, method, path, *args, **kwargs):
            out = real(api, method, path, *args, **kwargs)
            return {**out, "published_deploy": {"id": "d1"}} if path == f"/sites/{SITE}" else out
        with mock.patch.object(web.Netlify, "_call", with_published):
            self.assertEqual(deploy_desk.main(self.argv(), prompt=lambda _: TOKEN, api_url=server.api), 0)
        self.assertEqual(len([c for c in server.calls if c[0] == "POST"]), posts)

    def test_rejected_upload_exits_5_and_publishes_nothing(self):
        server = FakeNetlify()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        server.reject_with = 413
        self.assertEqual(deploy_desk.main(self.argv(), prompt=lambda _: TOKEN, api_url=server.api), 5)
        self.assertFalse(server.restored)

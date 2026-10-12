"""Tests for ops/security_gate.py. Temp dirs only; scanner tools are replaced by a tiny fake.

Run: python -m unittest ops.tests.test_security_gate -v      (or pytest ops/tests/test_security_gate.py)
Sensitive-looking fixtures (SSN, emails) are built at run time so this file passes its own gate.
"""
import base64
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import security_gate as gate  # noqa: E402

FAKE_TOOL = r'''
import json, os, sys
name, a = sys.argv[1], sys.argv[2:]
arg = lambda flag: a[a.index(flag) + 1]
if "--version" in a or (a[:1] == ["version"] and "--format" not in a):
    print("fake-%s 0.0.0" % name); sys.exit(0)
if name == "gitleaks":
    out = os.environ.get("FAKE_GITLEAKS", "[]")
    open(arg("--report-path"), "w").write(out)
    sys.exit(1 if json.loads(out) else 0)
if name == "trivy":
    if a[:1] == ["version"]:
        db = os.environ["FAKE_TRIVY_DB"]
        print(json.dumps({"Version": "0", "VulnerabilityDB": {} if db == "none" else {"UpdatedAt": db}})); sys.exit(0)
    out = os.environ.get("FAKE_TRIVY", '{"Results": []}')
    open(arg("--output"), "w").write(out)
    sys.exit(1 if json.loads(out).get("Results") else 0)
sys.exit(2)
'''
SSN = "-".join(["123", "45", "6789"])
SECRET = "fake-" * 5  # low entropy on purpose; the gate ignores values under 12 chars
DIGEST = "ghcr.io/example/app@sha256:" + "a" * 64
NOW = dt.datetime.now(dt.timezone.utc)


def compliant_service(**over):
    svc = {"read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "user": "10001:10001"}
    svc.update(over)
    return svc


def envelope(blob: bytes) -> str:
    b64 = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return ('<html><script>var D={s:"%s",i:"%s",c:"%s",n:250000};/* AES-GCM */</script></html>'
            % (b64(os.urandom(16)), b64(os.urandom(12)), b64(blob)))


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tree = self.tmp / "tree"
        (self.tree / "pkg").mkdir(parents=True)
        (self.tree / "pkg" / "mod.py").write_text("print('hello')\n", encoding="utf-8")
        (self.tree / "README.md").write_text("# readme\n", encoding="utf-8")
        self.fake = self.tmp / "fake_tool.py"
        self.fake.write_text(FAKE_TOOL, encoding="utf-8")
        self.keys = self.tmp / "keys.json"
        self.keys.write_text(json.dumps({"svc": {"keys": [SECRET], "note": "free text with spaces"}}), encoding="utf-8")
        self.sources = json.dumps({"keys_json": str(self.keys)})
        self.canary = next(x.strip() for x in gate.SEC.joinpath("canaries.txt").read_text(encoding="utf-8").splitlines()
                           if x.strip() and not x.startswith("#"))

    def run_gate(self, *argv, tools=("gitleaks",), env=None):
        finder = lambda n: [sys.executable, str(self.fake), n] if n in tools else None  # noqa: E731
        out = io.StringIO()
        with mock.patch.object(gate, "find_tool", finder), mock.patch.dict(os.environ, env or {}), \
                contextlib.redirect_stdout(out):
            rc = gate.main([str(a) for a in argv])
        return rc, out.getvalue()

    def export(self, *extra, ci=True, **kw):
        base = ["export", "--target", self.tree]
        base += ["--ci"] if ci else ["--secret-sources", self.sources]
        return self.run_gate(*base, *extra, **kw)

    def toml(self, body):
        p = self.tmp / "exceptions.toml"
        p.write_text(body, encoding="utf-8")
        return p

    def exception(self, **over):
        f = {"id": "EX-1", "stage": "export", "check": "data_leak", "match": "data/*.csv", "reason": "test fixture",
             "approver": "Paul", "expires": str(dt.date.today() + dt.timedelta(days=10)), "decision": "D-150"}
        f.update(over)
        return "[[exception]]\n" + "".join(f'{k} = "{v}"\n' for k, v in f.items())

    # ---- export: clean and planted

    def test_clean_tree_passes_ci_and_local(self):
        rc, out = self.export()
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP export/exact_secrets", out)
        self.assertIn("RESULT PASS", out)
        rc, out = self.export(ci=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS export/exact_secrets", out)

    def test_planted_data_each_fail(self):
        cases = {
            "data/rows.csv": b"a,b\n1,2\n", "db/app.sqlite": b"SQLite format 3\0",
            "cfg/keys.json": b"{}", "notes/ssn.txt": f"id {SSN} end\n".encode(),
            "notes/canary.txt": f"x {self.canary} y\n".encode(),
            "downloads/r1/index.html": b"<html><body>plain dashboard</body></html>",
            "deploy/index.html": b"<html>" + b"x" * 100 + b"</html>",
            "big/blob.txt": b"a" * (1_300_000),
        }
        for rel, content in cases.items():
            with self.subTest(rel=rel):
                p = self.tree / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(content)
                rc, out = self.export("--max-file-mb", 1)
                p.unlink()
                self.assertEqual(rc, 1, out)
                self.assertIn("FAIL export/data_leak", out)
                self.assertIn(rel, out)
        rc, out = self.export("--max-file-mb", 1)
        self.assertEqual(rc, 0, out)  # tree is clean again

    def test_farm_stage_allows_data_but_never_a_secret_or_a_secret_name(self):
        farm = self.tmp / "farm"
        for rel in ("melange/rows.csv", "mentat/scores.sqlite3", "mentat/state/big.parquet", "terrier/co-05/bundle.zip"):
            (farm / rel).parent.mkdir(parents=True, exist_ok=True)
            (farm / rel).write_bytes(b"data\n")
        rc, out = self.run_gate("farm", "--target", farm, "--secret-sources", self.sources)
        self.assertEqual(rc, 0, out)                                             # data files are expected here
        self.assertNotIn("data_leak", out)
        self.assertIn("PASS farm/denied_names", out)
        self.assertIn("PASS farm/exact_secrets", out)
        (farm / "mentat" / "keys.json").write_text("{}")
        rc, out = self.run_gate("farm", "--target", farm, "--secret-sources", self.sources)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL farm/denied_names", out)
        self.assertIn("mentat/keys.json", out)
        (farm / "mentat" / "keys.json").unlink()
        (farm / "melange" / "notes.txt").write_text("copied by mistake: " + SECRET)
        rc, out = self.run_gate("farm", "--target", farm, "--secret-sources", self.sources)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL farm/exact_secrets", out)
        self.assertIn("melange/notes.txt", out)
        self.assertNotIn(SECRET, out)                                            # the name of the source, never the value

    def test_farm_stage_without_secret_sources_or_a_target_fails_closed(self):
        farm = self.tmp / "farm"
        farm.mkdir()
        rc, out = self.run_gate("farm", "--target", farm, "--secret-sources", json.dumps({"keys_json": str(self.tmp / "nope.json")}))
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL farm/exact_secrets", out)
        rc, out = self.run_gate("farm")
        self.assertEqual(rc, 1, out)

    def test_containers_and_dump_formats_the_gate_cannot_read_are_denied(self):
        for name in ("backup.gz", "data.tgz", "dump.tar", "x.7z", "x.bz2", "x.xz", "x.rar", "fit.rds", "fit.rda", "ws.RData",
                     "cols.feather", "arr.npz", "dump.sql", "old.bak", "mix.tar.gz"):
            with self.subTest(name):
                p = self.tree / "assets" / name
                p.parent.mkdir(exist_ok=True)
                p.write_bytes(b"opaque")
                rc, out = self.export()
                p.unlink()
                self.assertEqual(rc, 1, out)
                self.assertIn("FAIL export/data_leak", out)
                self.assertIn("assets/" + name + ": denied extension", out)
        self.assertEqual(self.export()[0], 0)

    def test_locked_html_envelope_passes_and_forgery_fails(self):
        p = self.tree / "downloads" / "r1" / "index.html"
        p.parent.mkdir(parents=True)
        p.write_text(envelope(os.urandom(4000)), encoding="utf-8")
        rc, out = self.export()
        self.assertEqual(rc, 0, out)
        p.write_text(envelope(b"plain csv-like text, easily compressed. " * 300), encoding="utf-8")  # base64 of plain data
        rc, out = self.export()
        self.assertEqual(rc, 1, out)
        self.assertIn("encrypted-envelope", out)

    def test_pii_thresholds_allow_normal_source(self):
        mails = "\n".join(f"u{i}@{d}" for i, d in enumerate(["example.com"] * 40))
        (self.tree / "ok.py").write_text(f"# {mails}\nowner = 'a@" + "umn.edu'\n", encoding="utf-8")
        self.assertEqual(self.export()[0], 0)
        (self.tree / "leak.txt").write_text("\n".join(f"p{i}@" + f"mail{i}.org" for i in range(30)), encoding="utf-8")
        rc, out = self.export()
        self.assertEqual(rc, 1)
        self.assertIn("30 email addresses", out)
        self.assertNotIn("mail7.org", out)  # counts and paths only

    # ---- exact secrets

    def test_exact_secret_reports_name_not_value(self):
        (self.tree / "pkg" / "oops.py").write_text(f"TOKEN = '{SECRET}'\n", encoding="utf-8")
        report = self.tmp / "report.json"
        rc, out = self.export("--report", report, ci=False)
        self.assertEqual(rc, 1)
        self.assertIn("pkg/oops.py: matches secret keys.json:svc.keys[0]", out)
        self.assertNotIn(SECRET, out)
        self.assertNotIn(SECRET, report.read_text(encoding="utf-8"))
        self.assertNotIn("free text", out)  # prose leaves are not secrets

    def test_exact_secret_found_in_utf16_and_ci_skips(self):
        (self.tree / "pkg" / "w.txt").write_bytes(SECRET.encode("utf-16-le"))
        self.assertEqual(self.export(ci=False)[0], 1)
        self.assertEqual(self.export(ci=True)[0], 0)  # CI: SKIP, real values do not exist there

    def test_no_secret_sources_loaded_fails_closed(self):
        rc, out = self.run_gate("export", "--target", self.tree, "--secret-sources", "{}")
        self.assertEqual(rc, 1)
        self.assertIn("FAIL export/exact_secrets", out)

    def sources_with(self, **extra):
        return json.dumps({"keys_json": str(self.keys), **extra})

    def local_run(self, sources, patches=()):
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return self.run_gate("export", "--target", self.tree, "--secret-sources", sources)

    def test_every_named_secret_source_must_exist_a_missing_one_is_a_failure_not_a_skip(self):
        missing = str(self.tmp / "nowhere")
        for label, spec, said in (("keys.json", {"keys_json": missing}, "keys.json is missing"),
                                  ("fec dir", {"fec_dir": missing}, "the fec key directory is missing"),
                                  ("password", {"password_files": [missing + ".txt"]}, "password file nowhere.txt is missing")):
            with self.subTest(label):
                rc, out = self.local_run(self.sources_with(**spec) if label != "keys.json" else json.dumps(spec))
                self.assertEqual(rc, 1, out)
                self.assertIn("FAIL export/exact_secrets", out)
                self.assertIn(said, out)
                self.assertNotIn("SKIP", out)
        rc, out = self.local_run(self.sources_with(password_files=[str(self.keys)]))
        self.assertEqual(rc, 0, out)                                              # present sources pass, nothing is silently ignored

    def test_ci_is_the_only_opt_out(self):
        rc, out = self.run_gate("export", "--target", self.tree, "--ci", "--secret-sources", json.dumps({"keys_json": str(self.tmp / "x")}))
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP export/exact_secrets", out)

    def release_config(self, token_file="", credentials=None, in_use=True):
        """A release config that names the token file and credentials; `in_use` also sets netlify.site_id and domain."""
        path = self.tmp / "release_config.json"
        path.write_text(json.dumps({"netlify": {"token_file": str(token_file), "site_id": "site-1" if in_use else None},
                                    "domain": "example.test" if in_use else None, "credentials": credentials or {}}), encoding="utf-8")
        return str(path)

    def test_dpapi_file_and_credential_manager_are_local_sources_and_values_are_never_printed(self):
        blob = self.tmp / "netlify.dpapi"
        blob.write_bytes(b"opaque")
        config = self.release_config(blob, {"restic": "melange-restic"})
        creds = {"melange-restic": ("restic-user", "RESTIC-password-1234567")}
        read = mock.patch.object(gate, "read_credential", lambda target: creds.get(target))
        dpapi = mock.patch.object(gate, "dpapi_decrypt", lambda path: "nfp_the_netlify_token_1234567")
        spec = self.sources_with(dpapi_files=[str(blob)], credman=["melange-restic"], release_config=config)
        rc, out = self.local_run(spec, (read, dpapi))
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS export/exact_secrets: 3 secret source(s) checked", out)
        (self.tree / "pkg" / "a.py").write_text("T = 'nfp_the_netlify_token_1234567'\n", encoding="utf-8")
        (self.tree / "pkg" / "b.py").write_text("R = 'RESTIC-password-1234567'\n", encoding="utf-8")
        rc, out = self.local_run(spec, (read, dpapi))
        self.assertEqual(rc, 1, out)
        self.assertIn("pkg/a.py: matches secret netlify.dpapi", out)
        self.assertIn("pkg/b.py: matches secret melange-restic", out)
        self.assertNotIn("nfp_the_netlify_token", out)
        self.assertNotIn("RESTIC-password", out)

    def test_an_absent_local_source_fails_only_when_the_release_config_references_it(self):
        blob = self.tmp / "netlify.dpapi"
        nobody = mock.patch.object(gate, "read_credential", lambda target: None)
        referenced = self.release_config(blob, {"push": "melange-svc-push"})
        spec = self.sources_with(dpapi_files=[str(blob)], credman=["melange-svc-push"], release_config=referenced)
        rc, out = self.local_run(spec, (nobody,))
        self.assertEqual(rc, 1, out)
        self.assertIn("netlify.dpapi is referenced by the release config but is missing", out)
        self.assertIn("credential melange-svc-push is referenced by the release config but is not in Credential Manager", out)
        unreferenced = self.release_config(blob, {"push": "melange-svc-push"}, in_use=False)     # named, but no site_id or domain yet
        rc, out = self.local_run(self.sources_with(dpapi_files=[str(blob)], credman=["melange-svc-push"], release_config=unreferenced), (nobody,))
        self.assertEqual(rc, 0, out)
        self.assertIn("netlify.dpapi not configured", out)
        self.assertIn("credential melange-svc-push not configured", out)

    def test_a_dpapi_file_that_cannot_be_decrypted_is_a_failure(self):
        blob = self.tmp / "netlify.dpapi"
        blob.write_bytes(b"not a blob")
        spec = self.sources_with(dpapi_files=[str(blob)], release_config=self.release_config(blob))
        boom = mock.patch.object(gate, "dpapi_decrypt", mock.Mock(side_effect=OSError("secret details")))
        rc, out = self.local_run(spec, (boom,))
        self.assertEqual(rc, 1, out)
        self.assertIn("netlify.dpapi cannot be decrypted", out)
        self.assertNotIn("secret details", out)

    # ---- terrier_no_local_admin: an exact sentinel, not any message that mentions "admin"

    def terrier_probe(self, returncode, stdout):
        g = mock.Mock()
        g.args.terrier_dir = str(self.tmp)
        g.tmp = self.tmp
        done = mock.Mock(returncode=returncode, stdout=stdout.encode(), stderr=b"")
        with mock.patch.object(gate.subprocess, "run", return_value=done):
            return gate.check_terrier_no_local_admin(g)

    def test_terrier_startup_refusal_is_accepted_only_as_the_exact_sentinel(self):
        refusal = ("SETTINGS: RuntimeError: CU_LOCAL_ADMIN=1 is refused in a hosted deployment "
                   "(it grants an owner without a password).")
        self.assertEqual(self.terrier_probe(3, refusal)[0], [])
        for out in ("SETTINGS: ModuleNotFoundError: No module named 'django.contrib.admin'",       # says admin, is not the refusal
                    "SETTINGS: RuntimeError: the admin settings module is broken",
                    "SETTINGS: ValueError: CU_LOCAL_ADMIN=1 is refused in a hosted deployment",     # right words, wrong exception
                    "Traceback: CU_LOCAL_ADMIN=1 is refused in a hosted deployment",
                    refusal + "\nSETTINGS: also admin trouble", ""):
            with self.subTest(out=out), self.assertRaises(gate.GateError):
                self.terrier_probe(3, out)

    def test_terrier_clean_start_must_print_the_flags_it_asserted(self):
        self.assertEqual(self.terrier_probe(0, "warning\nok HOSTED=True LOCAL_ADMIN_ACCESS=False")[0], [])
        for rc, out in ((0, "ok"), (0, "ok HOSTED=False LOCAL_ADMIN_ACCESS=False"), (0, "ok HOSTED=True LOCAL_ADMIN_ACCESS=True"),
                        (0, "broken"), (1, "ASSERT: LOCAL_ADMIN_ACCESS is enabled under hosted"),
                        (1, "ok HOSTED=True LOCAL_ADMIN_ACCESS=False")):
            with self.subTest(rc=rc, out=out), self.assertRaises(gate.GateError):
                self.terrier_probe(rc, out)

    # ---- tools

    def test_gitleaks_absent_fails_naming_it(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": str(empty)}), mock.patch.object(gate, "BIN_DIR", empty), \
                contextlib.redirect_stdout(out):
            rc = gate.main(["export", "--ci", "--target", str(self.tree)])
        self.assertEqual(rc, 1)
        self.assertRegex(out.getvalue(), r"FAIL export/gitleaks: gitleaks not found")

    def test_gitleaks_finding_fails_without_printing_secret(self):
        leak = [{"RuleID": "generic-api-key", "File": str(self.tree / "pkg" / "mod.py"), "StartLine": 1,
                 "Secret": "SHOULD-NEVER-PRINT", "Match": "SHOULD-NEVER-PRINT"}]
        report = self.tmp / "r.json"
        rc, out = self.export("--report", report, env={"FAKE_GITLEAKS": json.dumps(leak)})
        self.assertEqual(rc, 1)
        self.assertIn("pkg/mod.py: gitleaks rule generic-api-key line 1", out)
        self.assertNotIn("SHOULD-NEVER-PRINT", out + report.read_text(encoding="utf-8"))

    def test_all_stage_with_no_tools_fails_every_required_tool(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": str(empty)}), mock.patch.object(gate, "BIN_DIR", empty), \
                contextlib.redirect_stdout(out):
            rc = gate.main(["all", "--ci", "--target", str(self.tree), "--lockfile", str(self.tmp / "x.txt")])
        text = out.getvalue()
        self.assertEqual(rc, 1)
        for name in ("gitleaks", "npm_audit", "bandit", "semgrep", "terrier_check", "trivy", "compose_policy"):
            self.assertRegex(text, rf"FAIL \w+/{name}:")
        self.assertIn("pip-audit not found", text)
        self.assertIn("osv-scanner", text)

    # ---- exceptions

    def run_with_exceptions(self, body, *extra, ci=True):
        (self.tree / "data").mkdir(exist_ok=True)
        (self.tree / "data" / "rows.csv").write_text("a,b\n", encoding="utf-8")
        return self.export("--exceptions", self.toml(body), *extra, ci=ci)

    def test_valid_exception_suppresses_only_its_match(self):
        rc, out = self.run_with_exceptions(self.exception())
        self.assertEqual(rc, 0, out)
        self.assertIn("1 suppressed by EX-1", out)
        (self.tree / "data" / "other.sqlite").write_bytes(b"x")
        rc, out = self.export("--exceptions", self.toml(self.exception()))
        self.assertEqual(rc, 1)
        self.assertIn("data/other.sqlite", out)
        self.assertNotIn("data/rows.csv: denied", out)

    def test_expired_exception_fails_and_does_not_suppress(self):
        past = str(dt.date.today() - dt.timedelta(days=1))
        rc, out = self.run_with_exceptions(self.exception(expires=past))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL export/exceptions", out)
        self.assertIn("expired", out)
        self.assertIn("data/rows.csv: denied extension .csv", out)

    def test_exception_rules(self):
        far = str(dt.date.today() + dt.timedelta(days=40))
        for label, over, want in [
            ("approver", {"approver": "Claude"}, "approver must be exactly 'Paul'"),
            ("high too far", {"expires": far}, "max 30 for HIGH"),
            ("bad decision", {"decision": "decision-1"}, "decision must look like D-###"),
            ("unknown check", {"check": "nope"}, "check must be one of"),
        ]:
            with self.subTest(label):
                rc, out = self.run_with_exceptions(self.exception(**over))
                self.assertEqual(rc, 1)
                self.assertIn(want, out)
        rc, out = self.run_with_exceptions(self.exception(expires=far, severity="LOW", stage="static", check="bandit"))  # others get 90 days
        self.assertIn("PASS export/exceptions", out)
        rc, out = self.run_with_exceptions("[[exception]\nbroken")
        self.assertEqual((rc, "malformed" in out), (1, True))

    def test_a_secret_check_exception_is_always_high_whatever_severity_it_claims(self):
        far = str(dt.date.today() + dt.timedelta(days=40))
        soon = str(dt.date.today() + dt.timedelta(days=10))
        for check in ("gitleaks", "data_leak", "exact_secrets", "denied_names"):
            for severity in ("LOW", "MEDIUM"):
                with self.subTest(check=check, severity=severity):
                    rc, out = self.run_with_exceptions(self.exception(check=check, severity=severity, expires=far))
                    self.assertEqual(rc, 1, out)
                    self.assertIn("max 30 for HIGH", out)
            rc, out = self.run_with_exceptions(self.exception(check=check, severity="LOW", expires=soon))
            self.assertIn("PASS export/exceptions", out)                              # still available, for a short while

    def test_a_match_made_only_of_wildcards_is_rejected(self):
        for match in ("*", "**", "*/*", "?", "**/*.*", "[*]"):
            with self.subTest(match=match):
                rc, out = self.run_with_exceptions(self.exception(match=match))
                self.assertEqual(rc, 1, out)
                self.assertIn("match is only wildcards", out)
                self.assertIn("data/rows.csv: denied extension .csv", out)           # and it suppressed nothing
        rc, out = self.run_with_exceptions(self.exception(match="data/*.csv"))
        self.assertEqual(rc, 0, out)

    def test_decision_must_exist_in_brain_on_local_runs_only(self):
        brain = self.tmp / "DECISIONS.md"
        brain.write_text("## D-149 — other\n", encoding="utf-8")
        rc, out = self.run_with_exceptions(self.exception(), "--decisions", brain, ci=False)
        self.assertEqual(rc, 1)
        self.assertIn("D-150 not found in DECISIONS.md", out)
        brain.write_text("## D-149 — other\n## D-150 — this one\n", encoding="utf-8")
        rc, out = self.run_with_exceptions(self.exception(), "--decisions", brain, ci=False)
        self.assertEqual(rc, 0, out)
        rc, out = self.run_with_exceptions(self.exception(), "--decisions", self.tmp / "absent.md", ci=True)
        self.assertEqual(rc, 0, out)  # CI never consults the brain

    def test_shipped_exceptions_are_narrow_and_wellformed(self):
        # D-176 ships 13 gitleaks entries; each must name one file and one rule, never a directory or a bare wildcard.
        # Fixed "today" keeps this structural (the gate itself fails on an expired entry); ci=True skips the brain lookup.
        valid, problems = gate.load_exceptions(gate.SEC / "exceptions.toml", True, "x", dt.date(2026, 10, 12))
        self.assertEqual(problems, [])
        for e in valid:
            self.assertEqual((e["stage"], e["check"], e["decision"], e["severity"]), ("export", "gitleaks", "D-176", "HIGH"))
            self.assertRegex(e["match"], r"^[A-Za-z0-9_./-]+: gitleaks rule generic-api-key line \*$")

    # ---- packages

    def package(self, name, members):
        p = self.tmp / name
        with zipfile.ZipFile(p, "w") as z:
            for n, data in members.items():
                z.writestr(n, data)
        return p

    def test_package_stage(self):
        ok = self.package("ok.zip", {"kit/install.sh": "echo hi\n"})
        rc, out = self.run_gate("packages", "--ci", "--target", ok)
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS packages/safe_unpack", out)
        bad = self.package("data.zip", {"kit/rows.csv": "a,b\n"})
        rc, out = self.run_gate("packages", "--ci", "--target", bad)
        self.assertEqual(rc, 1)
        self.assertIn("data.zip!/kit/rows.csv: denied extension .csv", out)

    def test_traversal_and_links_are_refused(self):
        evil = self.package("evil.zip", {"../evil.txt": "pwn"})
        rc, out = self.run_gate("packages", "--ci", "--target", evil)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL packages/safe_unpack", out)
        self.assertIn("unsafe member path", out)
        self.assertFalse((self.tmp / "evil.txt").exists())
        for kind, make in {"traversal": lambda t: t.addfile(tarfile.TarInfo("../x.txt"), io.BytesIO(b"")),
                           "symlink": self.tar_symlink}.items():
            with self.subTest(kind):
                tgz = self.tmp / f"{kind}.tar.gz"
                with tarfile.open(tgz, "w:gz") as t:
                    make(t)
                rc, out = self.run_gate("packages", "--ci", "--target", tgz)
                self.assertEqual(rc, 1)
                self.assertIn("FAIL packages/safe_unpack", out)
        win = self.package("win.zip", {"..\\evil.txt": "pwn", "C:/abs.txt": "x"})
        self.assertEqual(self.run_gate("packages", "--ci", "--target", win)[0], 1)

    @staticmethod
    def tar_symlink(t):
        info = tarfile.TarInfo("link")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        t.addfile(info)

    def test_an_archive_inside_a_package_is_refused_because_it_would_never_be_scanned(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("rows.txt", "inside")
        outer = self.package("outer.zip", {"kit/install.sh": "echo hi\n", "kit/payload.zip": inner.getvalue()})
        rc, out = self.run_gate("packages", "--ci", "--target", outer)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL packages/safe_unpack", out)
        self.assertIn("nested archive 'kit/payload.zip'", out)
        # an archive cannot vouch for its own contents: a SHA256SUMS inside it does not excuse the nested zip
        sums = f"{hashlib.sha256(inner.getvalue()).hexdigest()}  payload.zip\n"
        sneaky = self.package("sneaky.zip", {"payload.zip": inner.getvalue(), "SHA256SUMS": sums})
        self.assertEqual(self.run_gate("packages", "--ci", "--target", sneaky)[0], 1)
        tgz = self.tmp / "nest.tar.gz"
        with tarfile.open(tgz, "w:gz") as t:
            info = tarfile.TarInfo("deep/more.tgz")
            info.size = 4
            t.addfile(info, io.BytesIO(b"abcd"))
        rc, out = self.run_gate("packages", "--ci", "--target", tgz)
        self.assertEqual(rc, 1, out)
        self.assertIn("nested archive 'deep/more.tgz'", out)

    def test_kit_zip_allowed_only_when_listed_in_sha256sums(self):
        kit = self.tmp / "kit"
        kit.mkdir()
        z = kit / "install.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("a.txt", "hi")
        rc, out = self.run_gate("export", "--ci", "--target", kit)
        self.assertEqual(rc, 1)
        self.assertIn("denied extension .zip", out)
        (kit / "SHA256SUMS").write_text(f"{gate.sha256_file(z)}  install.zip\n", encoding="utf-8")
        rc, out = self.run_gate("export", "--ci", "--target", kit)
        self.assertEqual(rc, 0, out)
        (kit / "SHA256SUMS").write_text(f"{'0' * 64}  install.zip\n", encoding="utf-8")  # wrong hash
        self.assertEqual(self.run_gate("export", "--ci", "--target", kit)[0], 1)

    # ---- compose policy and images

    def test_compose_policy(self):
        good = {"services": {
            "app": compliant_service(ports=[{"host_ip": "127.0.0.1", "target": 80, "published": "8080"}],
                                     volumes=[{"type": "bind", "source": "/srv/data/x", "target": "/d",
                                               "read_only": True}]),
            "cloudflared": {"image": "cf"}}}
        self.assertEqual(gate.compose_findings(good), [])
        cases = {
            "0.0.0.0 port": {"app": compliant_service(ports=[{"host_ip": "0.0.0.0", "target": 80, "published": "80"}])},
            "unspecified host_ip": {"app": compliant_service(ports=[{"target": 80, "published": "80"}])},
            "short port syntax": {"app": compliant_service(ports=["8080:80"])},
            "missing cap_drop": {"app": {k: v for k, v in compliant_service().items() if k != "cap_drop"}},
            "cap_drop not ALL": {"app": compliant_service(cap_drop=["NET_RAW"])},
            "not read_only": {"app": compliant_service(read_only=False)},
            "no no-new-privileges": {"app": compliant_service(security_opt=[])},
            "root user": {"app": compliant_service(user="0:0")},
            "no user": {"app": {k: v for k, v in compliant_service().items() if k != "user"}},
            "rw data mount": {"app": compliant_service(volumes=[{"type": "bind", "source": "/srv/data/x", "target": "/d"}])},
            "rw data mount short": {"app": compliant_service(volumes=["/srv/data/x:/d"])},
            "traversal into data": {"app": compliant_service(volumes=[{"type": "bind", "source": "/srv/data/../data/x",
                                                                      "target": "/d"}])},
            "cloudflared still no public ports": {"cloudflared": {"ports": ["443:443"]}},
            "privileged": {"app": compliant_service(privileged=True)},
        }
        for label, services in cases.items():
            with self.subTest(label):
                self.assertTrue(gate.compose_findings({"services": services}), label)
        self.assertEqual(gate.compose_findings({"services": {"app": compliant_service(
            volumes=["/srv/data/x:/d:ro"], ports=["127.0.0.1:8080:80"])}}), [])
        with self.assertRaises(gate.GateError):
            gate.compose_findings({"services": {}})

    def images(self, compose, *extra, tools=("trivy",), env=None):
        cj = self.tmp / "compose.json"
        cj.write_text(json.dumps(compose), encoding="utf-8")
        env = {"FAKE_TRIVY_DB": NOW.isoformat(), **(env or {})}
        return self.run_gate("images", "--ci", "--image", DIGEST, "--compose-json", cj, *extra, tools=tools, env=env)

    def test_images_stage(self):
        good = {"services": {"app": compliant_service()}}
        rc, out = self.images(good)
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS images/compose_policy", out)
        rc, out = self.images({"services": {"app": compliant_service(ports=[{"host_ip": "0.0.0.0", "target": 80}])}})
        self.assertEqual(rc, 1)
        self.assertIn("FAIL images/compose_policy", out)
        vuln = {"Results": [{"Target": "usr/lib/libx", "Vulnerabilities": [
            {"VulnerabilityID": "CVE-2026-0001", "PkgName": "libx", "Severity": "CRITICAL"}]}]}
        rc, out = self.images(good, env={"FAKE_TRIVY": json.dumps(vuln)})
        self.assertEqual(rc, 1)
        self.assertIn("CVE-2026-0001 libx CRITICAL", out)

    def test_images_fail_closed_cases(self):
        good = {"services": {"app": compliant_service()}}
        stale = (NOW - dt.timedelta(hours=30)).isoformat()
        rc, out = self.images(good, env={"FAKE_TRIVY_DB": stale})
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"FAIL images/trivy: trivy vulnerability DB is 30 h old")
        rc, out = self.images(good, env={"FAKE_TRIVY_DB": "none"})        # a DB whose age cannot be told is not a fresh DB
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"FAIL images/trivy: trivy vulnerability DB has no timestamp")
        rc, out = self.images(good, tools=())
        self.assertRegex(out, r"FAIL images/trivy: trivy not found")
        rc, out = self.run_gate("images", "--ci", "--image", "ghcr.io/example/app:latest", tools=("trivy",))
        self.assertIn("not pinned by digest", out)
        self.assertIn("FAIL images/compose_policy: no --compose-json given", out)
        self.assertEqual(rc, 1)

    def test_report_is_written(self):
        report = self.tmp / "out" / "report.json"
        report.parent.mkdir()
        rc, _ = self.export("--report", report)
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((rc, data["result"], data["stage"]), (0, "PASS", "export"))
        self.assertEqual({c["check"] for c in data["checks"]}, {"exceptions", "gitleaks", "data_leak", "exact_secrets"})
        self.assertIn("gitleaks", data["tools"])


    # --- exact_secrets: one pass over each file, and a cache of files already scanned clean ---

    def test_find_needles_names_every_needle_in_one_pass(self):
        a, b, c = "alpha-secret-value-1", "bravo-secret-value-2", "charlie-secret-value-3"
        needles = {"A": [a.encode(), a.encode("utf-16-le")], "B": [b.encode(), b.encode("utf-16-le")],
                   "C": [c.encode(), c.encode("utf-16-le")]}
        f = self.tmp / "multi.bin"
        f.write_bytes(b"noise " + a.encode() + b" more noise " + b.encode("utf-16-le") + b" tail")
        self.assertEqual(gate.find_needles(f, needles), {"A", "B"})
        f.write_bytes(b"nothing here")
        self.assertEqual(gate.find_needles(f, needles), set())

    def test_find_needles_sees_a_needle_split_across_the_chunk_boundary(self):
        needle = "boundary-secret-value-xyz".encode()
        needles = {"N": [needle]}
        f = self.tmp / "edge.bin"
        for split in (1, len(needle) // 2, len(needle) - 1):                   # the 1 MiB chunk ends `split` bytes into the needle
            f.write_bytes(b"x" * ((1 << 20) - split) + needle + b"y" * 100)
            self.assertEqual(gate.find_needles(f, needles), {"N"}, split)
        f.write_bytes(b"x" * ((1 << 20) - 3) + needle[:-1] + b"Z" + b"y" * 100)   # one byte off: no hit
        self.assertEqual(gate.find_needles(f, needles), set())

    def farm(self):
        farm = self.tmp / "farm"
        for rel in ("melange/a.txt", "mentat/b.txt", "terrier/c.txt"):
            (farm / rel).parent.mkdir(parents=True, exist_ok=True)
            (farm / rel).write_text("clean " + rel)
        return farm

    def farm_scan(self, farm, cache, sources=None):
        with mock.patch.object(gate, "find_needles", wraps=gate.find_needles) as spy:
            rc, out = self.run_gate("farm", "--target", farm, "--secret-sources", sources or self.sources, "--scan-cache", cache)
        return rc, out, spy.call_count

    def test_the_scan_cache_skips_files_already_clean_and_rescans_a_changed_one(self):
        farm, cache = self.farm(), self.tmp / "cache" / "scan.sqlite"
        rc, out, scanned = self.farm_scan(farm, cache)
        self.assertEqual((rc, scanned), (0, 3), out)                              # first pass reads every file
        rc, out, scanned = self.farm_scan(farm, cache)
        self.assertEqual((rc, scanned), (0, 0), out)                              # unchanged: nothing is read again
        self.assertIn("3 unchanged file(s) already clean in the scan cache", out)
        (farm / "mentat" / "b.txt").write_text("copied by mistake: " + SECRET)    # new size and mtime: a miss, and a real hit
        rc, out, scanned = self.farm_scan(farm, cache)
        self.assertEqual((rc, scanned), (1, 1), out)
        self.assertIn("mentat/b.txt", out)
        rc, out, scanned = self.farm_scan(farm, cache)
        self.assertEqual((rc, scanned), (1, 1), out)                              # a file with a hit is never cached as clean
        self.assertNotIn(SECRET.encode(), cache.read_bytes())                    # the cache holds a fingerprint, never a needle

    def test_the_scan_cache_is_void_when_the_needle_set_changes(self):
        farm, cache = self.farm(), self.tmp / "scan.sqlite"
        self.farm_scan(farm, cache)
        self.assertEqual(self.farm_scan(farm, cache)[2], 0)
        later = "a-second-secret-value-9"
        (self.tmp / "keys.json").write_text(json.dumps({"svc": {"keys": [SECRET, later]}}), encoding="utf-8")
        rc, out, scanned = self.farm_scan(farm, cache)
        self.assertEqual((rc, scanned), (0, 3), out)                              # a new needle set: every file is read again
        con = sqlite3.connect(cache)
        try:
            self.assertEqual(con.execute("select count(distinct fp), count(*) from clean").fetchone(), (1, 3))
        finally:
            con.close()
        self.assertEqual(self.farm_scan(farm, cache)[2], 0)                       # and cached again under the new fingerprint


if __name__ == "__main__":
    unittest.main()

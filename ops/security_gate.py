#!/usr/bin/env python3
"""Fail-closed security gate for everything that leaves the laptop (stdlib only).

    python ops/security_gate.py {export,packages,farm,images,all} [--ci] [--target PATH ...]
        [--image REF@sha256:... ...] [--lockfile PATH ...] [--terrier-dir DIR]
        [--compose-json FILE] [--report OUT.json]

Exit 0 only if every REQUIRED check for the stage ran and passed. A missing tool, a tool
error, unparseable tool output or a stale vulnerability DB is a FAIL naming the tool.
Never prints secret values. See ops/SECURITY_GATE.md.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fnmatch
import hashlib
import ipaddress
import json
import os
import posixpath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
import zlib
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
SEC = HERE / "security"
BIN_DIR = SEC / "bin"  # gitignored; scanner binaries dropped here are found after PATH
DECISIONS_MD = Path(r"C:\Users\pgoff\OneDrive\Desktop\Project Brain\brains\melange-systems-brain\DECISIONS.md")
KEYS_JSON = Path(r"C:\Users\pgoff\OneDrive\Desktop\API Keys\keys.json")
LIVE_FEC = Path(r"C:\Users\pgoff\OneDrive\Desktop\Melange Systems\fec")
SECRET_ENV = ("ANTHROPIC_API_KEY", "GOVINFO_KEY", "CONGRESS_API_KEY", "FEC_API_KEY", "LDA_API_KEY",
              "X_BEARER_TOKEN", "TRUTH_TOKEN")

TIMEOUT = 1800  # seconds per tool run
MAX_UNPACK = 8 << 30  # bytes written by one safe-unpack (zip-bomb cap)
DB_MAX_AGE_H = 24
PII_MAX = 10  # emails / phones per file before it counts (normal source peaks at 3 / 2)
SHOW = 25  # findings printed per check; the report carries all of them
UNFIXED_SEVERITIES = ("CRITICAL", "HIGH")  # D-181: counted and reported, never blocking
# D-182: licence findings on OS packages are reported, never blocking. Trivy's licence scan puts every OS package in one
# result (Target "OS Packages", Class "license"); its vuln results say Class "os-pkgs" and Type = the distro. Anything
# that is not recognisably an OS-package result is a language package (or unknown) and keeps blocking.
OS_LICENSE_TARGET = "OS Packages"
OS_PKG_TYPES = {"debian", "ubuntu", "alpine", "redhat", "centos", "rocky", "alma", "fedora", "amazon", "oracle", "suse",
                "photon", "wolfi", "chainguard", "cbl-mariner", "azurelinux"}
LICENSE_SEVERITIES = ("CRITICAL", "HIGH", "UNKNOWN")  # forbidden, restricted, unknown: trivy's UNKNOWN is the unrecognised licence

DENY_EXT = {".sqlite", ".sqlite3", ".db", ".parquet", ".csv", ".tsv", ".xlsx", ".xls", ".joblib", ".pkl",
            ".pickle", ".jsonl", ".ndjson", ".wav", ".mp3", ".mp4", ".m4a", ".flac",
            # opaque containers and R / columnar / dump formats: the gate cannot read inside them (lower case: ext is lower-cased)
            ".gz", ".tgz", ".tar", ".7z", ".bz2", ".xz", ".rar", ".rds", ".rda", ".rdata", ".feather", ".npz", ".sql", ".bak"}
# .gitleaks.toml / .gitleaksignore inside a scan target could silence gitleaks, so they are denied too.
DENY_NAMES = ("keys.json", "*key*.txt", "dashboard_password*", ".secret", "*salt*", ".demo-key", "*.dpapi",
              ".env", ".env.*", ".gitleaks.toml", "gitleaks.toml", ".gitleaksignore")
SSN_RE = re.compile(r"(?<![\w-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\w-])")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
EMAIL_OK = re.compile(r"(^|\.)(example(\.(com|org|net|test))?|test|invalid|localhost|anthropic\.com|"
                      r"users\.noreply\.github\.com)$", re.I)
PHONE_RE = re.compile(r"(?<![\w.-])(?:\+?1[-. ]?)?(?:\(\d{3}\)\s?|\d{3}[-. ])\d{3}[-. ]\d{4}(?![\w-])")
# Envelope written by fec/surfaces/lock_dashboard.py: `var D={s:"<salt>",i:"<iv>",c:"<ciphertext>",n:<iters>}`
# inside a self-decrypting AES-GCM page. Everything outside the ciphertext blob must stay small.
ENVELOPE_RE = re.compile(rb'var D=\{s:"[A-Za-z0-9+/=]{8,}",i:"[A-Za-z0-9+/=]{8,}",c:"([A-Za-z0-9+/=]{64,})",n:\d+\}')
ENVELOPE_WRAPPER_MAX = 65536
DIGEST_RE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")

SECRET_CHECKS = {"gitleaks", "data_leak", "denied_names", "exact_secrets"}  # an exception for these is always HIGH: 30 days at most
STAGES = {"export", "packages", "farm", "deps", "static", "terrier", "images"}
CHECKS = {"gitleaks", "data_leak", "denied_names", "exact_secrets", "safe_unpack", "pip_audit", "npm_audit", "osv_scanner",
          "bandit", "semgrep", "terrier_check", "terrier_no_local_admin", "trivy", "compose_policy"}
_EXPORT = [("export", c) for c in ("exceptions", "gitleaks", "data_leak", "exact_secrets")]
_PACKAGES = [("packages", c) for c in ("exceptions", "safe_unpack", "gitleaks", "data_leak", "exact_secrets")]
_IMAGES = [("images", c) for c in ("exceptions", "trivy", "compose_policy")]
# The data farm is data by design: no data_leak (extensions, size, PII), but never a secret value or a secret-named file.
_FARM = [("farm", c) for c in ("exceptions", "denied_names", "exact_secrets")]
REQUIRED = {
    "export": _EXPORT,
    "packages": _PACKAGES,
    "farm": _FARM,
    "images": _IMAGES,
    "all": _EXPORT + [("deps", c) for c in ("pip_audit", "npm_audit", "osv_scanner")]
    + [("static", c) for c in ("bandit", "semgrep")]
    + [("terrier", c) for c in ("terrier_check", "terrier_no_local_admin")] + _IMAGES[1:],
}

TERRIER_ENV = {  # documented dummy, non-secret values for `check --deploy` (hosted mode)
    "CU_DEPLOYMENT": "hosted", "CU_SECRET_KEY": "terrier-gate-dummy-" * 3,
    "CU_ALLOWED_HOSTS": "gate.invalid,127.0.0.1,localhost", "CU_TRUSTED_ORIGINS": "https://gate.invalid",
    "CU_HTTPS_REDIRECT": "1", "CU_LOCAL_ADMIN": "1",  # CU_LOCAL_ADMIN=1 is adversarial: hosted must ignore it
}
# deploy/terrier-hosted's server/settings.py raises RuntimeError("CU_LOCAL_ADMIN=1 is refused in a hosted deployment (...)") when the
# adversarial CU_LOCAL_ADMIN=1 above reaches a hosted start; the gate accepts exactly that sentinel, not any message that says "admin".
TERRIER_SENTINEL = "SETTINGS: RuntimeError: CU_LOCAL_ADMIN=1 is refused in a hosted deployment"
TERRIER_OK = "ok HOSTED=True LOCAL_ADMIN_ACCESS=False"
TERRIER_ASSERT = r'''
import sys
try:
    import django
    django.setup()
    from django.conf import settings
except Exception as e:
    print("SETTINGS: %s: %s" % (type(e).__name__, str(e)[:200])); sys.exit(3)
try:
    assert settings.HOSTED is True, "HOSTED is not true under CU_DEPLOYMENT=hosted"
    assert settings.LOCAL_ADMIN_ACCESS is False, "LOCAL_ADMIN_ACCESS is enabled under hosted"
    from django.test import RequestFactory
    from server.research.middleware import LocalAdministratorMiddleware
    r = RequestFactory().get("/api/anything", REMOTE_ADDR="127.0.0.1", HTTP_HOST="127.0.0.1")
    out = LocalAdministratorMiddleware(lambda q: q)(r)
    assert getattr(out, "local_admin_access", False) is False, "LocalAdministratorMiddleware grants access under hosted"
except AssertionError as e:
    print("ASSERT: %s" % e); sys.exit(1)
print("ok HOSTED=%s LOCAL_ADMIN_ACCESS=%s" % (settings.HOSTED, settings.LOCAL_ADMIN_ACCESS))
'''


class GateError(Exception):
    """Infrastructure failure (tool missing, tool error, unparseable output). Never suppressible."""


class Skip(Exception):
    pass


# ---------------------------------------------------------------- helpers

def posix(p) -> str:
    return str(p).replace("\\", "/")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GateError(f"{what} missing or unparseable ({type(e).__name__})")


def parse_json(text, what):
    try:
        return json.loads(text)
    except ValueError:
        raise GateError(f"{what} output is not parseable JSON")


def relsubj(path, roots) -> str:
    """Report path relative to the first scan root that contains it."""
    p = Path(str(path))
    for root in roots:
        try:
            return posix(p.resolve().relative_to(Path(root).resolve()))
        except (ValueError, OSError):
            continue
    return posix(path)


def find_needles(path, needles) -> set:
    """Binary-safe, one disk read per 1 MiB chunk, each needle variant checked with bytes `in` (a C-level find; it benchmarked
    faster than one compiled alternation over every variant). Chunks overlap by longest needle - 1 so a needle split across a
    chunk boundary is still seen. needles: {name: [bytes variants]} -> names found in the file."""
    hit, keep, tail = set(), max(len(v) for vs in needles.values() for v in vs) - 1, b""
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            buf = tail + chunk
            hit |= {name for name, variants in needles.items() if name not in hit and any(v in buf for v in variants)}
            tail = buf[-keep:] if keep else b""
    return hit


class ScanCache:
    """Files already scanned clean: (path, size, mtime_ns) under one needle-set fingerprint, in a small sqlite file. The
    fingerprint is the sha256 of the sorted needle hashes: no needle is ever stored, and a changed needle set misses every
    row (the stale rows are dropped on open)."""

    def __init__(self, path, needles):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        hashes = sorted(hashlib.sha256(v).hexdigest() for vs in needles.values() for v in vs)
        self.fp = hashlib.sha256(",".join(hashes).encode()).hexdigest()
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("CREATE TABLE IF NOT EXISTS clean(path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, fp TEXT)")
        self.db.execute("DELETE FROM clean WHERE fp != ?", (self.fp,))
        self.pending = 0

    def is_clean(self, key, size, mtime_ns) -> bool:
        return self.db.execute("SELECT 1 FROM clean WHERE path=? AND size=? AND mtime_ns=? AND fp=?",
                               (key, size, mtime_ns, self.fp)).fetchone() is not None

    def mark(self, key, size, mtime_ns):
        self.db.execute("INSERT OR REPLACE INTO clean VALUES(?,?,?,?)", (key, size, mtime_ns, self.fp))
        self.pending += 1
        if self.pending >= 500:  # an interrupted 55 GB scan keeps what it finished
            self.db.commit()
            self.pending = 0

    def close(self):
        self.db.commit()
        self.db.close()


def find_tool(name):
    """argv prefix for a tool: PATH first, then ops/security/bin/. None when absent."""
    found = shutil.which(name) or shutil.which(name, path=str(BIN_DIR))
    return [found] if found else None


def parse_iso(s):
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.strip()).replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------- exceptions

def load_exceptions(path, ci, decisions_path, today):
    """-> (valid exceptions, problems). Anything malformed is a problem and suppresses nothing."""
    p = Path(path)
    if not p.exists():
        return [], []
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return [], [("exceptions.toml", f"unreadable or malformed ({type(e).__name__})")]
    problems = [("exceptions.toml", f"unknown top-level key {k!r} (only [[exception]] is allowed)")
                for k in data if k != "exception"]
    items = data.get("exception", [])
    if not isinstance(items, list):
        return [], problems + [("exceptions.toml", "`exception` must be an array of tables")]
    need = ("id", "stage", "check", "match", "reason", "approver", "expires", "decision")
    decisions = None
    valid, seen = [], set()
    for i, e in enumerate(items):
        label = str(e.get("id")) if isinstance(e, dict) and e.get("id") else f"#{i + 1}"
        errs = []
        if not isinstance(e, dict):
            problems.append((label, "not a table"))
            continue
        errs += [f"missing or empty field {k!r}" for k in need if not str(e.get(k, "")).strip()]
        errs += [f"unknown field {k!r}" for k in e if k not in need + ("severity",)]
        if label in seen:
            errs.append("duplicate id")
        seen.add(label)
        if e.get("stage") not in STAGES:
            errs.append(f"stage must be one of {sorted(STAGES)}")
        if e.get("check") not in CHECKS:
            errs.append(f"check must be one of {sorted(CHECKS)}")
        if e.get("approver") != "Paul":
            errs.append("approver must be exactly 'Paul'")
        sev = str(e.get("severity", "HIGH")).upper()
        if sev not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            errs.append("severity must be LOW, MEDIUM, HIGH or CRITICAL")
        if e.get("check") in SECRET_CHECKS and sev in ("LOW", "MEDIUM"):
            sev = "HIGH"  # a leak is never low severity, whatever the entry says
        match = str(e.get("match", ""))
        if match.strip() and not re.search(r"[A-Za-z0-9_-]", match):
            errs.append("match is only wildcards; name the path it excuses")
        exp = e.get("expires")
        try:
            exp = exp.date() if isinstance(exp, dt.datetime) else exp if isinstance(exp, dt.date) \
                else dt.date.fromisoformat(str(exp))
            if exp < today:
                errs.append(f"expired {exp}")
            else:
                cap = 30 if sev in ("HIGH", "CRITICAL") else 90
                if (exp - today).days > cap:
                    errs.append(f"expires {(exp - today).days} days out; max {cap} for {sev}")
        except ValueError:
            errs.append("expires must be YYYY-MM-DD")
        dec = str(e.get("decision", ""))
        if not re.fullmatch(r"D-\d{3,4}", dec):
            errs.append("decision must look like D-###")
        elif not ci:  # local runs: the D-id must exist in the brain's DECISIONS.md
            if decisions is None:
                try:
                    decisions = Path(decisions_path).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    decisions = ""
            if not re.search(rf"^#+\s*{re.escape(dec)}\b", decisions, re.M):
                errs.append(f"decision {dec} not found in DECISIONS.md")
        if errs:
            problems += [(label, m) for m in errs]
        else:
            valid.append({**e, "id": label, "severity": sev})
    return valid, problems


# ---------------------------------------------------------------- the gate

class Gate:
    def __init__(self, args):
        self.args = args
        self.results, self.versions, self.unfixed, self.os_licenses = [], {}, {}, {}
        self.tmp = Path(tempfile.mkdtemp(prefix="secgate-"))
        self.ex, self.ex_problems = load_exceptions(args.exceptions, args.ci, args.decisions, dt.date.today())
        self._n = 0

    def tmpfile(self, name):
        self._n += 1
        return self.tmp / f"{self._n}-{name}"

    def tool(self, name, argv, cwd=None, env=None):
        """Run a required tool -> (rc, stdout, stderr). Raw output is never printed."""
        exe = find_tool(name)
        if exe is None:
            raise GateError(f"{name} not found on PATH or in ops/security/bin: required tool is missing")
        if name not in self.versions:
            self.versions[name] = self._version(name, exe)
            print(f"tool {name} {self.versions[name]} ({exe[-1]})")
        try:
            p = subprocess.run(exe + argv, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise GateError(f"{name} timed out after {TIMEOUT}s")
        except OSError as e:
            raise GateError(f"{name} could not start ({type(e).__name__})")
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    @staticmethod
    def _version(name, exe):
        try:
            p = subprocess.run(exe + (["version"] if name == "gitleaks" else ["--version"]), capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=60)
            return (p.stdout or p.stderr).decode("utf-8", "replace").strip().splitlines()[0][:80]
        except (OSError, subprocess.SubprocessError, IndexError):
            return "unknown-version"

    def run(self, stage, check, fn):
        """Run one check -> record + print one result line. fn() -> (findings [(subject, detail)], note)."""
        try:
            findings, note = fn()
            kept, used = [], set()
            for subj, det in findings:
                hit = next((e for e in self.ex if e["stage"] == stage and e["check"] == check
                            and (fnmatch.fnmatchcase(subj, e["match"])
                                 or fnmatch.fnmatchcase(f"{subj}: {det}", e["match"]))), None)
                if hit:
                    used.add(hit["id"])
                else:
                    kept.append((subj, det))
            sup = f" ({len(findings) - len(kept)} suppressed by {', '.join(sorted(used))})" if used else ""
            status = "FAIL" if kept else "PASS"
            reason = f"{len(kept)} finding(s){sup}" if kept else f"{note}{sup}"
        except GateError as e:
            status, reason, kept, used = "FAIL", str(e), [], set()
        except Skip as e:
            status, reason, kept, used = "SKIP", str(e), [], set()
        except Exception as e:  # a bug in the gate must fail the gate, never pass it
            status, reason, kept, used = "FAIL", f"internal error {type(e).__name__}: {e}", [], set()
        self.results.append({"stage": stage, "check": check, "status": status, "reason": reason,
                             "findings": [list(f) for f in kept], "suppressed_by": sorted(used)})
        print(f"{status:<4} {stage}/{check}: {reason}")
        for subj, det in kept[:SHOW]:
            print(f"       - {subj}: {det}")
        if len(kept) > SHOW:
            print(f"       ... and {len(kept) - SHOW} more (see --report)")

    def check_exceptions(self):
        return list(self.ex_problems), f"{len(self.ex)} exception(s) in force"


# ---------------------------------------------------------------- scanning checks

def roots_for(targets, need_dir=True):
    """Scan roots for --target; raises GateError (so every dependent check FAILs) when unusable."""
    if not targets:
        raise GateError("no --target given; nothing to scan")
    out = []
    for t in targets:
        p = Path(t)
        if not p.exists() or (need_dir and not p.is_dir()):
            raise GateError(f"target {t!r} is not an existing directory")
        out.append((p.resolve(), ""))
    return out


def check_gitleaks(g, roots):
    findings = []
    for root, prefix in roots:
        rep = g.tmpfile("gitleaks.json")
        # `detect --no-git` scans the directory as plain files and exists in every 8.x release.
        rc, _, _ = g.tool("gitleaks", ["detect", "--no-git", "--source", str(root), "--report-format", "json",
                                       "--report-path", str(rep), "--redact", "--no-banner", "--exit-code", "1"])
        if rc not in (0, 1):
            raise GateError(f"gitleaks exited {rc} (tool error)")
        data = load_json(rep, "gitleaks report")
        if not isinstance(data, list):
            raise GateError("gitleaks report is not a JSON list")
        if rc == 1 and not data:
            raise GateError("gitleaks exited 1 with an empty report")
        # Only RuleID / File / line are read: the Secret and Match fields are never touched.
        findings += [(prefix + relsubj(f.get("File", "?"), [root]), f"gitleaks rule {f.get('RuleID', '?')} "
                      f"line {f.get('StartLine', '?')}") for f in data]
    return findings, f"{len(roots)} tree(s), no leaks"


def load_canaries(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        raise GateError("canaries file missing: ops/security/canaries.txt")
    tokens = [x.strip() for x in lines if x.strip() and not x.lstrip().startswith("#")]
    if not tokens:
        raise GateError("canaries file has no tokens")
    return {f"canary #{i + 1}": [t.encode(), t.encode("utf-16-le")] for i, t in enumerate(tokens)}


def is_locked_envelope(path) -> bool:
    data = Path(path).read_bytes()
    m = ENVELOPE_RE.search(data)
    if not m or b"AES-GCM" not in data or len(data) - len(m.group(1)) > ENVELOPE_WRAPPER_MAX:
        return False
    try:  # ciphertext is incompressible; base64-wrapped plain data is not
        raw = base64.b64decode(m.group(1)[:65536])
    except ValueError:
        return False
    return len(raw) >= 48 and len(zlib.compress(raw, 6)) >= 0.95 * len(raw)


def pii_details(sample: bytes):
    if b"\0" in sample[:8192]:
        return []  # binary: not a text file
    text = sample.decode("utf-8", "replace")
    out = []  # note: every regex here is bounded, so a 5 MB base64 run cannot go quadratic
    n = len(SSN_RE.findall(text))
    if n:
        out.append(f"PII sample: {n} SSN-pattern match(es)")
    e = sum(1 for local, dom in EMAIL_RE.findall(text) if not EMAIL_OK.search(dom)
            and local.lower() not in ("noreply", "no-reply"))
    if e > PII_MAX:
        out.append(f"PII sample: {e} email addresses (max {PII_MAX})")
    p = len(PHONE_RE.findall(text))
    if p > PII_MAX:
        out.append(f"PII sample: {p} phone numbers (max {PII_MAX})")
    return out


def check_data_leak(g, roots):
    canaries = load_canaries(g.args.canaries)
    limit = int(g.args.max_file_mb * 1048576)
    findings = []
    for root, prefix in roots:
        sums = {}
        if (root / "SHA256SUMS").is_file():
            for ln in (root / "SHA256SUMS").read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"([0-9a-fA-F]{64})\s+\*?(.+)$", ln.strip())
                if m:
                    sums[m.group(2)] = m.group(1).lower()
        for dp, dns, fns in os.walk(root, followlinks=False):
            for n in list(dns) + fns:
                full = Path(dp, n)
                rel = prefix + posix(full.relative_to(root))
                if n == ".git":
                    findings.append((rel, "git metadata inside the scan target"))
                    if n in dns:
                        dns.remove(n)
                elif full.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(full):
                    findings.append((rel, "symlink or junction"))
                    if n in dns:
                        dns.remove(n)
            for n in fns:
                full = Path(dp, n)
                if full.is_symlink():
                    continue
                rel_in = posix(full.relative_to(root))
                rel, parts, low, ext = prefix + rel_in, rel_in.split("/"), n.lower(), full.suffix.lower()
                if ext in DENY_EXT:
                    findings.append((rel, f"denied extension {ext}"))
                elif ext == ".zip" and not (len(parts) == 1 and sums.get(n) == sha256_file(full)):
                    findings.append((rel, "denied extension .zip (only kit top-level zips listed in SHA256SUMS)"))
                pat = next((p for p in DENY_NAMES if fnmatch.fnmatchcase(low, p)), None)
                if pat:
                    findings.append((rel, f"denied file name ({pat})"))
                locked = False
                if ext in (".html", ".htm") and {s.lower() for s in parts[:-1]} & {"downloads", "deploy"}:
                    locked = is_locked_envelope(full)
                    if not locked:
                        findings.append((rel, "HTML under downloads/deploy lacks the encrypted-envelope marker"))
                size = full.stat().st_size
                if size > limit and not locked:  # a verified encrypted envelope is ciphertext: size is harmless
                    findings.append((rel, f"{size / 1048576:.1f} MB exceeds the {g.args.max_file_mb:g} MB cap"))
                if rel_in != "ops/security/canaries.txt":  # the canary list itself holds the tokens
                    findings += [(rel, f"contains {c}") for c in sorted(find_needles(full, canaries))]
                with open(full, "rb") as f:
                    findings += [(rel, d) for d in pii_details(f.read(max(limit, 1 << 20)))]
    return findings, f"{len(roots)} tree(s) clean"


def farm_deny_names():
    """DENY_NAMES plus the release's own data denylist (ops/release_lib/data.py DENY_PATTERNS), imported not copied."""
    sys.path.insert(0, str(HERE.parent))
    from ops.release_lib.data import DENY_PATTERNS
    return tuple(dict.fromkeys(DENY_NAMES + tuple(DENY_PATTERNS)))


def check_denied_names(g, roots):
    """No file or directory in the data farm may carry a secret-style name."""
    patterns, findings = farm_deny_names(), []
    for root, prefix in roots:
        for dp, dns, fns in os.walk(root, followlinks=False):
            for n in dns + fns:
                pat = next((p for p in patterns if fnmatch.fnmatchcase(n.lower(), p)), None)
                if pat:
                    findings.append((prefix + posix(Path(dp, n).relative_to(root)), f"denied name ({pat})"))
    return findings, f"{len(roots)} tree(s), no secret-named files"


CREDMAN = ("melange-restic", "melange-svc-push", "melange-svc-status")  # Credential Manager targets ops/release.py uses


def read_credential(target):
    """(username, secret) of a Windows Credential Manager generic credential, or None when it is absent. Never printed."""
    sys.path.insert(0, str(HERE.parent))
    from ops.release_lib import core
    try:
        user, secret = core.read_credential(target)
    except core.NotConfigured:
        return None
    return user, secret.reveal()


def dpapi_decrypt(path):
    sys.path.insert(0, str(HERE.parent))
    from ops.release_lib import core
    return core.dpapi_crypt(Path(path).read_bytes(), decrypt=True).decode("utf-8", "replace")


def release_refs(spec):
    """(normalised DPAPI paths, Credential Manager targets) that ops/release_config.json puts to use: the Netlify token file
    once netlify.site_id is set, the restic / push / status credentials once domain is set (as ops/release.py's own
    readiness check does). A config that does not use them yet leaves their absence a note; empty when it cannot be read."""
    try:
        cfg = json.loads(Path(spec.get("release_config") or HERE / "release_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), set()
    token = os.path.expandvars((cfg.get("netlify") or {}).get("token_file") or "") if (cfg.get("netlify") or {}).get("site_id") else ""
    return ({os.path.normcase(os.path.normpath(token))} if token else set()), \
        (set((cfg.get("credentials") or {}).values()) if cfg.get("domain") else set())


def load_secret_values(spec):
    """-> ({SOURCE_NAME: [needle bytes]}, [problem], [not-configured note]). Values live only in memory (ones under 12
    chars are ignored) and no message carries one. Every file the spec names must be there and parse: a missing source
    is a problem, never a skip (--ci skips the whole check instead). Environment variables are values that may simply
    be unset. DPAPI files and Credential Manager targets are local-only: absent is a problem only if the release config
    (ops/release_config.json) references them, otherwise a note."""
    vals, problems, notes = {}, [], []

    def add(name, v):
        v = str(v).strip()
        if len(v) >= 12:
            vals.setdefault(name, [])
            vals[name] += [b for b in (v.encode(), v.encode("utf-16-le")) if b not in vals[name]]

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
        elif isinstance(o, str) and not re.search(r"\s", o) and not re.match(r"https?://", o):
            add(f"keys.json:{path}", o)  # whitespace-free, non-URL leaves; skips prose/metadata fields

    if spec is None:
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        spec = {"keys_json": str(KEYS_JSON), "fec_dir": str(LIVE_FEC), "env": list(SECRET_ENV),
                "password_files": [os.environ.get("DASHBOARD_PASSWORD_FILE") or str(local / "Melange" / "dashboard_password.txt")],
                "dpapi_files": [str(local / "Melange" / "secrets" / "netlify.dpapi")], "credman": list(CREDMAN)}
    kj = spec.get("keys_json")
    if kj:
        try:
            walk(json.loads(Path(kj).read_text(encoding="utf-8")), "")
        except FileNotFoundError:
            problems.append("keys.json is missing")
        except (OSError, ValueError):
            problems.append("keys.json is unparseable")
    fd = spec.get("fec_dir")
    if fd:
        if not Path(fd).is_dir():
            problems.append("the fec key directory is missing")
        else:
            for f in sorted([*Path(fd).glob("*key*.txt"), *Path(fd).glob("icloud_smtp.txt")]):
                text = f.read_text(encoding="utf-8", errors="replace")
                for v in [text, *text.splitlines()]:
                    add(f.name, v)
    for pf in spec.get("password_files") or []:
        if pf and Path(pf).is_file():
            add("DASHBOARD_PASSWORD", Path(pf).read_text(encoding="utf-8", errors="replace"))
        else:
            problems.append(f"password file {Path(pf or 'unset').name} is missing")
    for name in spec.get("env") or []:
        add(name, os.environ.get(name, ""))
    ref_files, ref_targets = release_refs(spec)
    for path in spec.get("dpapi_files") or []:
        name = Path(path).name
        if Path(path).is_file():
            try:
                add(name, dpapi_decrypt(path))
            except Exception:  # noqa: BLE001 - another Windows user's blob, a corrupt file, not on Windows: never say more
                problems.append(f"{name} cannot be decrypted")
        elif os.path.normcase(os.path.normpath(str(path))) in ref_files:
            problems.append(f"{name} is referenced by the release config but is missing")
        else:
            notes.append(f"{name} not configured")
    for target in spec.get("credman") or []:
        got = read_credential(target)
        if got:
            add(target, got[1])
            add(target, got[0])
        elif target in ref_targets:
            problems.append(f"credential {target} is referenced by the release config but is not in Credential Manager")
        else:
            notes.append(f"credential {target} not configured")
    return vals, problems, notes


def check_exact_secrets(g, roots):
    if g.args.ci:
        raise Skip("local only (real secret values are not present in CI)")
    spec = None
    if g.args.secret_sources:
        s = g.args.secret_sources
        spec = json.loads(s) if s.lstrip().startswith("{") else load_json(s, "--secret-sources file")
    vals, problems, notes = load_secret_values(spec)
    if problems:
        raise GateError("secret source problem: " + "; ".join(problems))
    if not vals:
        raise GateError("no secret values could be loaded from any source; cannot prove the tree is clean")
    findings, skipped = [], 0
    cache = ScanCache(g.args.scan_cache, vals) if getattr(g.args, "scan_cache", None) else None
    try:
        for i, (root, prefix) in enumerate(roots):
            for dp, dns, fns in os.walk(root, followlinks=False):
                for n in fns:
                    full = Path(dp, n)
                    if full.is_symlink():
                        continue
                    key, info = f"{i}/{prefix}{posix(full.relative_to(root))}", full.stat()  # stat first: a file edited mid-scan misses next time
                    if cache and cache.is_clean(key, info.st_size, info.st_mtime_ns):
                        skipped += 1
                        continue
                    hit = find_needles(full, vals)
                    findings += [(prefix + posix(full.relative_to(root)), f"matches secret {name}") for name in sorted(hit)]
                    if cache and not hit:
                        cache.mark(key, info.st_size, info.st_mtime_ns)
    finally:
        if cache:
            cache.close()
    return findings, (f"{len(vals)} secret source(s) checked, no match" + (f" ({'; '.join(notes)})" if notes else "")
                      + (f" [{skipped} unchanged file(s) already clean in the scan cache]" if skipped else ""))


# ---------------------------------------------------------------- packages

def unpack_one(archive: Path, dest: Path):
    """Extract tar.gz/tgz/zip member by member; refuse traversal, absolute paths, links and special files."""
    root, total = dest.resolve(), 0

    def target(name):
        n = name.replace("\\", "/")
        if n.startswith("/") or ":" in n or "\0" in n or ".." in PurePosixPath(n).parts:
            raise GateError(f"{archive.name}: unsafe member path {name!r}")
        out = (root / n).resolve()
        if out != root and root not in out.parents:
            raise GateError(f"{archive.name}: member {name!r} escapes the extraction directory")
        return out

    def copy(src, out):
        nonlocal total
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as f:
            while chunk := src.read(1 << 20):
                total += len(chunk)
                if total > MAX_UNPACK:
                    raise GateError(f"{archive.name}: expands beyond {MAX_UNPACK >> 30} GiB (archive bomb)")
                f.write(chunk)

    low = archive.name.lower()
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    out = target(info.filename)
                    if stat.S_ISLNK(info.external_attr >> 16):
                        raise GateError(f"{archive.name}: link member {info.filename!r}")
                    if info.is_dir():
                        out.mkdir(parents=True, exist_ok=True)
                    else:
                        with z.open(info) as src:
                            copy(src, out)
        else:
            with tarfile.open(archive, "r:gz") as t:
                for m in t:
                    out = target(m.name)
                    if m.isdir():
                        out.mkdir(parents=True, exist_ok=True)
                    elif m.isreg():
                        copy(t.extractfile(m), out)
                    else:
                        raise GateError(f"{archive.name}: link or special member {m.name!r}")
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, zlib.error) as e:
        raise GateError(f"{archive.name}: unreadable archive ({type(e).__name__})")


def is_archive(p: Path) -> bool:
    return p.name.lower().endswith((".zip", ".tar.gz", ".tgz"))


NESTED = (".zip", ".tar", ".tar.gz", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar")


def check_safe_unpack(g, roots_out):
    if not g.args.target:
        raise GateError("no --target given; nothing to scan")
    errors, n = [], 0
    for t in g.args.target:
        p = Path(t)
        if not p.exists():
            errors.append(f"{t!r} does not exist")
            continue
        archives = [(p, p.name)]
        if p.is_dir():
            roots_out.append((p.resolve(), ""))
            archives = [(Path(dp, f), posix(Path(dp, f).relative_to(p))) for dp, _, fs in os.walk(p) for f in fs
                        if is_archive(Path(f))]
        elif not is_archive(p):
            errors.append(f"{p.name}: unsupported package type (need .zip, .tar.gz or .tgz, or a directory)")
            continue
        for arc, label in archives:
            dest = g.tmpfile("unpack")
            try:
                dest.mkdir()
                unpack_one(arc, dest)
                nested = sorted(posix(Path(dp, f).relative_to(dest)) for dp, _, fs in os.walk(dest) for f in fs if f.lower().endswith(NESTED))
                if nested:   # an archive inside an archive is never opened, so what it holds is never scanned
                    raise GateError(f"{label}: nested archive {nested[0]!r}" + (f" and {len(nested) - 1} more" if len(nested) > 1 else ""))
                roots_out.append((dest, f"{label}!/"))
                n += 1
            except GateError as e:
                errors.append(str(e))
    if errors:
        raise GateError("; ".join(errors))
    if not roots_out:
        raise GateError("no archive or directory to scan")
    return [], f"{n} archive(s) unpacked safely"


# ---------------------------------------------------------------- dependency / static / terrier

def check_pip_audit(g):
    locks = [Path(x) for x in g.args.lockfile if x.lower().endswith(".txt")]
    if not locks:
        raise GateError("pip-audit: no requirements lockfile (--lockfile *.txt) given")
    findings = []
    for lk in locks:
        rc, out, _ = g.tool("pip-audit", ["-r", str(lk), "--require-hashes", "--strict", "--format", "json",
                                          "--progress-spinner", "off"])
        if rc not in (0, 1):
            raise GateError(f"pip-audit exited {rc} on {lk.name} (tool error)")
        data = parse_json(out, "pip-audit")
        fs = [(lk.name, f"{d.get('name')}=={d.get('version')} {v.get('id')}")
              for d in data.get("dependencies", []) for v in d.get("vulns", [])]
        if rc == 1 and not fs:
            raise GateError(f"pip-audit exited 1 on {lk.name} with no vulnerability (--strict: unauditable dependency?)")
        findings += fs
    return findings, f"{len(locks)} lockfile(s), no known vulnerabilities"


def terrier_frontend(g):
    if not g.args.terrier_dir:
        raise GateError("no --terrier-dir given")
    fe = Path(g.args.terrier_dir) / "frontend"
    if not (fe / "package.json").is_file():
        raise GateError(f"{fe} has no package.json")
    return fe


def check_npm_audit(g):
    fe = terrier_frontend(g)
    rc, out, _ = g.tool("npm", ["audit", "--omit=dev", "--audit-level=high", "--json"], cwd=str(fe))
    data = parse_json(out, "npm audit")
    if not isinstance(data, dict) or "error" in data or "vulnerabilities" not in data:
        raise GateError("npm audit returned an error or no report (offline? no lockfile?)")
    fs = [("terrier/frontend", f"{n} {v.get('severity')}") for n, v in data["vulnerabilities"].items()
          if v.get("severity") in ("high", "critical")]
    if rc != 0 and not fs:
        raise GateError(f"npm audit exited {rc} with no high/critical finding")
    return fs, "no high/critical vulnerabilities"


def check_osv(g):
    locks = [Path(x) for x in g.args.lockfile]
    if g.args.terrier_dir and (Path(g.args.terrier_dir) / "frontend" / "package-lock.json").is_file():
        locks.append(Path(g.args.terrier_dir) / "frontend" / "package-lock.json")
    if not locks:
        raise GateError("osv-scanner: no lockfile given")
    findings = []
    for lk in locks:
        rc, out, _ = g.tool("osv-scanner", ["--lockfile", str(lk), "--format", "json"])
        if rc not in (0, 1):
            raise GateError(f"osv-scanner exited {rc} on {lk.name} (tool error / no packages found)")
        data = parse_json(out, "osv-scanner")
        fs = [(lk.name, f"{p['package'].get('name')}@{p['package'].get('version')} {v.get('id')}")
              for r in data.get("results") or [] for p in r.get("packages") or []
              for v in p.get("vulnerabilities") or []]
        if rc == 1 and not fs:
            raise GateError(f"osv-scanner exited 1 on {lk.name} with no parseable vulnerability")
        findings += fs
    return findings, f"{len(locks)} lockfile(s), no known vulnerabilities"


def check_bandit(g, roots):
    findings = []
    for root, _ in roots:
        rc, out, _ = g.tool("bandit", ["-r", str(root), "-f", "json", "-q", "--severity-level", "high"])
        if rc not in (0, 1):
            raise GateError(f"bandit exited {rc} (tool error)")
        data = parse_json(out, "bandit")
        if data.get("errors"):
            raise GateError(f"bandit could not analyze {len(data['errors'])} file(s)")
        fs = [(relsubj(r.get("filename", "?"), [root]), f"bandit {r.get('test_id')} line {r.get('line_number')}")
              for r in data.get("results", []) if str(r.get("issue_severity")).upper() == "HIGH"]
        if rc == 1 and not fs:
            raise GateError("bandit exited 1 with no HIGH finding in its report")
        findings += fs
    return findings, "no HIGH findings"


def check_semgrep(g, roots):
    cmd = ["scan", "--config", "p/python", "--config", "p/django", "--config", "p/secrets",
           "--config", str(SEC / "semgrep"), "--error", "--json", "--quiet", "--metrics", "off"]
    rc, out, _ = g.tool("semgrep", cmd + [str(r) for r, _ in roots])
    if rc not in (0, 1):
        raise GateError(f"semgrep exited {rc} (tool error / invalid config)")
    data = parse_json(out, "semgrep")
    if any(e.get("level") == "error" for e in data.get("errors", [])):
        raise GateError("semgrep reported errors while scanning")
    fs = [(relsubj(r.get("path", "?"), [x for x, _ in roots]),
           f"semgrep {r.get('check_id')} line {(r.get('start') or {}).get('line', '?')}")
          for r in data.get("results", [])]
    if rc == 1 and not fs:
        raise GateError("semgrep exited 1 with no finding in its report")
    return fs, "no findings"


def terrier_env(g):
    return {**TERRIER_ENV, "CU_DATA_DIR": str(g.tmp / "terrier-data"), "DJANGO_SETTINGS_MODULE": "server.settings",
            "PYTHONPATH": str(Path(g.args.terrier_dir).resolve())}


def check_terrier(g):
    if not g.args.terrier_dir or not (Path(g.args.terrier_dir) / "manage.py").is_file():
        raise GateError("--terrier-dir missing or has no manage.py")
    rc = subprocess.run([sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
                        cwd=g.args.terrier_dir, env={**os.environ, **terrier_env(g)}, capture_output=True,
                        stdin=subprocess.DEVNULL, timeout=TIMEOUT)
    if rc.returncode == 0:
        return [], "manage.py check --deploy is clean"
    ids = sorted(set(re.findall(r"\(([a-z_]+\.[WE]\d+)\)", (rc.stdout + rc.stderr).decode("utf-8", "replace"))))
    if not ids:
        raise GateError(f"manage.py check --deploy exited {rc.returncode} (could not run Django checks)")
    return [("terrier", f"django check {i}") for i in ids], "clean"


def check_terrier_no_local_admin(g):
    if not g.args.terrier_dir:
        raise GateError("no --terrier-dir given")
    p = subprocess.run([sys.executable, "-c", TERRIER_ASSERT], cwd=g.args.terrier_dir,
                       env={**os.environ, **terrier_env(g)}, capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=TIMEOUT)
    out = p.stdout.decode("utf-8", "replace").strip()
    lines = out.splitlines()
    if p.returncode == 0 and lines[-1:] == [TERRIER_OK]:  # the probe ran its asserts and printed the flags it saw
        return [], "hosted settings do not enable local admin"
    if p.returncode == 3 and len(lines) == 1 and lines[0].startswith(TERRIER_SENTINEL):  # the startup refusal, exactly
        return [], "hosted settings refuse local admin at startup"
    raise GateError(f"hosted mode may enable local admin or settings failed: {out[:200] or 'rc=%d' % p.returncode}")


# ---------------------------------------------------------------- images

def trivy_unfixed(g, ref):
    """D-181: HIGH/CRITICAL vulnerabilities with no fix yet, which the blocking run hides with --ignore-unfixed.
    Report only: any trouble here is recorded in the report and never fails the gate."""
    out = g.tmpfile("trivy-unfixed.json")
    try:
        rc, _, _ = g.tool("trivy", ["image", "--scanners", "vuln", "--severity", "HIGH,CRITICAL", "--format", "json",
                                    "--output", str(out), ref])
        if rc != 0:
            return {"error": f"unfixed report unavailable (trivy exited {rc})"}
        data = load_json(out, "trivy unfixed report")
    except GateError as e:
        return {"error": f"unfixed report unavailable ({e})"}
    counts = dict.fromkeys(UNFIXED_SEVERITIES, 0)
    for res in data.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            if not v.get("FixedVersion") and v.get("Severity") in counts:
                counts[v["Severity"]] += 1
    return counts


def is_os_package_result(res):
    return (res.get("Class") == "os-pkgs" or res.get("Target") == OS_LICENSE_TARGET
            or str(res.get("Type") or "").lower() in OS_PKG_TYPES)


def trivy_licenses(g, ref):
    """D-182: licence findings by class -> (blocking language-package findings, OS-package severity counts).
    A separate run so UNKNOWN can be listed for licences without turning UNKNOWN vulnerabilities into findings.
    Fail closed: any trouble here fails the gate."""
    out = g.tmpfile("trivy-license.json")
    rc, _, _ = g.tool("trivy", ["image", "--scanners", "license", "--severity", ",".join(LICENSE_SEVERITIES),
                                "--format", "json", "--output", str(out), ref])
    if rc != 0:
        raise GateError(f"trivy licence scan exited {rc} on {ref.split('@')[0]} (tool error)")
    fs, os_counts = [], dict.fromkeys(LICENSE_SEVERITIES, 0)
    for res in load_json(out, "trivy licence report").get("Results") or []:
        subj = f"{ref.split('@')[0]}: {res.get('Target', '?')}"
        for lic in res.get("Licenses") or []:
            if is_os_package_result(res):
                os_counts[lic.get("Severity") if lic.get("Severity") in os_counts else "UNKNOWN"] += 1
            else:
                fs.append((subj, f"license {lic.get('Name')} {lic.get('PkgName')} {lic.get('Severity')}"))
    return fs, os_counts


def check_trivy(g):
    if not g.args.image:
        raise GateError("no --image given")
    bad = [i for i in g.args.image if not DIGEST_RE.fullmatch(i)]
    if bad:
        raise GateError(f"image reference not pinned by digest (REF@sha256:<64 hex>): {bad[0]}")
    findings = []
    for ref in g.args.image:
        out = g.tmpfile("trivy.json")
        rc, _, _ = g.tool("trivy", ["image", "--scanners", "vuln,misconfig,secret", "--severity",
                                    "HIGH,CRITICAL", "--ignore-unfixed", "--exit-code", "1", "--format", "json",
                                    "--output", str(out), ref])
        if rc not in (0, 1):
            raise GateError(f"trivy exited {rc} on {ref.split('@')[0]} (tool error)")
        data = load_json(out, "trivy report")
        fs = []
        for res in data.get("Results") or []:
            subj = f"{ref.split('@')[0]}: {res.get('Target', '?')}"
            for v in res.get("Vulnerabilities") or []:
                fs.append((subj, f"{v.get('VulnerabilityID')} {v.get('PkgName')} {v.get('Severity')}"))
            for m in res.get("Misconfigurations") or []:
                fs.append((subj, f"misconfig {m.get('ID')} {m.get('Severity')}"))
            for s in res.get("Secrets") or []:  # rule + severity only, never the match text
                fs.append((subj, f"secret rule {s.get('RuleID')} {s.get('Severity')}"))
        if rc == 1 and not fs:
            raise GateError(f"trivy exited 1 on {ref.split('@')[0]} with no parseable finding")
        findings += fs
        lic_fs, g.os_licenses[ref] = trivy_licenses(g, ref)
        findings += lic_fs
        g.unfixed[ref] = trivy_unfixed(g, ref)
    _, out, _ = g.tool("trivy", ["version", "--format", "json"])
    db = (parse_json(out, "trivy version") or {}).get("VulnerabilityDB") or {}
    stamp = db.get("UpdatedAt") or db.get("DownloadedAt")
    if not stamp:
        raise GateError("trivy vulnerability DB has no timestamp: its age cannot be checked")
    try:
        age_h = (dt.datetime.now(dt.timezone.utc) - parse_iso(stamp)).total_seconds() / 3600
    except ValueError:
        raise GateError("trivy vulnerability DB timestamp is unparseable")
    if age_h > DB_MAX_AGE_H:
        raise GateError(f"trivy vulnerability DB is {age_h:.0f} h old (max {DB_MAX_AGE_H} h)")
    for ref, counts in g.unfixed.items():
        shown = ", ".join(f"{s} {counts[s]}" for s in UNFIXED_SEVERITIES) if "error" not in counts else counts["error"]
        print(f"INFO images/trivy: {ref.split('@')[0]} unfixed, not blocking (D-181): {shown}")
    for ref, counts in g.os_licenses.items():
        print(f"INFO images/trivy: {ref.split('@')[0]} OS-package licences, not blocking (D-182): "
              + ", ".join(f"{s} {counts[s]}" for s in LICENSE_SEVERITIES))
    return findings, (f"{len(g.args.image)} image(s), no fixable HIGH/CRITICAL findings, "
                      "no forbidden or unknown language-package licences")


def _published_ip(port):
    if isinstance(port, dict):
        return str(port.get("host_ip") or "")
    s = str(port).split("/")[0]
    if s.startswith("["):
        return s[1:].split("]")[0]
    parts = s.split(":")
    return parts[0] if len(parts) == 3 else ""


def _loopback(ip):
    try:
        return ip == "localhost" or ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def compose_findings(cfg):
    svcs = cfg.get("services") if isinstance(cfg, dict) else None
    if not isinstance(svcs, dict) or not svcs:
        raise GateError("compose JSON has no services")
    out = []
    for name, s in svcs.items():
        add = lambda d, n=name: out.append((n, d))  # noqa: E731
        if name != "cloudflared":
            if s.get("read_only") is not True:
                add("read_only is not true")
            if "ALL" not in [str(c).upper() for c in s.get("cap_drop") or []]:
                add("cap_drop does not include ALL")
            if not any(str(o).replace("=", ":").lower() == "no-new-privileges:true" for o in s.get("security_opt") or []):
                add("security_opt lacks no-new-privileges:true")
            user = str(s.get("user") or "")
            if not user or user.split(":")[0].lower() in ("0", "root"):
                add("does not run as a non-root user")
        if s.get("privileged"):
            add("privileged: true")
        if s.get("network_mode") == "host":
            add("network_mode: host")
        for port in s.get("ports") or []:  # every entry publishes on the host, even without a host port
            ip = _published_ip(port)
            if not _loopback(ip):
                add(f"published port {port if not isinstance(port, dict) else port.get('published')} binds "
                    f"{ip or '0.0.0.0 (all interfaces)'}, not 127.0.0.1")
        for v in s.get("volumes") or []:
            if isinstance(v, dict):
                src, ro, kind = str(v.get("source", "")), v.get("read_only") is True, v.get("type")
            else:
                parts = str(v).split(":")
                src, kind = parts[0], "bind" if parts[0].startswith(("/", ".")) else "volume"
                ro = "ro" in (parts[2].split(",") if len(parts) > 2 else [])
            src = posixpath.normpath(src.replace("\\", "/"))
            if kind == "bind" and (src == "/srv/data" or src.startswith("/srv/data/")) and not ro:
                add(f"bind mount {src} is not read-only")
    return out


def check_compose(g):
    if not g.args.compose_json:
        raise GateError("no --compose-json given (output of `docker compose config --format json`)")
    return compose_findings(load_json(g.args.compose_json, "compose JSON")), "compose policy satisfied"


# ---------------------------------------------------------------- stages

def stage_scan(g, tag, roots_fn):
    g.run(tag, "gitleaks", lambda: check_gitleaks(g, roots_fn()))
    g.run(tag, "data_leak", lambda: check_data_leak(g, roots_fn()))
    g.run(tag, "exact_secrets", lambda: check_exact_secrets(g, roots_fn()))


def run_stage(g, stage):
    if stage in ("export", "all"):
        g.run("export", "exceptions", g.check_exceptions)
        stage_scan(g, "export", lambda: roots_for(g.args.target))
    if stage == "farm":
        g.run("farm", "exceptions", g.check_exceptions)
        g.run("farm", "denied_names", lambda: check_denied_names(g, roots_for(g.args.target)))
        g.run("farm", "exact_secrets", lambda: check_exact_secrets(g, roots_for(g.args.target)))
    if stage == "packages":
        g.run("packages", "exceptions", g.check_exceptions)
        roots = []
        g.run("packages", "safe_unpack", lambda: check_safe_unpack(g, roots))
        stage_scan(g, "packages", lambda: roots or roots_for([]))
    if stage == "all":
        g.run("deps", "pip_audit", lambda: check_pip_audit(g))
        g.run("deps", "npm_audit", lambda: check_npm_audit(g))
        g.run("deps", "osv_scanner", lambda: check_osv(g))
        g.run("static", "bandit", lambda: check_bandit(g, roots_for(g.args.target, need_dir=False)))
        g.run("static", "semgrep", lambda: check_semgrep(g, roots_for(g.args.target, need_dir=False)))
        g.run("terrier", "terrier_check", lambda: check_terrier(g))
        g.run("terrier", "terrier_no_local_admin", lambda: check_terrier_no_local_admin(g))
    if stage in ("images", "all"):
        if stage == "images":
            g.run("images", "exceptions", g.check_exceptions)
        g.run("images", "trivy", lambda: check_trivy(g))
        g.run("images", "compose_policy", lambda: check_compose(g))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["export", "packages", "farm", "images", "all"])
    ap.add_argument("--ci", action="store_true", help="CI run: exact_secrets SKIPs, DECISIONS.md is not consulted")
    for flag in ("--target", "--image", "--lockfile"):
        ap.add_argument(flag, action="extend", nargs="+", default=[])
    ap.add_argument("--terrier-dir")
    ap.add_argument("--compose-json")
    ap.add_argument("--report")
    ap.add_argument("--max-file-mb", type=float, default=5.0)
    ap.add_argument("--scan-cache", help="sqlite file of files already scanned clean for exact_secrets (same needle set, path, size, mtime)")
    ap.add_argument("--secret-sources", help="JSON (inline or file) replacing the real secret sources; for tests")
    ap.add_argument("--exceptions", default=str(SEC / "exceptions.toml"))
    ap.add_argument("--canaries", default=str(SEC / "canaries.txt"))
    ap.add_argument("--decisions", default=str(DECISIONS_MD))
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    g = Gate(args)
    try:
        run_stage(g, args.stage)
        done = {(r["stage"], r["check"]): r for r in g.results}
        for key in REQUIRED[args.stage]:  # fail closed against a required check that never ran
            if key not in done:
                g.results.append({"stage": key[0], "check": key[1], "status": "FAIL", "findings": [],
                                  "reason": "required check did not run", "suppressed_by": []})
                print(f"FAIL {key[0]}/{key[1]}: required check did not run")
        ok = all(r["status"] == "PASS" or (r["status"] == "SKIP" and r["check"] == "exact_secrets" and args.ci)
                 for r in g.results)
        print(f"tools: {', '.join(f'{k} {v}' for k, v in sorted(g.versions.items())) or 'none run'}")
        print(f"RESULT {'PASS' if ok else 'FAIL'}: {args.stage} "
              f"({sum(r['status'] == 'FAIL' for r in g.results)} failing check(s))")
        if args.report:
            Path(args.report).write_text(json.dumps({
                "gate": "security_gate", "stage": args.stage, "ci": args.ci, "result": "PASS" if ok else "FAIL",
                "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "tools": g.versions, "unfixed": g.unfixed, "os_licenses": g.os_licenses, "checks": g.results}, indent=2), encoding="utf-8")
        return 0 if ok else 1
    finally:
        shutil.rmtree(g.tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

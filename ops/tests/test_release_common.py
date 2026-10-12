"""Shared fixtures for the release tests: throwaway git repos, a fake live tree, PATH shims. No network."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if os.name != "nt":
    raise unittest.SkipTest("the release command and its tests are Windows-only (Credential Manager, DPAPI, msvcrt, .cmd shims)")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ops.release_lib import core   # noqa: E402

REAL_LIVE = Path(r"C:/Users/pgoff/OneDrive/Desktop/Melange Systems")      # read only: liveness helpers are copied from here
DATE = {"GIT_AUTHOR_DATE": "2026-01-01T00:00:00 +0000", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00 +0000",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
SHIM = Path(__file__)


def _rm(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path):
    shutil.rmtree(path, onerror=_rm)


def g(*args, cwd, input=None, env=None) -> str:
    out = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args], cwd=cwd, input=input,
                         capture_output=True, check=True, env={**os.environ, **DATE, **(env or {})})
    return out.stdout.decode("utf-8", "replace").strip()


def make_repo(path: Path, files: dict, branch: str, eol_attrs: bool = True) -> str:
    """Commit raw bytes exactly (autocrlf off while committing), then (eol_attrs) turn autocrlf + eol attributes ON like the live repo."""
    path.mkdir(parents=True, exist_ok=True)
    g("init", "-q", "-b", branch, cwd=path)
    for name, body in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body if isinstance(body, bytes) else body.encode())
    g("add", "-A", cwd=path)
    g("commit", "-q", "-m", "init", cwd=path)
    g("config", "core.autocrlf", "true" if eol_attrs else "false", cwd=path)
    if eol_attrs:                                    # exports must ignore these: nothing is ever checked out
        (path / ".git" / "info").mkdir(exist_ok=True)
        (path / ".git" / "info" / "attributes").write_text("*.py text eol=crlf\n*.md eol=lf\n")
    return g("rev-parse", "HEAD", cwd=path)


def liveness_files() -> dict:
    if not (REAL_LIVE / "fec" / "pipeline_lock.py").is_file():
        raise unittest.SkipTest("live tree (read-only source of the liveness helpers) is not present")
    out = {}
    for name in ("pipeline_lock.py", "process_liveness.py", "paths.py"):
        out["fec/" + name] = (REAL_LIVE / "fec" / name).read_bytes()
    out["fec/surfaces/glassbox_emit.py"] = (REAL_LIVE / "fec" / "surfaces" / "glassbox_emit.py").read_bytes()
    return out


MELANGE_FILES = {
    "fec/crlf.py": b"print('a')\r\nprint('b')\r\n",
    "fec/lf.py": b"print('lf')\n",
    "fec/logo.png": bytes(range(256)) * 4,
    "fec/sub/module.js": b"let x = 1;\n",
    "fec/big.json": b"0" * (5 * 1024 * 1024 + 1),
    "fec/data.csv": b"a,b\n1,2\n",
    "fec/_quarantine_old/q.py": b"x = 1\n",
    "fec/attic/a.py": b"x = 2\n",
    "fec/research/r.py": b"x = 3\n",
    "fec/pas224.zip": b"PK",
    "requirements.txt": b"requests==2.0\n",
    "ops/tool.py": b"print('ops')\n",
    "ops/github/workflows/release.yml": b"name: release\n",
    "ops/export.gitattributes": b"* -text\n",
    "ops/vps/allowed_signers": b"release@melange namespaces=\"melange-release\" ssh-ed25519 AAAA\n",
    "outside/x.py": b"x = 4\n",
    "fec/docs/NOTES.md": b"# working notes\n",
    "fec/docs/audit/findings.json": b"{}\n",
}
MENTAT_FILES = {
    "mentat/app/server.py": b"print('server')\r\n",
    "mentat/tools/t.R": b"x <- 1\n",
    "mentat/tools/pocket/mentat-pocket.html": b"<script>var DATA = /*__DATA_START__*/{}/*__DATA_END__*/;</script>\n",
    "mentat/inputs/in.py": b"x = 1\n",
    "mentat/state/s.json": b"{}\n",
    "mentat/audits/a.md": b"# a\n",
    "mentat/.cache/c.json": b"{}\n",
    "mentat/README.md": b"# mentat\n",
    "mentat/inputs/fec_receipt_rules.json": b"{\"rules\": []}\n",
    "mentat/inputs/fec_transaction_types.json": b"{\"types\": []}\n",
    "mentat/inputs/fec_other.json": b"{}\n",
    "mentat/tests/fixtures/memo_redisclosure_cases.json": b"{\"cases\": [\"a real contributor name\"]}\n",
    "mentat/tests/fixtures/other_cases.json": b"{}\n",
    "mentat/R-packages.lock.tsv": b"package\tpath\nx\tC:/Users/someone/R/library\n",     # 199 local paths in the real one
}


class Fixture:
    """Fake melange / mentat / terrier repos with local-only remotes, plus a config pointing at them."""

    def __init__(self, with_liveness=True, eol_attrs=True, big=True):
        self.tmp = Path(tempfile.mkdtemp(prefix="relfix-"))
        files = dict(MELANGE_FILES)
        if not big:
            files.pop("fec/big.json")
        if with_liveness:
            files.update(liveness_files())
        self.melange = self.tmp / "melange"
        self.mentat = self.tmp / "mentat"
        self.terrier = self.tmp / "terrier"
        self.commits = {"melange": make_repo(self.melange, files, "main", eol_attrs),
                        "mentat": make_repo(self.mentat, MENTAT_FILES, "codex/mentat-crypto", eol_attrs),
                        "terrier": make_repo(self.terrier, {"README.md": b"terrier\n", "Dockerfile": b"FROM x\n"}, "claude/walk-list")}
        for name in ("platform-remote.git", "geo-remote.git"):
            g("init", "-q", "--bare", self.tmp / name, cwd=self.tmp)
        g("remote", "add", "origin", str(self.tmp / "geo-remote.git"), cwd=self.terrier)
        self.cfg = core.load_config(overrides={
            "melange_live": str(self.melange), "mentat_live": str(self.mentat), "terrier_checkout": str(self.terrier),
            "platform_bare": str(self.tmp / "platform.git"), "platform_remote_url": str(self.tmp / "platform-remote.git"),
            "terrier_remote_url": str(self.tmp / "geo-remote.git"),
            "releases_dir": str(self.tmp / "releases"), "downloads": {"workbooks": []},
            "signing": {"key": str(self.tmp / "sign_key"), "agent": False},
            "netlify": {"token_file": str(self.tmp / "netlify.dpapi")},
        })

    def close(self):
        rmtree(self.tmp)


class Base(unittest.TestCase):
    def tmpdir(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="reltest-"))
        self.addCleanup(rmtree, path)               # cleanups run last-in-first-out: handles registered later close first
        return path


def install_shims(bin_dir: Path, names, log: Path, remote: Path | None = None, extra_env: dict | None = None) -> dict:
    """Fake tools on PATH: each .cmd runs this file with --shim <tool>. Returns env additions (PATH + FAKE_*)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (bin_dir / f"{name}.cmd").write_text(f'@"{sys.executable}" "{SHIM}" --shim {name} %*\n')
    env = {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "FAKE_LOG": str(log)}
    if remote:
        env["FAKE_REMOTE"] = str(remote)
    env.update(extra_env or {})
    return env


def read_log(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


def shim_main(tool, argv):
    """Fake restic / sftp / gh: log argv + the secret env seen, then mimic the tool. Run as `common.py --shim <tool> ...`."""
    import shutil
    SEEN = ("RESTIC_PASSWORD", "TUNNEL_SERVICE_TOKEN_ID", "TUNNEL_SERVICE_TOKEN_SECRET")
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"tool": tool, "argv": argv, "env": {k: os.environ[k] for k in SEEN if k in os.environ}}) + "\n")
    remote = Path(os.environ.get("FAKE_REMOTE", "."))
    if tool == "gh" and " ".join(argv[:2]) in os.environ.get("FAKE_GH_FAIL", "").split(","):     # e.g. "repo view": exit 1
        sys.exit(1)

    if tool == "restic":
        sub = next((a for a in argv if a in ("cat", "init", "snapshots", "backup")), "")
        if sub == "cat" and os.environ.get("FAKE_RESTIC_NO_REPO"):
            sys.exit(1)
        if sub == "snapshots" and "--tag" in argv:
            print(os.environ.get("FAKE_RESTIC_SNAPSHOTS", "[]"))
        elif sub == "snapshots":                    # `snapshots <id> --json`: the path the backup was given, as restic records it
            made = remote / "backup_path.txt"
            given = argv[argv.index("snapshots") + 1]       # restic expands a short id to the full one; FAKE_RESTIC_FULL_ID is that expansion
            full = os.environ.get("FAKE_RESTIC_FULL_ID", "")
            print(json.dumps([{"id": full if full.startswith(given) else given, "paths": [made.read_text().strip()]}] if made.is_file() else []))
        elif sub == "backup":
            (remote / "backup_path.txt").write_text(Path(argv[argv.index("--files-from-verbatim") + 1]).read_text().strip())
            print(json.dumps({"message_type": "status", "percent_done": 0.5}))
            print(json.dumps({"message_type": "summary", "snapshot_id": os.environ.get("FAKE_SNAPSHOT", "ab12cd34" * 8),
                              "total_files_processed": 3, "total_bytes_processed": 99}))
    elif tool == "sftp":
        batch = Path(argv[argv.index("-b") + 1])
        for line in batch.read_text().splitlines():
            parts = line.replace('"', "").split()
            if parts[0] == "put":
                dest = remote / parts[2].lstrip("/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(parts[1], dest)
                with open(remote / "order.log", "a") as order:
                    order.write("put " + dest.name + "\n")
            elif parts[0] in ("rm", "-rm"):
                (remote / parts[1].lstrip("/")).unlink(missing_ok=True)
            elif parts[0] == "rename":
                if (remote / parts[2].lstrip("/")).exists():           # SSH_FXP_RENAME never overwrites an existing target
                    sys.exit(1)
                os.replace(remote / parts[1].lstrip("/"), remote / parts[2].lstrip("/"))
                with open(remote / "order.log", "a") as order:
                    order.write("rename " + Path(parts[2]).name + "\n")
            elif parts[0] == "get":
                src = remote / parts[1].lstrip("/")
                if not src.is_file():
                    sys.exit(1)
                shutil.copyfile(src, parts[2])
    elif tool == "gh":
        canned = Path(os.environ.get("FAKE_GH_DIR", "."))
        key = " ".join(argv[:2]) if argv[:1] != ["api"] else "api " + argv[-1].split("?")[0]
        answer = canned / (key.replace("/", "_").replace(" ", "_") + ".json")
        if answer.is_file():
            text = answer.read_text()
            if os.environ.get("FAKE_STATE"):                # the commits are only known once the release has made them
                state = json.loads(Path(os.environ["FAKE_STATE"]).read_text())
                text = text.replace("@PLATFORM_COMMIT@", state.get("platform_commit", "")).replace("@TERRIER_COMMIT@", state.get("terrier_commit", ""))
            sys.stdout.write(text)
        elif argv[:1] == ["api"] and "-X" in argv:
            pass
        else:
            sys.stdout.write("[]")
    sys.exit(0)


if __name__ == "__main__" and sys.argv[1:2] == ["--shim"]:
    shim_main(sys.argv[2], sys.argv[3:])


def lock_envelope_bytes(seed: bytes = b"lock", size: int = 4096) -> bytes:
    """Bytes that pass both lock checks: lock_dashboard's wrapper (salt 16, iv 12, 250k iterations) and security_gate's
    (AES-GCM marker, small wrapper, incompressible ciphertext). `seed` makes distinct locks."""
    import base64
    import hashlib
    ct = b"".join(hashlib.sha256(seed + bytes([i])).digest() for i in range(size // 32 + 1))[:size]
    b64 = lambda raw: base64.b64encode(raw).decode()
    return (f'<html><script>var D={{s:"{b64(bytes(range(16)))}",i:"{b64(bytes(range(12)))}",c:"{b64(ct)}",n:250000}};'
            "/* AES-GCM */</script></html>").encode()


FULL_DESK = b"<html>FULL DESK with the staffer surface</html>"


def write_invited(live: Path, governance: str = "ruled", html: bytes = b"<html>invited</html>", seed: bytes = b"invited") -> dict:
    """The live full desk, its invited build (outputs/invited_build, hashes consistent with it) and a deploy/index.html
    lock whose receipt names the invited Moneyball.html as its source. Returns the paths and hashes."""
    import gzip
    sha = core.sha256_bytes
    out = live / "outputs" / "invited_build"
    out.mkdir(parents=True, exist_ok=True)
    (live / "Moneyball.html").write_bytes(FULL_DESK)
    gz = gzip.compress(html, mtime=0)
    (out / "Moneyball.html").write_bytes(html)
    (out / "Moneyball.html.gz").write_bytes(gz)
    doc = {"schema": "melange-invited-build-v1", "governance": governance, "profile": {"path": "p.json", "sha256": "d" * 64},
           "inputs": {"Moneyball.html": {"sha256": sha(FULL_DESK), "bytes": len(FULL_DESK)}},
           "outputs": {"Moneyball.html": {"sha256": sha(html), "bytes": len(html), "gz_sha256": sha(gz), "gz_bytes": len(gz)}}}
    (out / "INVITED_BUILD.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    (live / "deploy").mkdir(exist_ok=True)
    (live / "data" / "engine").mkdir(parents=True, exist_ok=True)
    lock = lock_envelope_bytes(seed)
    (live / "deploy" / "index.html").write_bytes(lock)
    receipt = live / "data" / "engine" / "dashboard_deployment_receipt.json"
    receipt.write_text(json.dumps({"status": "LOCAL_DEPLOY_ARTIFACT_READY_NOT_REMOTE", "source": {"sha256": sha(html)},
                                   "local_deploy_artifact": {"sha256": sha(lock)}, "contract": {"remote_deploy_confirmed": False}}),
                       encoding="utf-8")
    return {"invited": out, "index": live / "deploy" / "index.html", "receipt": receipt, "lock": lock, "html": html}

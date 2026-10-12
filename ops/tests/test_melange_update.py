"""Tests for ops/vps/melange_update.py. Windows-runnable: fake runner, temp dirs, file-based
`current` links, and real ed25519 signatures made with ssh-keygen."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("melange_update", ROOT / "ops" / "vps" / "melange_update.py")
mu = importlib.util.module_from_spec(_spec)
sys.modules["melange_update"] = mu  # dataclasses needs it
_spec.loader.exec_module(mu)

SSH_KEYGEN = r"C:\Windows\System32\OpenSSH\ssh-keygen.exe" if os.name == "nt" else "ssh-keygen"
MTIME = 1_700_000_000_123_456_700  # multiple of 100 ns: survives NTFS
# Where restic records the laptop's farm directory (releases\staging\<id>\farm) in a snapshot's tree on Windows: the drive letter
# is the top directory, so a whole-snapshot restore would put C/Users/... under the target instead of melange/, mentat/, terrier/.
FARM = "/C/Users/pgoff/AppData/Local/Melange/releases/staging/2026.10.12.1/farm"
REAL_RUN = mu.run


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def digest(name, tag):
    return hashlib.sha256(f"{name}:{tag}".encode()).hexdigest()


def image(name, tag):
    return f"ghcr.io/pjg93-222/{name}@sha256:{digest(name, tag)}"


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    d = tmp_path_factory.mktemp("keys")
    for name in ("release", "other"):
        subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "release@melange", "-f", str(d / name)],
                       check=True)
    kind, blob = (d / "release.pub").read_text().split()[:2]
    (d / "allowed_signers").write_text(f'release@melange namespaces="melange-release" {kind} {blob}\n')
    return d


def sign(keys, key, path: Path):
    subprocess.run([SSH_KEYGEN, "-q", "-Y", "sign", "-f", str(keys / key), "-n", "melange-release", str(path)],
                   check=True)
    return path.with_name(path.name + ".sig")


def fake_read_link(link):
    try:
        return Path(Path(link).read_text())
    except FileNotFoundError:
        return None


def fake_repoint(link, target):
    link = Path(link)
    tmp = link.with_name(link.name + ".new")
    tmp.write_text(str(target))
    os.replace(tmp, link)


class Rig:
    """Temp VPS + laptop + fake docker/restic. `run` records every command in self.calls."""

    def __init__(self, tmp: Path, keys: Path):
        self.keys, self.tmp = keys, tmp
        self.cfg = mu.Cfg(push=tmp / "push", data=tmp / "data", serve=tmp / "serve", state_dir=tmp / "state",
                          allowed_signers=keys / "allowed_signers", restic_pass=tmp / "restic.pass",
                          require_mount=False, wait_timeout=5)
        for d in (self.cfg.inbox, self.cfg.restic_repo, self.cfg.status_file.parent, self.cfg.data, self.cfg.serve):
            d.mkdir(parents=True)
        (self.cfg.restic_repo / "config").write_text("x")
        self.cfg.restic_pass.write_text("not-a-real-password\n")
        self.cfg.compose_file.write_text("services: {}\n")
        # the real file keeps its "PASTE" comments after Paul fills the values in
        self.cfg.env_file.write_text("# Every PASTE value is entered by Paul.\nDOMAIN=example.test\nTEAM=acme\nAUD_MELANGE=aud1\n"
                                     "AUD_MENTAT=aud2\nAUD_CO05=aud3\nAUD_OR05=aud4\nAUD_CT01=aud5\n"
                                     f"IMAGE_CLOUDFLARED=cloudflare/cloudflared@sha256:{'c' * 64}\n")
        self.snapshots, self.calls, self.running = {}, [], {}
        self.corrupt, self.pull_fail, self.up_fail, self.lie = set(), set(), set(), set()
        self.backups, self.backup_fail, self.backup_missing = [], set(), set()   # Terrier's `manage.py backup` calls
        self.backup_lies = set()   # districts whose backup fails with "does not exist" although the DB file is there

    # --- laptop side ---
    def _tree(self, snapid, seed, systems=None):
        snap_root = self.tmp / "rs" / snapid[:8]   # short dir name: a 64-char one nears Windows MAX_PATH under pytest tmp
        root = snap_root / FARM.lstrip("/")
        files = {
            "melange/Moneyball.html": f"<html>{seed}</html>".encode(),
            "melange/downloads/a.txt": b"download " + seed.encode(),
            "mentat/reports/r1.txt": b"report " + seed.encode(),
            "terrier/co-05/bundle.zip": b"co05 " + seed.encode(),
            "terrier/or-05/bundle.zip": b"or05 " + seed.encode(),
            "terrier/ct-01/bundle.zip": b"ct01 " + seed.encode(),
        }
        for rel, data in files.items():
            if systems is not None and rel.split("/")[0] not in systems:
                continue
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        if systems is None or "mentat" in systems:
            db = root / "mentat/melange/db.sqlite3"
            db.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(db)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("create table t(x)")
            con.execute("insert into t values (?)", (seed,))
            con.commit()
            con.close()
        entries = []
        for p in sorted(root.rglob("*")):
            if p.is_file():
                os.utime(p, ns=(MTIME, MTIME))
                entries.append({"path": p.relative_to(root).as_posix(), "size": p.stat().st_size,
                                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "mtime_ns": MTIME})
        return snap_root, entries

    def release(self, rid, snapid, *, seed="a", tag="1", key="release", tamper_manifest=False,
                tamper_snapshot=False, extra_path=None, restic_path=FARM, systems=None, tags=None,
                governance="ruled", gates=None):
        if snapid not in self.snapshots:
            self.snapshots[snapid] = self._tree(snapid, seed, systems)
        _, entries = self.snapshots[snapid]
        entries = [*entries, *([{"path": extra_path, "size": 1, "sha256": "0" * 64, "mtime_ns": MTIME}] if extra_path else [])]
        snap_raw = canon({"schema": "melange-snapshot-v1", "release_id": rid, "governance": governance, "files": entries})
        m = {"schema": "melange-platform-release-v1", "release_id": rid, "previous": None, "kind": "full",
             "platform_repo": {"repo": "pjg93-222/melange-platform", "commit": "a" * 40, "tag": "v" + rid},
             "systems": {s: {"source": {"repo": "x", "ref": "main", "commit": "b" * 40}, "image": image(n, (tags or {}).get(s, tag)),
                             "data_subdir": s} for s, n in
                         (("melange", "melange-desk"), ("mentat", "mentat-console"), ("terrier", "terrier-web"))
                         if systems is None or s in systems},
             "data_snapshot": {"restic_snapshot_id": snapid, "manifest_sha256": hashlib.sha256(snap_raw).hexdigest(),
                               "files": len(entries), "bytes": sum(e["size"] for e in entries)},
             "packages": {}, "gates": gates or {}, "signer": "release@melange"}
        if restic_path is not None:
            m["data_snapshot"]["restic_path"] = restic_path
        out = self.tmp / "outgoing"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        (out / "manifest.json").write_bytes(canon(m))
        sig = sign(self.keys, key, out / "manifest.json")
        if tamper_manifest:
            raw = bytearray((out / "manifest.json").read_bytes())
            raw[-3] ^= 0x01
            (out / "manifest.json").write_bytes(bytes(raw))
        if tamper_snapshot:
            snap_raw += b" "
        inbox = self.cfg.inbox
        (inbox / "snapshot_manifest.json").write_bytes(snap_raw)
        (inbox / "manifest.json.sig").write_bytes(sig.read_bytes())
        (inbox / "manifest.json").write_bytes((out / "manifest.json").read_bytes())  # last, as the laptop does
        return m

    # --- VPS side ---
    def apply(self):
        return mu.update_once(self.cfg)

    def status(self):
        return json.loads(self.cfg.status_file.read_text())

    def state(self):
        return json.loads(self.cfg.state_file.read_text())

    def link(self, system):
        return mu.read_link(self.cfg.data / system / "current")

    def env(self):
        return self.cfg.env_file.read_text()

    def count(self, *prefix):
        return sum(1 for c in self.calls if c[: len(prefix)] == list(prefix))

    def restores(self):
        return self.count("restic", "restore")

    def __call__(self, cmd, *, input=None, env=None, timeout=None):
        self.calls.append([str(c) for c in cmd])
        if cmd[0] == "ssh-keygen":
            return REAL_RUN([SSH_KEYGEN, *cmd[1:]], input=input, env=env, timeout=timeout)
        if cmd[0] == "restic":
            return self._restic(cmd, env or {})
        if cmd[0] == "docker":
            return self._docker(cmd)
        return mu.Proc(0)

    def _restic(self, cmd, env):
        assert env.get("RESTIC_PASSWORD") == "not-a-real-password"  # from the environment, never argv
        assert env.get("RESTIC_REPOSITORY") == str(self.cfg.restic_repo)
        assert "not-a-real-password" not in " ".join(map(str, cmd))
        snapid, _, sub = cmd[2].partition(":")       # restic restore <id>[:<subfolder of the snapshot tree>]
        target = Path(cmd[cmd.index("--target") + 1])
        shutil.copytree(self.snapshots[snapid][0] / sub.lstrip("/"), target, dirs_exist_ok=True)
        if snapid in self.corrupt:
            victim = target / "terrier/or-05/bundle.zip"
            raw = bytearray(victim.read_bytes())
            raw[0] ^= 0xFF
            victim.write_bytes(bytes(raw))
        return mu.Proc(0)

    def _docker(self, cmd):
        if "--env-file" not in cmd:                  # plain docker (image prune...): nothing to simulate
            return mu.Proc(0)
        envfile, args = Path(cmd[cmd.index("--env-file") + 1]), cmd[cmd.index("--env-file") + 2:]
        text = envfile.read_text()
        vals = dict(ln.split("=", 1) for ln in text.splitlines() if "=" in ln and not ln.startswith("#"))
        if args[0] == "pull":
            if any(d in text for d in self.pull_fail):
                return mu.Proc(1, "", "manifest unknown")
            return mu.Proc(0)
        if args[0] == "up":
            names = [a for a in args[1:] if a in {n for s in mu.SYSTEMS.values() for n in s}]
            if any(d in text for d in self.up_fail):
                return mu.Proc(1, "", "container is unhealthy")
            for n in names:
                system = next(s for s, v in mu.SYSTEMS.items() if n in v)
                snap = (fake_read_link(self.cfg.data / system / "current") / ".snapshot_id").read_text().strip()
                self.running[n] = {"image": vals[mu.image_var(n)], "snapshot": snap}
            return mu.Proc(0)
        if args[0] == "stop":
            for n in args[1:]:
                self.running.pop(n, None)
            return mu.Proc(0)
        if args[0] == "exec" and args[3:4] == ["sh"]:   # the accounts-DB probe: `sh -c 'test -f ... && echo present || echo absent'`
            if args[2] not in self.running:
                return mu.Proc(1, "", "service is not running")
            return mu.Proc(0, "absent\n" if args[2] in self.backup_missing else "present\n")
        if args[0] == "exec" and "manage.py" in args:
            self.backups.append((args[2], args[-1]))
            if args[2] in self.backup_fail:
                return mu.Proc(1, "", "CommandError: Source integrity check failed")
            if args[2] in self.backup_lies:
                return mu.Proc(1, "", "CommandError: Source database does not exist")
            return mu.Proc(0)
        if args[0] == "exec":
            run = self.running.get(args[2])
            if run is None:
                return mu.Proc(1, "", "not running")
            snap = "deadbeef" if any(d in run["image"] for d in self.lie) else run["snapshot"]
            return mu.Proc(0, json.dumps({"git_sha": "a" * 40, "build_time": "t", "snapshot_id": snap}))
        raise AssertionError(f"unexpected compose command {args}")


@pytest.fixture
def rig(tmp_path, keys, monkeypatch):
    r = Rig(tmp_path, keys)
    monkeypatch.setattr(mu, "run", r)
    monkeypatch.setattr(mu, "read_link", fake_read_link)
    monkeypatch.setattr(mu, "repoint", fake_repoint)
    # the updater wants 15 GiB free beyond the snapshot and a CI runner has about 12: report a roomy volume (a test may patch it again)
    monkeypatch.setattr(mu.shutil, "disk_usage", lambda p: SimpleNamespace(total=1 << 40, used=0, free=1 << 40))
    yield r
    for dp, dns, fns in os.walk(tmp_path):  # read-only release trees: let pytest clean up
        for n in dns + fns:
            try:
                os.chmod(os.path.join(dp, n), 0o700)
            except OSError:
                pass


SNAP_A, SNAP_B, SNAP_C = "a1" * 32, "b2" * 32, "c3" * 32   # restic ids are 64 hex
R1, R2, R3 = "2026.10.12.1", "2026.10.13.1", "2026.10.14.1"


def test_ok_path_flips_links_and_writes_env(rig):
    m = rig.release(R1, SNAP_A)
    assert rig.apply() == "OK", rig.status()
    for s in mu.SYSTEMS:
        assert rig.link(s) == rig.cfg.data / s / "releases" / R1
    env = rig.env()
    assert "DOMAIN=example.test" in env and "TEAM=acme" in env and "AUD_MELANGE=aud1" in env
    assert f"IMAGE_CLOUDFLARED=cloudflare/cloudflared@sha256:{'c' * 64}" in env  # operator value kept
    assert f"IMAGE_MELANGE_DESK={m['systems']['melange']['image']}" in env
    assert all(f"IMAGE_TERRIER_{d}={m['systems']['terrier']['image']}" in env for d in ("CO05", "OR05", "CT01"))
    assert f"RELEASE_ID={R1}" in env
    st = rig.status()
    assert st["state"] == "OK" and st["release_id"] == R1 and st["serving_release_id"] == R1
    assert set(st["services"]) == {n for s in mu.SYSTEMS.values() for n in s}
    assert st["services"]["melange-desk"]["image_digest"] == "sha256:" + digest("melange-desk", "1")
    assert st["services"]["terrier-co05"]["version"]["snapshot_id"] == SNAP_A
    assert rig.state()["release_id"] == R1 and rig.state()["restic_snapshot_id"] == SNAP_A
    assert not any(rig.cfg.inbox.iterdir())  # consumed
    assert any((rig.cfg.state_dir / "processed").iterdir())
    assert not (rig.cfg.data / ".staging").exists()


def test_data_is_readonly_sqlite_relaxed_and_mtimes_restored(rig):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    rel = rig.cfg.data / "mentat" / "releases" / R1
    f = rel / "reports" / "r1.txt"
    assert stat.S_IMODE(f.stat().st_mode) & 0o222 == 0
    assert f.stat().st_mtime_ns == MTIME
    assert (rig.cfg.data / "terrier" / "releases" / R1 / ".snapshot_id").read_text().strip() == SNAP_A
    db = rel / "melange" / "db.sqlite3"
    con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert con.execute("select x from t").fetchone()[0] == "a"
    con.close()
    rec = rig.state()["sqlite"]["mentat/melange/db.sqlite3"]
    assert rec["before"] != rec["after"] and rec["after"] == hashlib.sha256(db.read_bytes()).hexdigest()
    assert db.stat().st_mtime_ns == MTIME


def test_keeps_only_current_and_previous_release_dirs(rig):
    for rid, snap in ((R1, SNAP_A), (R2, SNAP_B), (R3, SNAP_C)):
        rig.release(rid, snap, seed=snap)
        assert rig.apply() == "OK", rig.status()
    kept = sorted(p.name for p in (rig.cfg.data / "melange" / "releases").iterdir())
    assert kept == [R2, R3]
    assert rig.link("melange").name == R3


@pytest.mark.parametrize("how", ["wrong_key", "tampered"])
def test_bad_signature_refused(rig, how):
    rig.release(R1, SNAP_A, key="other" if how == "wrong_key" else "release", tamper_manifest=how == "tampered")
    assert rig.apply() == "FAILED"
    assert "signature" in rig.status()["reason"]
    assert rig.restores() == 0 and rig.count("docker") == 0
    assert rig.link("melange") is None
    assert rig.status()["serving_release_id"] is None


def test_downgrade_refused_and_current_untouched(rig):
    rig.release(R2, SNAP_B)
    assert rig.apply() == "OK"
    env_before = rig.env()
    rig.release(R1, SNAP_A, seed="old")
    assert rig.apply() == "FAILED"
    assert "downgrade" in rig.status()["reason"]
    assert rig.link("melange").name == R2 and rig.env() == env_before
    assert rig.restores() == 1


def test_equal_release_is_a_noop_success(rig):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    ups = rig.count("docker", "compose")
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    assert "no-op" in rig.status()["reason"]
    assert rig.restores() == 1 and rig.count("docker", "compose") == ups


def test_equal_release_id_with_different_manifest_refused(rig):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    rig.release(R1, SNAP_A, tag="2")
    assert rig.apply() == "FAILED" and "different manifest" in rig.status()["reason"]
    assert "melange-desk@sha256:" + digest("melange-desk", "1") in rig.env()


def test_snapshot_manifest_hash_mismatch_fails_without_touching_current(rig):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    env_before = rig.env()
    rig.release(R2, SNAP_B, tamper_snapshot=True)
    assert rig.apply() == "FAILED"
    assert "does not match the signed manifest" in rig.status()["reason"]
    assert rig.restores() == 1 and rig.link("terrier").name == R1 and rig.env() == env_before


@pytest.mark.parametrize("governance, gates, ok", [
    ("ruled", {}, True),                                                                   # the real value the release writes
    ("pending", {"invited_build": {"ok": True, "governance_override": True}}, True),       # Paul-only testing, signed in the gates
    ("pending", {}, False),                                                                # unruled with no signed override
    ("pending", {"invited_build": {"ok": True}}, False),
    ("pending", {"invited_build": {"ok": True, "governance_override": "yes"}}, False),     # only a literal true counts
    ("invited", {"invited_build": {"ok": True, "governance_override": True}}, False),      # the old word is not a governance
    ("", {}, False),
])
def test_snapshot_governance_must_be_ruled_or_pending_with_a_signed_override(rig, governance, gates, ok):
    m = rig.release(R1, SNAP_A, governance=governance, gates=gates)
    raw = (rig.cfg.inbox / "snapshot_manifest.json").read_bytes()
    if ok:
        assert mu.check_snapshot_manifest(m, raw)
    else:
        with pytest.raises(mu.StepError, match="governance"):
            mu.check_snapshot_manifest(m, raw)


def test_corrupted_byte_fails_restore_before_flip(rig):
    """Quarterly drill's negative control, as a test."""
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    env_before = rig.env()
    rig.release(R2, SNAP_B)
    rig.corrupt.add(SNAP_B)
    assert rig.apply() == "FAILED"
    assert "sha256" in rig.status()["reason"]
    assert rig.link("terrier").name == R1 and rig.env() == env_before
    assert not (rig.cfg.data / "terrier" / "releases" / R2).exists()
    assert not (rig.cfg.data / ".staging").exists()
    assert rig.state()["release_id"] == R1


@pytest.mark.parametrize("path", ["../escape.txt", "/abs.txt", "melange/../x", "unknown/x.txt"])
def test_unsafe_snapshot_paths_refused(rig, path):
    rig.release(R1, SNAP_A, extra_path=path)
    assert rig.apply() == "FAILED"
    assert rig.restores() == 0 and rig.link("melange") is None


@pytest.mark.parametrize("how", ["version_mismatch", "up_fails"])
def test_bad_new_release_rolls_back_and_previous_keeps_serving(rig, how):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    env_before = rig.env()
    rig.release(R2, SNAP_B, tag="2")
    (rig.lie if how == "version_mismatch" else rig.up_fail).add(digest("melange-desk", "2"))
    assert rig.apply() == "ROLLED_BACK", rig.status()
    st = rig.status()
    assert st["state"] == "ROLLED_BACK" and st["release_id"] == R2 and st["serving_release_id"] == R1
    assert ("snapshot_id" if how == "version_mismatch" else "unhealthy") in st["reason"]
    assert all(rig.link(s).name == R1 for s in mu.SYSTEMS)
    assert rig.env() == env_before
    assert {v["snapshot"] for v in rig.running.values()} == {SNAP_A}
    assert not (rig.cfg.data / "melange" / "releases" / R2).exists()
    assert rig.state()["release_id"] == R1


def test_bad_digest_at_pull_leaves_everything_untouched(rig):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    env_before = rig.env()
    rig.release(R2, SNAP_B, tag="2")
    rig.pull_fail.add(digest("melange-desk", "2"))
    assert rig.apply() == "FAILED"
    assert "pull" in rig.status()["reason"]
    assert all(rig.link(s).name == R1 for s in mu.SYSTEMS) and rig.env() == env_before
    assert not (rig.cfg.data / "melange" / "releases" / R2).exists()
    assert not (rig.cfg.serve / "compose.env.new").exists()


def test_unexpected_error_after_the_flip_still_rolls_back(rig, monkeypatch):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    env_before = rig.env()
    rig.release(R2, SNAP_B, tag="2")
    real, calls = mu.check_versions, []

    def boom(*a, **k):
        if not calls:
            calls.append(1)
            raise KeyError("a bug, not a StepError")
        return real(*a, **k)

    monkeypatch.setattr(mu, "check_versions", boom)
    assert rig.apply() == "ROLLED_BACK", rig.status()
    assert all(rig.link(s).name == R1 for s in mu.SYSTEMS) and rig.env() == env_before


def test_paste_placeholders_block_deploy_before_the_restore(rig):
    rig.cfg.env_file.write_text("DOMAIN=PASTE-YOUR-DOMAIN.invalid\n")
    rig.release(R1, SNAP_A)
    assert rig.apply() == "FAILED"
    assert "PASTE" in rig.status()["reason"] and rig.restores() == 0


def test_first_release_failure_serves_nothing_and_the_same_manifest_can_be_rerun(rig, monkeypatch):
    env0 = rig.env()
    rig.release(R1, SNAP_A, tag="2")
    rig.lie.add(digest("melange-desk", "2"))
    stops, real = [], mu.run

    def spy(cmd, **kw):
        if "stop" in cmd:
            stops.append(kw.get("env") or {})
        return real(cmd, **kw)

    monkeypatch.setattr(mu, "run", spy)
    assert rig.apply() == "ROLLED_BACK", rig.status()
    assert "first release" in rig.status()["reason"]
    assert all(rig.link(s) is None for s in mu.SYSTEMS) and rig.running == {}      # nothing is served
    assert rig.env() == env0 and "pending" not in rig.state() and "release_id" not in rig.state()
    assert not any((rig.cfg.data / s / "releases" / R1).exists() for s in mu.SYSTEMS)
    # compose interpolates the whole file even for `stop`; the restored env has no IMAGE_* line, so the vars come from the process
    assert stops and all(mu.image_var(n) in stops[0] for v in mu.SYSTEMS.values() for n in v)
    rig.lie.clear()
    rig.release(R1, SNAP_A, tag="2")                                                # the same manifest again
    assert rig.apply() == "OK", rig.status()
    assert rig.state()["release_id"] == R1 and all(rig.link(s).name == R1 for s in mu.SYSTEMS)


def test_code_only_release_reuses_the_data_snapshot(rig):
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK"
    rig.release(R2, SNAP_A, tag="2")
    assert rig.apply() == "OK", rig.status()
    assert rig.restores() == 1
    assert rig.link("mentat").name == R1  # data dir unchanged, only images moved
    assert f"IMAGE_MELANGE_DESK={image('melange-desk', '2')}" in rig.env() and f"RELEASE_ID={R2}" in rig.env()
    assert rig.state()["release_id"] == R2


def test_unreadable_state_fails_closed_and_is_not_overwritten(rig):
    rig.cfg.state_dir.mkdir(parents=True, exist_ok=True)
    rig.cfg.state_file.write_text("{ not json")
    rig.release(R1, SNAP_A)
    assert rig.apply() == "FAILED"
    assert rig.cfg.state_file.read_text() == "{ not json"
    assert "unreadable" in rig.status()["reason"] and rig.restores() == 0


def test_newer_upload_during_processing_is_not_consumed(rig, monkeypatch):
    rig.release(R1, SNAP_A)
    real_deploy = mu.deploy

    def deploy_then_upload(*a, **k):
        res = real_deploy(*a, **k)
        rig.release(R2, SNAP_B)  # the laptop pushes again while we were busy
        return res

    monkeypatch.setattr(mu, "deploy", deploy_then_upload)
    assert rig.apply() == "OK"
    assert json.loads((rig.cfg.inbox / "manifest.json").read_text())["release_id"] == R2


def test_restore_targets_the_farm_inside_the_snapshot_so_the_tree_root_holds_the_systems(rig):
    """The release backs up a farm directory; restic stores it as /C/Users/.../farm. `<id>:<restic_path>` makes the restored
    root hold <system>/<rel> exactly, which is what verify_tree demands."""
    rig.release(R1, SNAP_A)
    assert rig.apply() == "OK", rig.status()
    restore = next(c for c in rig.calls if c[:2] == ["restic", "restore"])
    assert restore[2] == f"{SNAP_A}:{FARM}" and "--no-lock" in restore
    assert (rig.cfg.data / "terrier" / "releases" / R1 / "co-05" / "bundle.zip").read_bytes() == b"co05 a"
    assert (rig.cfg.data / "melange" / "releases" / R1 / "Moneyball.html").is_file()


def test_manifest_without_restic_path_fails_closed_on_a_windows_farm_snapshot(rig):
    rig.release(R1, SNAP_A, restic_path=None)           # whole-snapshot restore: C/Users/... at the top
    assert rig.apply() == "FAILED"
    assert "unexpected top-level entries" in rig.status()["reason"] and "'C'" in rig.status()["reason"]
    assert rig.link("melange") is None and not (rig.cfg.data / ".staging").exists()


@pytest.mark.parametrize("bad", ["/C/../etc", "C:/Users/x", "relative/path", "/C/Users/$(id)", "/a;b"])
def test_unsafe_restic_path_refused_before_any_restore(rig, bad):
    rig.release(R1, SNAP_A, restic_path=bad)
    assert rig.apply() == "FAILED" and "restic_path" in rig.status()["reason"]
    assert rig.restores() == 0 and rig.link("melange") is None


def test_part_files_alone_do_nothing_and_are_never_consumed(rig):
    """The laptop uploads <name>.part then renames. A half-uploaded set (parts only, or parts beside an older manifest.json
    that is absent) must not start a run, and the updater must not touch the parts."""
    for name in ("snapshot_manifest.json", "manifest.json.sig", "manifest.json"):
        (rig.cfg.inbox / (name + ".part")).write_bytes(b"half")
    assert rig.apply() is None and rig.calls == []
    assert sorted(p.name for p in rig.cfg.inbox.iterdir()) == ["manifest.json.part", "manifest.json.sig.part", "snapshot_manifest.json.part"]
    rig.release(R1, SNAP_A)                                 # the real files arrive: parts are ignored, then left alone
    assert rig.apply() == "OK"
    assert sorted(p.name for p in rig.cfg.inbox.iterdir()) == ["manifest.json.part", "manifest.json.sig.part", "snapshot_manifest.json.part"]


def test_compose_never_touches_state_volumes(rig):
    for bad in ("down", "rm", "volume", "-v", "--volumes"):
        with pytest.raises(mu.StepError):
            mu.compose(rig.cfg, "up", bad)
    assert "--env-file" in mu.compose(rig.cfg, "up", "-d")


def test_read_only_release_trees_can_be_removed_and_moved_by_a_non_root_user(tmp_path):
    """Linux CI runs as non-root: a 0555 directory can neither be emptied nor (across parents) renamed."""
    tree = tmp_path / "a" / "sub"
    tree.mkdir(parents=True)
    (tree / "f.txt").write_text("x")
    mu.make_readonly(tmp_path / "a")
    assert stat.S_IMODE((tree / "f.txt").stat().st_mode) & 0o222 == 0
    mu.force_rmtree(tmp_path / "a")
    assert not (tmp_path / "a").exists()


def test_build_env_is_idempotent_and_keeps_operator_lines():
    m = {"release_id": R1, "systems": {"melange": {"image": image("melange-desk", "1")}}}
    first = mu.build_env("# mine\nDOMAIN=x.test\nIMAGE_MELANGE_DESK=old\n", m)
    assert mu.build_env(first, m) == first
    assert "# mine\nDOMAIN=x.test" in first and "old" not in first


def test_real_symlink_repoint_is_atomic(tmp_path):
    (tmp_path / "releases" / "1").mkdir(parents=True)
    (tmp_path / "releases" / "2").mkdir()
    link = tmp_path / "current"
    try:
        mu.repoint(link, tmp_path / "releases" / "1")
    except OSError:
        pytest.skip("no symlink privilege on this machine")
    assert mu.read_link(link) == tmp_path / "releases" / "1"
    mu.repoint(link, tmp_path / "releases" / "2")
    assert mu.read_link(link) == tmp_path / "releases" / "2"

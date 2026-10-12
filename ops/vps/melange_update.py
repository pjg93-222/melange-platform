#!/usr/bin/env python3
"""Apply one signed Melange platform release on the VPS (stdlib only, Python 3.12).

Runs as root from melange-update.service (hourly timer + a path trigger on the sftp inbox).

    verify signature -> refuse downgrade -> check snapshot manifest hash -> restic restore
    -> rehash every file (exact set) -> sqlite journal_mode=DELETE -> mtimes -> read-only
    -> flip `current` -> compose up --wait -> /version check -> roll back on any failure.

Everything before the flip touches only /srv/data/.staging and new releases/<id> dirs, so a
refused or failed release leaves the running release exactly as it was. Terrier state volumes
are never touched: no compose `down`, `rm`, `volume` or `-v` is ever issued (see compose()).
Every external command goes through run(); tests replace it.
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: importable for tests only
    fcntl = None

SCHEMA = "melange-platform-release-v1"
SNAP_SCHEMA = "melange-snapshot-v1"
SIGNER, NAMESPACE = "release@melange", "melange-release"
# system -> {compose service: loopback port inside the container}
SYSTEMS = {
    "melange": {"melange-desk": 8080},
    "mentat": {"mentat-console": 8802},
    "terrier": {"terrier-co05": 8080, "terrier-or05": 8080, "terrier-ct01": 8080},
}
# The only inbox names ever read or consumed; a run starts only when manifest.json (uploaded last) exists. In-flight <name>.part files are left alone.
INBOX_FILES = ("snapshot_manifest.json", "manifest.json.sig", "manifest.json")
RID = re.compile(r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+$")
IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SNAPID = re.compile(r"^[0-9a-f]{64}$")  # restic's full id; a short prefix is not a name for one snapshot
SUBDIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RESTIC_PATH = re.compile(r"^/[A-Za-z0-9._@ /-]*$")
# Every validation below is re.fullmatch: `$` alone would let a trailing newline through.
MAX_INBOX_FILE = 64 << 20  # the push user chooses what lands in the inbox
FREE_AFTER_RESTORE = 15 << 30  # headroom left on the data volume once the restore has landed
DB_SUFFIXES = (".sqlite", ".sqlite3", ".db")
COMPOSE_FORBIDDEN = {"down", "rm", "volume", "-v", "--volumes"}  # nothing here may touch state volumes
MANAGED_HEADER = "# --- managed by melange_update (do not edit below) ---"
# service -> the compose.env variable holding its Cloudflare Access application's AUD tag
AUD_VARS = {"melange-desk": "AUD_MELANGE", "mentat-console": "AUD_MENTAT", "terrier-co05": "AUD_CO05",
            "terrier-or05": "AUD_OR05", "terrier-ct01": "AUD_CT01"}
TERRIER_NOTE = "Terrier image changed: DB schema may be ahead; see RUNBOOK §10"
PROBE = ("import sys,urllib.request;sys.stdout.write("
         "urllib.request.urlopen('http://127.0.0.1:%d/version',timeout=5).read().decode())")


class StepError(Exception):
    """A step failed or an input was refused; the message becomes status.json `reason`."""


@dataclasses.dataclass
class Cfg:
    push: Path = Path("/srv/melange/push")
    data: Path = Path("/srv/data")
    serve: Path = Path("/srv/serve")
    state_dir: Path = Path("/var/lib/melange-update")
    allowed_signers: Path = Path("/etc/melange/allowed_signers")
    restic_pass: Path = Path("/etc/melange/restic.pass")
    push_user: str = "melange-push"
    wait_timeout: int = 600
    require_mount: bool = True  # never write PII onto the unencrypted boot disk if the LUKS mount failed

    inbox = property(lambda s: s.push / "inbox")
    restic_repo = property(lambda s: s.push / "restic")
    status_file = property(lambda s: s.push / "status" / "status.json")
    state_file = property(lambda s: s.state_dir / "state.json")
    compose_file = property(lambda s: s.serve / "compose.yaml")
    env_file = property(lambda s: s.serve / "compose.env")


@dataclasses.dataclass
class Proc:
    rc: int
    out: str = ""
    err: str = ""


def run(cmd, *, input=None, env=None, timeout=None) -> Proc:
    """The only place an external command starts. Never raises for a failing command."""
    try:
        p = subprocess.run(cmd, input=input, env=env, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Proc(124, "", f"timeout after {timeout}s")
    except OSError as e:
        return Proc(127, "", str(e))
    return Proc(p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"))


def sh(cmd, what, **kw) -> Proc:
    p = run(cmd, **kw)
    if p.rc:
        raise StepError(f"{what} failed (rc={p.rc}): {(p.err or p.out).strip()[-300:]}")
    return p


def log(msg):
    print(f"melange-update: {msg}", flush=True)


def utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rid_key(rid):
    return tuple(int(x) for x in rid.split("."))


def _k(p):
    return os.path.normcase(os.path.abspath(p))


def sha256_file(p):
    with open(p, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def read_text(p):
    try:
        return Path(p).read_text()
    except FileNotFoundError:
        return None


def snap_of(release_dir):
    """The restic snapshot id a release directory was restored from (finish_tree stamps it), or None."""
    t = read_text(Path(release_dir) / ".snapshot_id")
    return t.strip() if t else None


def read_inbox_file(p: Path):
    """The bytes, or None when absent. The push user owns the inbox: no symlink (O_NOFOLLOW), only a regular file (O_NONBLOCK so
    a FIFO cannot hang the open; fstat decides), at most MAX_INBOX_FILE."""
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as e:  # ELOOP is a symlink
        raise StepError(f"inbox/{p.name} refused: {e.strerror or e}") from e
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise StepError(f"inbox/{p.name} refused: not a regular file")
        data = f.read(MAX_INBOX_FILE + 1)
    if len(data) > MAX_INBOX_FILE:
        raise StepError(f"inbox/{p.name} refused: larger than {MAX_INBOX_FILE} bytes")
    return data


def write_atomic(path, data, mode=0o644):
    """Temp file + rename; the temp name is recreated O_EXCL|O_NOFOLLOW, so a planted symlink is never written through."""
    path = Path(path)
    tmp = path.with_name("." + path.name + ".tmp")
    raw = data.encode() if isinstance(data, str) else data
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def force_rmtree(p):
    """Release trees are read-only (dirs 0555); root ignores modes, anyone else needs the chmod. Never through a link."""
    if os.path.islink(p):
        os.unlink(p)
        return
    for dp, _, fns in os.walk(p):  # followlinks=False: a symlinked directory is listed but never entered
        os.chmod(dp, 0o700)
        for n in fns:
            if not os.path.islink(fp := os.path.join(dp, n)):
                os.chmod(fp, 0o600)
    shutil.rmtree(p)


def make_readonly(root):
    for dp, _, fns in os.walk(root, topdown=False):
        for n in fns:
            os.chmod(os.path.join(dp, n), 0o444)
        os.chmod(dp, 0o555)


# --- `current` links (tests fake them) ---

def read_link(link: Path):
    try:
        t = Path(os.readlink(link))
    except OSError:
        return None
    return t if t.is_absolute() else link.parent / t


def repoint(link: Path, target: Path):
    """Atomic: temp symlink + rename over `current`."""
    tmp = link.with_name(link.name + ".new")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    os.symlink(os.path.relpath(target, link.parent), tmp, target_is_directory=True)
    os.replace(tmp, link)


# --- state ---

def load_state(cfg):
    try:
        return json.loads(cfg.state_file.read_text())
    except FileNotFoundError:
        return {}
    except ValueError as e:  # fail closed: a damaged file would reset downgrade protection
        raise StepError(f"{cfg.state_file} is unreadable: {e}") from e


def save_state(cfg, st):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    write_atomic(cfg.state_file, json.dumps(st, indent=2, sort_keys=True), 0o600)


def current_release_id(cfg, st):
    if st.get("release_id"):
        return st["release_id"]
    for s in SYSTEMS:  # state lost but data present: the release dir name is a lower bound
        link = read_link(cfg.data / s / "current")
        if link and RID.fullmatch(link.name):
            return link.name
    return None


# --- compose ---

def compose(cfg, *args, env_file=None):
    bad = COMPOSE_FORBIDDEN & set(args)
    if bad:
        raise StepError(f"refusing compose {sorted(bad)}: it could touch state volumes")
    return ["docker", "compose", "-p", "melange", "-f", str(cfg.compose_file),
            "--env-file", str(env_file or cfg.env_file), *args]


def image_var(service):
    return "IMAGE_" + service.upper().replace("-", "_")


def plan_services(m):
    """Active services -> (system, port): the manifest's list, else every service of the system."""
    out = {}
    for s, spec in m["systems"].items():
        for n in spec.get("services") or list(SYSTEMS[s]):
            n = n["name"] if isinstance(n, dict) else n
            if n not in SYSTEMS[s]:
                raise StepError(f"unknown service {n!r} for system {s}")
            out[n] = (s, SYSTEMS[s][n])
    return out


def build_env(prev_text, m):
    """Keep every operator value (DOMAIN, TEAM, AUDs, cloudflared image...); replace only what we manage."""
    new = {"RELEASE_ID": m["release_id"]}
    for s, spec in m["systems"].items():
        for n in SYSTEMS[s]:
            new[image_var(n)] = spec["image"]
    kept = [ln for ln in prev_text.splitlines()
            if ln.strip() != MANAGED_HEADER and ln.partition("=")[0].strip() not in new]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join([*kept, "", MANAGED_HEADER, *(f"{k}={v}" for k, v in sorted(new.items()))]) + "\n"


def env_values(text):
    return {k.strip(): v.strip() for ln in text.splitlines() if "=" in ln and not ln.lstrip().startswith("#")
            for k, _, v in [ln.partition("=")]}


def check_auds(env_text, services):
    """Each Access application has its own AUD. compose catches an unset one only at `up`; a shared one not at all."""
    auds = {n: env_values(env_text).get(AUD_VARS[n], "") for n in services}
    if (missing := sorted(AUD_VARS[n] for n, v in auds.items() if not v)):
        raise StepError(f"compose.env: {', '.join(missing)} not set")
    if len(set(auds.values())) != len(auds):
        raise StepError("compose.env: two services share one AUD_* value; each Access application has its own")


def terrier_image_changed(prev_env, m):
    """Terrier runs under a different image, or the previous env does not say which (unknown = changed): the new one may
    migrate its accounts DB."""
    new = m["systems"].get("terrier", {}).get("image")
    old = env_values(prev_env or "")
    return bool(new) and any(old.get(image_var(n)) != new for n in SYSTEMS["terrier"])


def backup_terrier(cfg, services, rid):
    """Terrier's `manage.py backup` into each district's state volume before anything moves (RUNBOOK section 10 restores
    it). Skipped only for a district whose accounts DB file is verifiably absent; any other failure aborts the release."""
    for name in services:
        if name in SYSTEMS["terrier"]:
            p = run(compose(cfg, "exec", "-T", name, "sh", "-c", "test -f /state/accounts.sqlite3 && echo present || echo absent"), timeout=120)
            if p.rc == 0 and p.out.strip() == "absent":
                continue
            if p.rc == 0 and p.out.strip() == "present":
                p = run(compose(cfg, "exec", "-T", name, "python", "manage.py", "backup", f"/state/pre-{rid}.sqlite3"), timeout=900)
                if p.rc == 0:
                    continue
            raise StepError(f"pre-release backup of {name} failed (rc={p.rc}): {(p.err or p.out).strip()[-200:]}")


def check_versions(cfg, services, snapid):
    out = {}
    for name, (_, port) in services.items():
        p = sh(compose(cfg, "exec", "-T", name, "python", "-c", PROBE % port), f"/version check of {name}", timeout=60)
        try:
            v = json.loads(p.out)
        except ValueError as e:
            raise StepError(f"{name}: /version is not JSON") from e
        if v.get("snapshot_id") != snapid:
            raise StepError(f"{name}: /version snapshot_id {v.get('snapshot_id')!r} != expected {snapid}")
        out[name] = v
    return out


# --- manifests ---

def check_manifest(m):
    if not isinstance(m, dict) or m.get("schema") != SCHEMA:
        raise StepError(f"not a {SCHEMA} manifest")
    if m.get("signer") != SIGNER:
        raise StepError("unexpected signer")
    if not RID.fullmatch(str(m.get("release_id", ""))):
        raise StepError("bad release_id")
    systems = m.get("systems")
    if not isinstance(systems, dict) or not systems or set(systems) - set(SYSTEMS):
        raise StepError("systems must be a non-empty subset of melange, mentat, terrier")
    for s, spec in systems.items():
        if not isinstance(spec, dict) or not IMAGE.fullmatch(str(spec.get("image", ""))):
            raise StepError(f"{s}: image must be <repo>@sha256:<64 hex>")
        if not SUBDIR.fullmatch(str(spec.setdefault("data_subdir", s))):
            raise StepError(f"{s}: bad data_subdir")
    ds = m.get("data_snapshot")
    if not isinstance(ds, dict) or not SNAPID.fullmatch(str(ds.get("restic_snapshot_id", ""))) \
            or not HEX64.fullmatch(str(ds.get("manifest_sha256", ""))) \
            or not isinstance(ds.get("files"), int) or not isinstance(ds.get("bytes"), int):
        raise StepError("bad data_snapshot block")
    rp = ds.get("restic_path")
    if rp is not None and (".." in str(rp) or not RESTIC_PATH.fullmatch(str(rp))):
        raise StepError("bad data_snapshot.restic_path")
    return m


def check_snapshot_manifest(m, raw):
    """Hash-bind the snapshot manifest to the signed manifest, then validate every entry."""
    ds = m["data_snapshot"]
    if hashlib.sha256(raw).hexdigest() != ds["manifest_sha256"]:
        raise StepError("snapshot manifest sha256 does not match the signed manifest")
    try:
        snap = json.loads(raw)
    except ValueError as e:
        raise StepError("snapshot manifest is not JSON") from e
    gate = m.get("gates", {}).get("invited_build") if isinstance(m.get("gates"), dict) else None
    override = isinstance(gate, dict) and gate.get("governance_override") is True       # signed: Paul-only testing before invites
    if snap.get("schema") != SNAP_SCHEMA or snap.get("governance") not in ("ruled", *(("pending",) if override else ())):
        raise StepError("snapshot manifest must be melange-snapshot-v1 with governance 'ruled' "
                        "(or 'pending' with a signed governance_override)")
    by_sys, total, seen = {s: {} for s in m["systems"]}, 0, set()
    for e in snap.get("files", []):
        path, size = str(e.get("path", "")), e.get("size")
        parts = path.split("/")
        if (path.startswith("/") or "\\" in path or "\0" in path or len(parts) < 2
                or any(p in ("", ".", "..") for p in parts) or ":" in parts[0]):
            raise StepError(f"unsafe snapshot path {path!r}")
        if parts[0] not in by_sys:
            raise StepError(f"snapshot has a file for an unlisted system: {path}")
        if path in seen or not isinstance(size, int) or size < 0 or not HEX64.fullmatch(str(e.get("sha256", ""))) \
                or not isinstance(e.get("mtime_ns"), int):
            raise StepError(f"bad or duplicate snapshot entry {path!r}")
        seen.add(path)
        total += size
        by_sys[parts[0]]["/".join(parts[1:])] = e
    if (len(seen), total) != (ds["files"], ds["bytes"]):
        raise StepError("snapshot file/byte totals differ from the signed manifest")
    return by_sys


# --- data staging ---

def restic_env(cfg):
    return {**os.environ, "RESTIC_REPOSITORY": str(cfg.restic_repo),
            "RESTIC_PASSWORD": cfg.restic_pass.read_text().rstrip("\r\n")}


def verify_tree(staging, m, by_sys):
    """Exact file set + size + sha256 against the (signed-hash) snapshot manifest. Regular files and directories only."""
    subs = {spec["data_subdir"] for spec in m["systems"].values()}
    extra = set(os.listdir(staging)) - subs
    if extra:
        raise StepError(f"restore holds unexpected top-level entries: {sorted(extra)}")
    for s, spec in m["systems"].items():
        root = Path(staging, spec["data_subdir"])
        root.mkdir(exist_ok=True)
        have = {}
        for dp, dns, fns in os.walk(root):
            for n in dns + fns:
                if os.path.islink(os.path.join(dp, n)):
                    raise StepError(f"restore contains a symlink: {os.path.join(dp, n)}")
            for n in fns:  # a FIFO or device node would hang sha256_file; lstat, never open
                if not stat.S_ISREG(os.lstat(os.path.join(dp, n)).st_mode):
                    raise StepError(f"restore contains a non-regular file: {os.path.join(dp, n)}")
                have[Path(dp, n).relative_to(root).as_posix()] = os.path.join(dp, n)
        want = by_sys[s]
        if set(have) != set(want):
            miss, extra = sorted(set(want) - set(have)), sorted(set(have) - set(want))
            raise StepError(f"{s}: file set differs from manifest (missing {miss[:3]}, extra {extra[:3]})")
        for rel, e in want.items():
            if os.stat(have[rel]).st_size != e["size"] or sha256_file(have[rel]) != e["sha256"]:
                raise StepError(f"{s}/{rel}: size or sha256 differs from the manifest")


def finish_tree(staging, m, by_sys, snapid):
    """sqlite -> journal_mode=DELETE, restore mtimes, stamp the snapshot id."""
    sqlite_log = {}
    for s, spec in m["systems"].items():
        root = Path(staging, spec["data_subdir"])
        for rel, e in by_sys[s].items():
            p = root.joinpath(*rel.split("/"))
            if rel.lower().endswith(DB_SUFFIXES):
                con = sqlite3.connect(p)
                try:
                    mode = con.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                finally:
                    con.close()
                if str(mode).lower() != "delete":
                    raise StepError(f"{s}/{rel}: journal_mode is {mode}, not delete")
                sqlite_log[f"{s}/{rel}"] = {"before": e["sha256"], "after": sha256_file(p)}
            os.utime(p, ns=(e["mtime_ns"], e["mtime_ns"]))
        (root / ".snapshot_id").write_text(snapid + "\n")
    return sqlite_log


def live_dirs(cfg):
    return {_k(p) for s in SYSTEMS if (p := read_link(cfg.data / s / "current"))}


def prune(cfg, extra=()):
    """Delete every release dir (and staging) not under a `current` link, in `extra`, or the return point in `pending`."""
    held = ((load_state(cfg).get("pending") or {}).get("prev") or {}).values()
    keep = live_dirs(cfg) | {_k(p) for p in extra} | {_k(t) for t in held if t}
    for s in SYSTEMS:
        rel = cfg.data / s / "releases"
        if rel.is_dir():
            for d in rel.iterdir():
                if _k(d) not in keep:
                    force_rmtree(d)
    if (cfg.data / ".staging").exists():
        force_rmtree(cfg.data / ".staging")


def stage_data(cfg, m, by_sys):
    rid, ds = m["release_id"], m["data_snapshot"]
    prune(cfg)  # drops N-2 and leftovers before the big restore: peak disk = current + new
    free = shutil.disk_usage(cfg.data).free
    if free < ds["bytes"] * 1.05 + FREE_AFTER_RESTORE:
        raise StepError(f"not enough free space on {cfg.data}: {free} bytes free, need {ds['bytes']} + 5% + {FREE_AFTER_RESTORE >> 30} GiB headroom")
    staging = cfg.data / ".staging" / rid
    staging.mkdir(parents=True)
    target = ds["restic_snapshot_id"] + (":" + ds["restic_path"] if ds.get("restic_path") else "")
    sh(["restic", "restore", target, "--target", str(staging), "--no-lock"], "restic restore",
       env=restic_env(cfg), timeout=6 * 3600)
    verify_tree(staging, m, by_sys)
    sqlite_log = finish_tree(staging, m, by_sys, ds["restic_snapshot_id"])
    links = {}
    for s, spec in m["systems"].items():
        dest = cfg.data / s / "releases" / rid
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if _k(dest) in live_dirs(cfg):
                raise StepError(f"refusing to replace {dest}: a `current` link serves it")
            force_rmtree(dest)
        os.replace(staging / spec["data_subdir"], dest)
        make_readonly(dest)  # after the move: renaming a directory across parents needs it writable
        links[s] = dest
    force_rmtree(cfg.data / ".staging")
    return links, sqlite_log


# --- the release ---

def deploy(cfg, st, m, snap_raw, m_raw):
    """Returns (state, reason, services, warnings); raises StepError when nothing was changed."""
    rid, mhash = m["release_id"], hashlib.sha256(m_raw).hexdigest()
    cur = current_release_id(cfg, st)
    if cur:
        if rid_key(rid) < rid_key(cur):
            raise StepError(f"downgrade refused: {rid} is older than {cur}")
        if rid == cur:
            if mhash == st.get("manifest_sha256"):
                return "OK", "already deployed (no-op)", None, []
            raise StepError(f"{rid} is already deployed with a different manifest")
    by_sys = check_snapshot_manifest(m, snap_raw)
    snapid = m["data_snapshot"]["restic_snapshot_id"]
    services, systems = plan_services(m), list(m["systems"])
    prev_links = {s: read_link(cfg.data / s / "current") for s in systems}
    prev_env = read_text(cfg.env_file)
    terrier = prev_links.get("terrier") is not None and terrier_image_changed(prev_env, m)   # nothing deployed yet: no DB
    if any("PASTE" in ln for ln in (prev_env or "").splitlines() if not ln.lstrip().startswith("#")):
        # before a 50 GB restore, not after; comments may say PASTE, values may not
        raise StepError(f"{cfg.env_file} still has PASTE placeholders (ops/VPS.md, step 'compose.env')")
    check_auds(prev_env or "", services)
    new_env_path = cfg.serve / "compose.env.new"
    sqlite_log = {}
    try:
        if all(p and p.is_dir() and snap_of(p) == snapid for p in prev_links.values()):
            new_links = prev_links  # code-only release: each system already runs this exact snapshot, no 40 GB copy
        else:
            new_links, sqlite_log = stage_data(cfg, m, by_sys)
        new_env = build_env(prev_env or "", m)
        write_atomic(new_env_path, new_env, 0o600)
        sh(compose(cfg, "pull", "--quiet", *services, env_file=new_env_path), "compose pull", timeout=1800)
        if terrier:
            backup_terrier(cfg, services, rid)  # a failure here refuses the release with nothing changed
        if prev_env is not None:
            write_atomic(cfg.serve / "compose.env.prev", prev_env, 0o600)
            # the way back, durable before anything moves (first release: every prev is None)
            st["pending"] = {"rid": rid, "services": list(services), "prev_env": str(cfg.serve / "compose.env.prev"),
                             "terrier": terrier, "prev": {s: str(p) if p else None for s, p in prev_links.items()}}
            save_state(cfg, st)
    except (StepError, OSError, sqlite3.Error):
        st.pop("pending", None)
        prune(cfg)
        new_env_path.unlink(missing_ok=True)
        raise
    flipped, warn = [], []
    try:
        for s in systems:
            if new_links[s] != prev_links[s]:
                repoint(cfg.data / s / "current", new_links[s])
                flipped.append(s)
        write_atomic(cfg.env_file, new_env, 0o600)
        sh(compose(cfg, "up", "-d", "--wait", "--wait-timeout", str(cfg.wait_timeout), *services),
           "compose up --wait", timeout=cfg.wait_timeout + 300)
        if run(compose(cfg, "up", "-d", "cloudflared"), timeout=300).rc:
            warn.append("cloudflared did not start")  # the apps are fine; do not roll a good release back
        versions = check_versions(cfg, services, snapid)
    except Exception as e:  # anything at all once links/env have moved: put the old release back
        return rollback(cfg, st, services, flipped, prev_links, prev_env,
                        (str(e) or repr(e)) + (f"; {TERRIER_NOTE}" if terrier else ""))
    finally:
        new_env_path.unlink(missing_ok=True)
    st.update(release_id=rid, manifest_sha256=mhash, restic_snapshot_id=snapid,
              sqlite=sqlite_log or st.get("sqlite", {}))
    st.pop("pending", None)
    save_state(cfg, st)  # live and verified: record it before the slow prune
    prune(cfg, [p for p in prev_links.values() if p])  # keep current + previous only
    # old unused images only, never volumes (Terrier's field results live in them)
    run(["docker", "image", "prune", "-af", "--filter", "until=168h"], timeout=900)
    svc = {n: {"image_digest": m["systems"][s]["image"].split("@", 1)[1], "version": versions[n]}
           for n, (s, _) in services.items()}
    return "OK", "", svc, warn


def rollback(cfg, st, services, flipped, prev_links, prev_env, reason):
    if prev_env is None:
        return "FAILED", f"{reason}; there is no previous compose.env to return to", None, []
    back = {n: v for n, v in services.items() if prev_links[v[0]]}  # systems that had a previous release
    try:
        for s in flipped:
            if prev_links[s]:
                repoint(cfg.data / s / "current", prev_links[s])
            else:  # new in this release: nothing to return to, so take it down again rather than leave it serving
                (cfg.data / s / "current").unlink(missing_ok=True)
        write_atomic(cfg.env_file, prev_env, 0o600)
        if (gone := [n for n in services if n not in back]):  # compose resolves ${IMAGE_*:?} even for `stop`
            miss = {image_var(n): "scratch" for v in SYSTEMS.values() for n in v if image_var(n) not in env_values(prev_env)}
            sh(compose(cfg, "stop", *gone), "rollback compose stop", env={**os.environ, **miss}, timeout=600)
        if back:
            sh(compose(cfg, "up", "-d", "--wait", "--wait-timeout", str(cfg.wait_timeout), *back),
               "rollback compose up --wait", timeout=cfg.wait_timeout + 300)
        for s in {v[0] for v in back.values()}:  # each system answers with the snapshot of ITS previous release
            if (sid := snap_of(prev_links[s])):
                check_versions(cfg, {n: v for n, v in back.items() if v[0] == s}, sid)
        prune(cfg)
    except Exception as e:  # `pending` stays in state.json: the next run finishes the job
        return "FAILED", f"{reason}; ROLLBACK FAILED: {e}", None, []
    st.pop("pending", None)
    return "ROLLED_BACK", reason + ("" if back else "; first release: stopped, nothing is served"), None, []


def recover(cfg, st):
    """A kill, OOM or reboot after the flip leaves `pending` in state.json: put the previous release back, as a failed
    deploy would, before anything else runs. Returns (state, reason)."""
    p = st["pending"]
    prev_env = read_text(p["prev_env"])
    if prev_env is None:
        return "FAILED", f"release {p['rid']} was interrupted and {p['prev_env']} is missing: roll back by hand (RUNBOOK section 3)"
    prev = {s: Path(t) if t else None for s, t in p["prev"].items()}
    services = {n: (s, SYSTEMS[s][n]) for s in SYSTEMS for n in SYSTEMS[s] if n in p["services"]}
    state, reason, _, _ = rollback(cfg, st, services, list(prev), prev, prev_env,
                                   f"release {p['rid']} was interrupted (crash, kill or reboot)"
                                   + (f"; {TERRIER_NOTE}" if p.get("terrier") else ""))
    return state, reason


# --- inbox / status / main ---

def finish(cfg, st, rid, state, reason, svc, warn):
    """st is None when state.json was unreadable: write status only, never overwrite the damaged file."""
    ctx = st if st is not None else {}
    if state == "OK" and svc is not None:
        ctx["services"] = svc
    ctx["last"] = {"release_id": rid, "state": state, "reason": reason, "finished_utc": utc()}
    if st is not None:
        save_state(cfg, st)
    status = {"release_id": rid, "state": state, "reason": reason, "serving_release_id": ctx.get("release_id"),
              "services": ctx.get("services", {}), "warnings": warn, "finished_utc": ctx["last"]["finished_utc"]}
    try:
        cfg.status_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(cfg.status_file, json.dumps(status, indent=2, sort_keys=True))
    except OSError as e:  # a broken push area must not stop consume()
        log(f"could not write {cfg.status_file}: {e}")
    log(f"{rid or 'unknown'}: {state} {reason}".rstrip())


def consume(cfg, raw, tag, state, refused=()):
    """Move processed files out of the inbox, but never a newer upload that landed meanwhile.
    A refused entry (symlink, FIFO, oversize) is unlinked instead: it is never read, and would fail every run."""
    dest = cfg.state_dir / "processed" / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S}-{tag}-{state}"
    for n, b in raw.items():
        p = cfg.inbox / n
        try:
            same = b is not None and read_inbox_file(p) == b
        except StepError:
            same = False
        if same:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest / n))
    for n in refused:
        with contextlib.suppress(OSError):  # unlink never follows a link
            (cfg.inbox / n).unlink()
    for old in sorted((cfg.state_dir / "processed").glob("*"))[:-5]:  # ponytail: fixed 5; make a setting if forensics need more
        force_rmtree(old)


def update_once(cfg):
    """Process the inbox. Returns the final state, or None if there was nothing to do."""
    st, recovered = None, None
    with contextlib.suppress(StepError, OSError):  # an unreadable state.json is reported below, with the inbox
        st = load_state(cfg)
    if st and st.get("pending"):  # a crash interrupted a release: finish its rollback before touching anything else
        rid = st["pending"]["rid"]
        recovered, reason = recover(cfg, st)
        finish(cfg, st, rid, recovered, reason, None, [])
        if recovered != "ROLLED_BACK":
            return recovered
    if not os.path.lexists(cfg.inbox / "manifest.json"):
        return recovered
    raw, refused = {}, []
    for n in INBOX_FILES:
        try:
            raw[n] = read_inbox_file(cfg.inbox / n)
        except StepError as e:
            raw[n] = None
            refused.append((n, str(e)))
    st, rid, state, reason, svc, warn = None, None, "FAILED", "", None, []
    try:
        st = load_state(cfg)
        if refused:
            raise StepError("; ".join(r for _, r in refused))
        if cfg.require_mount and not os.path.ismount(cfg.data):
            raise StepError(f"{cfg.data} is not a mounted volume")
        if (miss := [n for n, b in raw.items() if b is None]):
            raise StepError(f"incomplete upload, missing {', '.join(miss)}")
        work = cfg.state_dir / "work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "manifest.json.sig").write_bytes(raw["manifest.json.sig"])
        p = run(["ssh-keygen", "-Y", "verify", "-f", str(cfg.allowed_signers), "-I", SIGNER, "-n", NAMESPACE,
                 "-s", str(work / "manifest.json.sig")], input=raw["manifest.json"])
        if p.rc:
            raise StepError("manifest signature invalid: " + (p.err or p.out).strip()[-200:])
        try:
            m = check_manifest(json.loads(raw["manifest.json"]))
        except ValueError as e:
            raise StepError("manifest is not JSON") from e
        rid = m["release_id"]
        state, reason, svc, warn = deploy(cfg, st, m, raw["snapshot_manifest.json"], raw["manifest.json"])
    except StepError as e:
        reason = str(e)
    except (OSError, sqlite3.Error) as e:
        reason = f"{type(e).__name__}: {e}"
    except Exception as e:  # a bug must never leave status.json stale
        reason = f"internal error: {e!r}"
    finish(cfg, st, rid, state, reason, svc, warn)
    consume(cfg, raw, rid or "unknown", state, [n for n, _ in refused])
    return state


def housekeeping(cfg):
    """Weekly restic forget+prune, as the push user so the laptop can still write the repo."""
    if not (cfg.restic_repo / "config").exists():
        return
    st = load_state(cfg)
    last = st.get("last_forget_utc")
    if last and dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last.replace("Z", "+00:00")) < dt.timedelta(days=7):
        return
    p = run(["setpriv", f"--reuid={cfg.push_user}", f"--regid={cfg.push_user}", "--init-groups", "restic", "forget",
             "--keep-last", "8", "--keep-monthly", "6", "--prune"], env=restic_env(cfg), timeout=4 * 3600)
    if p.rc:
        log(f"restic forget failed (will retry next run): {(p.err or p.out).strip()[-200:]}")
        return
    st["last_forget_utc"] = utc()
    save_state(cfg, st)


def main():
    cfg = Cfg()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    lock = open(cfg.state_dir / "lock", "w")
    if fcntl:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("another updater is running")
            return 0
    states = []
    for _ in range(3):  # a newer upload may have landed while we worked
        s = update_once(cfg)
        if s is None:
            break
        states.append(s)
    if not states or states[-1] == "OK":
        housekeeping(cfg)
    return 0 if all(s == "OK" for s in states) else 1


if __name__ == "__main__":
    sys.exit(main())

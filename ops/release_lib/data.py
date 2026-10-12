"""Data capture: serve sets -> hashed, staged file farm -> snapshot_manifest.json -> restic -> sftp inbox.

The farm (releases\\staging\\<id>\\<system>\\<rel>) is built from NTFS hard links, so it costs no disk and
reads the same bytes in place, yet a restic snapshot of it has exactly the manifest's layout. Only SQLite
files in WAL mode (or with -wal/-shm/-journal beside them) are copied, through sqlite3 backup().
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import sqlite3
import stat
from pathlib import Path

from .core import (NotConfigured, ReleaseError, Secret, atomic_write, canonical_json, need, read_credential,
                   run, sha256_bytes, sha256_file, which)

DENY_PATTERNS = ("keys.json", "*key*.txt", "dashboard_password*", ".secret", "*salt*", ".demo-key", "*.dpapi", ".env*",
                 "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "*.kdbx", ".netrc", "*credential*", "*token*")
NEVER_SHIP_NAMES = ("contacts.sqlite3*", "accounts.sqlite3*", "*.tar.gz")        # governed or server-owned stores
NEVER_SHIP_PARTS = {"luna-v1", "rowfw", "backups", "walk-backups", ".git", "__pycache__",
                    "email_package", "email_allinone"}
SQLITE_EXT = (".sqlite", ".sqlite3", ".db")
LINK_COPY_LIMIT = 256 * 1024 ** 2
SNAPSHOT_SCHEMA = "melange-snapshot-v1"


# ------------------------------------------------------------------ selection
def denied(rel_parts) -> str | None:
    """The first path component matching the hard denylist, regardless of any serve set."""
    for part in rel_parts:
        low = part.lower()
        if any(fnmatch.fnmatch(low, pattern) for pattern in DENY_PATTERNS):
            return part
    return None


def never_ship(rel_parts) -> bool:
    return any(p in NEVER_SHIP_PARTS or p.startswith(("email_package", "email_allinone")) for p in rel_parts) or \
        any(fnmatch.fnmatch(rel_parts[-1].lower(), pattern) for pattern in NEVER_SHIP_NAMES)


def _regular(path: Path):
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return info if stat.S_ISREG(info.st_mode) else None


def _expand_rule(cfg, rule) -> list[tuple[Path, str]]:
    """[(source file, path relative to the rule's src)] before denylist/never-ship filtering."""
    roots = {"melange_live": cfg["melange_live"], "mentat_live": cfg["mentat_live"],
             "terrier_checkout": cfg["terrier_checkout"]}
    base = Path(roots.get(rule["root"], rule["root"])) / rule.get("src", "")
    kind, picked = rule.get("kind", "files"), []
    if not base.is_dir():
        raise ReleaseError("SERVE_SET_MISSING", f"{base} does not exist")
    if kind == "files":
        for pattern in rule["include"]:
            pattern = "**/*" if pattern == "**" else pattern
            if re.search(r"[*?\[]", pattern):
                hits = list(base.glob(pattern))
            else:
                hits = [base / pattern] if (base / pattern).exists() else []
            if not hits and not rule.get("optional"):
                raise ReleaseError("SERVE_SET_MISSING", f"nothing matches {pattern} under {base}")
            for hit in hits:
                if hit.is_dir():
                    picked += [(p, p.relative_to(base).as_posix()) for p in hit.rglob("*")]
                else:
                    picked.append((hit, hit.relative_to(base).as_posix()))
    elif kind == "newest_pair":           # newest <glob> that has its <companion> beside it (bundle zip + .sha256)
        companion = rule.get("companion", ".sha256")
        pairs = [p for p in base.glob(rule["glob"]) if p.with_name(p.name + companion).is_file()]
        if not pairs:
            raise ReleaseError("SERVE_SET_MISSING", f"no {rule['glob']} with a {companion} companion under {base}")
        newest = max(pairs, key=lambda p: (p.stat().st_mtime_ns, p.name))
        name = rule.get("as", newest.name)                # "as": a fixed served name (Terrier's CU_BUNDLE is <dest>/bundle.zip)
        picked = [(newest, name), (newest.with_name(newest.name + companion), name + companion)]
    elif kind == "runs":                  # latest.json, the run it names, the newest run per group, extras
        latest = json.loads((base / rule["latest_file"]).read_text(encoding="utf-8"))
        keep = {str(latest["run_id"])}
        groups = {}
        for run_dir in sorted(p for p in base.iterdir() if p.is_dir() and re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]+", p.name)):
            try:
                value = json.loads((run_dir / "report.json").read_text(encoding="utf-8")).get(rule.get("group_key", ""))
            except (OSError, ValueError):
                value = None
            if value is not None:
                groups[str(value)] = run_dir.name              # sorted ascending, so the last write is the newest
        keep |= set(groups.values())
        if not (base / str(latest["run_id"])).is_dir():
            raise ReleaseError("SERVE_SET_MISSING", f"latest run {latest['run_id']} is not a directory under {base}")
        picked = [(base / rule["latest_file"], rule["latest_file"])]
        for name in sorted(keep):
            picked += [(p, p.relative_to(base).as_posix()) for p in (base / name).rglob("*")]
        picked += [(base / extra, extra) for extra in rule.get("extra", [])]
    else:
        raise ReleaseError("SERVE_SET_BAD", f"unknown rule kind {kind}")
    return picked


def resolve_files(cfg, serve_sets, extra=()) -> list[dict]:
    """Every file to ship: {system, dest, src, size, mtime_ns}. Denylisted -> refuse; never-ship -> silent."""
    files, seen, refused = {}, {}, []
    for system, spec in serve_sets["systems"].items():
        for rule in spec["rules"]:
            excludes = rule.get("exclude", [])
            for src, rel in _expand_rule(cfg, rule):
                info = _regular(src)
                if info is None:
                    continue
                parts = tuple(rel.split("/"))
                if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(parts[-1], pat) for pat in excludes) or never_ship(parts):
                    continue
                bad = denied(parts)
                if bad:
                    refused.append(f"{system}:{rel}")
                    continue
                dest = "/".join(x for x in (spec["data_subdir"], rule.get("dest", "").strip("/"), rel) if x)
                if dest in files and files[dest]["src"] != src:
                    raise ReleaseError("DEST_COLLISION", f"{dest} is produced by two different sources")
                files[dest] = {"system": system, "dest": dest, "src": src, "size": info.st_size, "mtime_ns": info.st_mtime_ns}
    for src, dest in extra:                       # built artifacts (reader downloads): same filters
        parts = tuple(dest.split("/"))
        if denied(parts) or never_ship(parts) or never_ship(tuple(Path(src).parts)):
            refused.append(dest)
            continue
        info = os.stat(src)
        files[dest] = {"system": parts[0], "dest": dest, "src": Path(src), "size": info.st_size, "mtime_ns": info.st_mtime_ns}
    if refused:
        raise ReleaseError("DENYLIST", f"{len(refused)} selected file(s) match the secret denylist, e.g. {refused[:3]}; "
                                       "fix the serve set, nothing was shipped")
    return [files[key] for key in sorted(files)]


def summarize(files) -> dict:
    out = {}
    for f in files:
        row = out.setdefault(f["system"], {"files": 0, "bytes": 0})
        row["files"] += 1
        row["bytes"] += f["size"]
    return out


# ------------------------------------------------------------------ hashing + staging
class HashCache:
    """sha256 keyed by (path, size, mtime_ns); an unchanged file is never re-read."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("CREATE TABLE IF NOT EXISTS h(path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, sha256 TEXT)")
        self.hits = self.misses = 0

    def digest(self, path: Path, size: int, mtime_ns: int) -> str:
        row = self.db.execute("SELECT sha256 FROM h WHERE path=? AND size=? AND mtime_ns=?", (str(path), size, mtime_ns)).fetchone()
        if row:
            self.hits += 1
            return row[0]
        self.misses += 1
        value = sha256_file(path)
        self.db.execute("INSERT OR REPLACE INTO h VALUES(?,?,?,?)", (str(path), size, mtime_ns, value))
        return value

    def close(self):
        self.db.commit()
        self.db.close()


def needs_staging(path: Path) -> bool:
    """A SQLite file in WAL mode, or with -wal/-shm/-journal beside it, may be open elsewhere: copy it."""
    if path.suffix.lower() not in SQLITE_EXT:
        return False
    try:
        with open(path, "rb") as handle:
            header = handle.read(100)
    except OSError:
        return True
    if not header.startswith(b"SQLite format 3\x00"):
        return False
    if len(header) >= 20 and 2 in (header[18], header[19]):
        return True
    return any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


def sqlite_copy(src: Path, dst: Path) -> None:
    """Consistent copy through the sqlite3 backup API (read-only source), mtime preserved."""
    info = os.stat(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    source = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True, timeout=60)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    os.utime(dst, ns=(info.st_atime_ns, info.st_mtime_ns))


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    try:
        if not os.stat(src).st_mode & stat.S_IWRITE:        # a read-only link could only be deleted by clearing the original's flag
            raise OSError("read-only source: copy instead")
        os.link(src, dst)
    except OSError:
        if os.path.getsize(src) > LINK_COPY_LIMIT:
            raise ReleaseError("LINK_FAILED", f"cannot hard-link {src.name} and it is too large to copy")
        shutil.copy2(src, dst)


def build_farm(files, stage_dir: Path, cache: HashCache, progress=None) -> list[dict]:
    """Hash every file and build the restic source tree. Returns manifest rows (path,size,sha256,mtime_ns)."""
    from .gates import disk_free
    for f in files:
        f["stage"] = needs_staging(f["src"])
    check = disk_free(stage_dir, sum(f["size"] for f in files if f["stage"]))
    if not check["ok"]:
        raise ReleaseError("DISK", check["detail"])
    rows = []
    for index, f in enumerate(files):
        target = stage_dir / f["dest"]
        if f["stage"]:
            sqlite_copy(f["src"], target)
            size, sha = os.stat(target).st_size, sha256_file(target)
        else:
            size, sha = f["size"], cache.digest(f["src"], f["size"], f["mtime_ns"])
            link_or_copy(f["src"], target)
        rows.append({"path": f["dest"], "size": size, "sha256": sha, "mtime_ns": f["mtime_ns"]})
        if index % 2000 == 0:
            cache.db.commit()
            if progress:
                progress(index, len(files))
    return sorted(rows, key=lambda r: r["path"])


def verify_unchanged(files) -> None:
    """After restic has read the farm, every in-place source must still have its hashed size and mtime."""
    moved = []
    for f in files:
        info = _regular(f["src"])
        if info is None or (info.st_size, info.st_mtime_ns) != (f["size"], f["mtime_ns"]):
            moved.append(f["dest"])
    if moved:
        raise ReleaseError("SOURCE_CHANGED", f"{len(moved)} file(s) changed during capture, e.g. {moved[:3]}; rerun when quiet")


def snapshot_manifest(release_id: str, rows, governance: str, profile_sha256: str) -> bytes:
    """`governance` is INVITED_BUILD.json's own word ("ruled" or "pending"), `profile_sha256` the profile it was built under."""
    return canonical_json({"schema": SNAPSHOT_SCHEMA, "release_id": release_id, "governance": governance,
                           "profile_sha256": profile_sha256, "files": rows})


# ------------------------------------------------------------------ restic + sftp (outward)
def vps_host(cfg) -> str:
    need(cfg, "domain", "hosts.ssh", "vps.ssh_user")
    return f"{cfg['hosts']['ssh']}.{cfg['domain']}"


def transport_env(cfg, cred=read_credential) -> dict:
    """cloudflared service-token env for svc-push. Values stay Secret objects until run() passes them to the child."""
    user, secret = cred(cfg["credentials"]["svc_push"])
    if not user or not secret:
        raise NotConfigured(f"credential {cfg['credentials']['svc_push']!r} needs a username (token id) and a secret")
    return {"TUNNEL_SERVICE_TOKEN_ID": Secret(user), "TUNNEL_SERVICE_TOKEN_SECRET": secret}


def _proxy(cfg) -> str:
    which("cloudflared")
    return "cloudflared access ssh --hostname %h"


def restic_args(cfg, *tail) -> list[str]:
    host, user = vps_host(cfg), cfg["vps"]["ssh_user"]
    identity = f" -i {cfg['vps']['ssh_identity']}" if cfg["vps"].get("ssh_identity") else ""
    sftp = f'ssh -o BatchMode=yes{identity} -o ProxyCommand="{_proxy(cfg)}" {user}@{host} -s sftp'
    return ["restic", "-r", f"sftp:{user}@{host}:{cfg['vps']['restic_repo']}", "-o", f"sftp.command={sftp}", *tail]


TREE_PATH = re.compile(r"^/[A-Za-z0-9._@ /-]*$")        # what melange_update.py accepts as data_snapshot.restic_path


def restic_tree_path(path: str) -> str:
    """A snapshot path as restic stores it in the tree. On Windows `C:\\a\\b` is `/C/a/b` (the drive letter is the top
    directory; see `restic ls`), so `restic restore <id>:<this> --target T` puts the farm's children (melange/, mentat/,
    terrier/) directly under T. A POSIX path is unchanged. Anything else (UNC, relative) is refused."""
    drive = re.fullmatch(r"([A-Za-z]):[\\/]+(.*)", path)
    tree = f"/{drive.group(1)}/{drive.group(2).replace(chr(92), '/')}" if drive else path
    tree = tree.rstrip("/") or "/"
    if not tree.startswith("/") or ".." in tree or not TREE_PATH.fullmatch(tree):
        raise ReleaseError("RESTIC_PATH", f"cannot ship {path!r}: the VPS restores only plain absolute paths ({TREE_PATH.pattern})")
    return tree


def farm_tree_path(snapshot: dict) -> str:
    """The restic tree path of the one directory a snapshot holds, from `restic snapshots --json` (never from our own farm path)."""
    paths = snapshot.get("paths") or []
    if len(paths) != 1:
        raise ReleaseError("RESTIC_PATH", f"snapshot {str(snapshot.get('id', '?'))[:8]} records {len(paths)} paths; expected the farm only")
    return restic_tree_path(paths[0])


def full_snapshot_id(snapshot: dict) -> str:
    """restic's full 64-hex id. `backup --json` reports a short one; the updater restores by the full id only."""
    snapid = str(snapshot.get("id", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", snapid):
        raise ReleaseError("RESTIC_SNAPSHOT_ID", f"restic gave {snapid[:8]!r}, not a full 64-hex snapshot id")
    return snapid


def restic_backup(cfg, release_id: str, stage_dir: Path, workdir: Path, cred=read_credential) -> dict:
    """Idempotent: a snapshot already tagged release=<id> is reused. Returns the snapshot id and `restic_path`, the farm's
    path inside the snapshot (data_snapshot.restic_path). Secrets travel in the environment only."""
    need(cfg, "credentials.restic", "credentials.svc_push")
    which("restic")
    restic_tree_path(str(stage_dir))                                     # refuse an unshippable staging path before 55 GB go out
    _, restic_secret = cred(cfg["credentials"]["restic"])
    env = {"RESTIC_PASSWORD": restic_secret, **transport_env(cfg, cred)}
    if run(restic_args(cfg, "cat", "config"), env=env, check=False).returncode != 0:
        run(restic_args(cfg, "init"), env=env)                           # first release only; fails loudly if the repo is unreachable
    existing = json.loads(run(restic_args(cfg, "snapshots", "--tag", f"release={release_id}", "--json"), env=env).stdout or b"[]")
    if existing:
        return {"snapshot_id": full_snapshot_id(existing[-1]), "reused": True, "restic_path": farm_tree_path(existing[-1])}
    listing = workdir / "restic-files.txt"
    atomic_write(listing, (str(stage_dir) + "\n").encode("utf-8"))       # one directory line: restic recurses
    limit = ["--limit-upload", str(cfg["vps"]["limit_upload_kib"])] if cfg["vps"].get("limit_upload_kib") else []
    out = run(restic_args(cfg, "backup", "--tag", f"release={release_id}", "--ignore-inode", "--ignore-ctime",
                          "--files-from-verbatim", str(listing), "--json", *limit), env=env, timeout=None).stdout
    summary = None
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("message_type") == "summary":
            summary = message
    if not summary or not summary.get("snapshot_id"):
        raise ReleaseError("RESTIC_NO_SUMMARY", "restic produced no snapshot summary")
    made = json.loads(run(restic_args(cfg, "snapshots", summary["snapshot_id"], "--json"), env=env).stdout or b"[]")
    if len(made) != 1:
        raise ReleaseError("RESTIC_NO_SNAPSHOT", f"restic cannot list the snapshot it just made ({summary['snapshot_id'][:8]})")
    return {"snapshot_id": full_snapshot_id(made[0]), "reused": False, "files": summary.get("total_files_processed"),
            "restic_path": farm_tree_path(made[0])}


def _sftp(cfg, batch: Path, cred) -> None:
    host, user = vps_host(cfg), cfg["vps"]["ssh_user"]
    argv = ["sftp", "-b", str(batch), "-o", "BatchMode=yes", "-o", f"ProxyCommand={_proxy(cfg)}"]
    if cfg["vps"].get("ssh_identity"):
        argv += ["-i", cfg["vps"]["ssh_identity"]]
    run(argv + [f"{user}@{host}"], env=transport_env(cfg, cred))


def push_release_files(cfg, files, workdir: Path, cred=read_credential) -> None:
    """Upload to the inbox in contract order. Each name appears atomically (.part then rename), manifest.json last.
    The updater and its path unit act on manifest.json only and ignore *.part. sftp's rename refuses an existing target, so a
    leftover from an interrupted earlier push is removed first (`-rm` ignores 'no such file')."""
    order = ["snapshot_manifest.json", "manifest.json.sig", "manifest.json"]
    if sorted(order) != sorted(files):
        raise ReleaseError("PUSH_SET", f"expected exactly {order}")
    inbox, lines = cfg["vps"]["inbox"].rstrip("/"), []
    for name in order:
        lines += [f'put "{Path(files[name]).as_posix()}" "{inbox}/{name}.part"', f'-rm "{inbox}/{name}"',
                  f'rename "{inbox}/{name}.part" "{inbox}/{name}"']
    batch = workdir / "sftp-push.batch"
    atomic_write(batch, ("\n".join(lines) + "\n").encode("utf-8"))
    _sftp(cfg, batch, cred)


def fetch_status(cfg, workdir: Path, cred=read_credential) -> dict:
    local = workdir / "vps-status.json"
    local.unlink(missing_ok=True)
    batch = workdir / "sftp-status.batch"
    atomic_write(batch, f'get "{cfg["vps"]["status_path"]}" "{local.as_posix()}"\n'.encode("utf-8"))
    _sftp(cfg, batch, cred)
    return json.loads(local.read_text(encoding="utf-8"))

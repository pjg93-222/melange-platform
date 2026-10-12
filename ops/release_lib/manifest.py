"""The signed release manifest, lock-envelope check, reader downloads and the Pocket build.

manifest.json / snapshot_manifest.json are canonical JSON; the manifest is signed with
`ssh-keygen -Y sign -n melange-release` and the VPS verifies it against its allowed_signers.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .. import security_gate
from .core import (NotConfigured, ReleaseError, Secret, atomic_write, canonical_json, invited_dir, load_live_module,
                   need, parse_id, run, sha256_bytes, sha256_file, which)
from .data import TREE_PATH, denied, link_or_copy, never_ship

SCHEMA = "melange-platform-release-v1"
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")   # IMAGE_RE and COMMIT_RE: always .fullmatch (`$` lets a trailing newline through)
COMMIT_RE =re.compile(r"^[0-9a-f]{40}$")
SERVICES = {"melange": ["melange-desk"], "mentat": ["mentat-console"], "terrier": ["terrier-co05", "terrier-or05", "terrier-ct01"]}
IMAGE_NAMES = {"melange": "melange-desk", "mentat": "mentat-console", "terrier": "terrier-web"}
ENVELOPE = re.compile(rb'var D=\{s:"([A-Za-z0-9+/=]+)",i:"([A-Za-z0-9+/=]+)",c:"([A-Za-z0-9+/=]+)",n:(\d+)\}')
FALLBACK_KEYGEN = r"C:\Windows\System32\OpenSSH\ssh-keygen.exe"


# ------------------------------------------------------------------ manifest
def build_manifest(cfg, *, release_id, previous, kind, created_utc, platform_commit, tag, commits, digests,
                   snapshot, packages, gates) -> dict:
    """`commits`: {system: sha}; `digests`: {system: 'sha256:<hex>'}; `snapshot`: the data_snapshot object."""
    sources = {"melange": ("local:melange-systems", cfg.get("melange_ref", "main")),
               "mentat": ("local:melange-systems", cfg["mentat_ref"]),
               "terrier": (cfg["terrier_repo"], cfg["terrier_ref"])}
    systems = {}
    for name, (repo, ref) in sources.items():
        base = cfg["images"][name]
        systems[name] = {"source": {"repo": repo, "ref": ref, "commit": commits[name]},
                         "image": f"{base}@{digests[name]}", "data_subdir": name, "services": SERVICES[name]}
    manifest = {"schema": SCHEMA, "release_id": release_id, "previous": previous, "kind": kind, "created_utc": created_utc,
                "platform_repo": {"repo": cfg["platform_repo"], "commit": platform_commit, "tag": tag},
                "systems": systems, "data_snapshot": snapshot, "packages": packages, "gates": gates,
                "signer": cfg["signing"]["principal"]}
    validate(manifest)
    return manifest


def validate(m: dict) -> None:
    """Structural check shared by build and verify. Raises ReleaseError('MANIFEST_INVALID', ...)."""
    def bad(why):
        raise ReleaseError("MANIFEST_INVALID", why)
    if m.get("schema") != SCHEMA:
        bad("wrong schema")
    new = parse_id(m.get("release_id", ""))
    if m.get("previous") is not None and parse_id(m["previous"]) >= new:
        bad("previous is not older than release_id")
    if m.get("kind") not in ("full", "data-only"):
        bad("kind must be full or data-only")
    repo = m.get("platform_repo") or {}
    if not (COMMIT_RE.fullmatch(repo.get("commit", "")) and re.fullmatch(r"v[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+", repo.get("tag", ""))):
        bad("platform_repo commit/tag malformed")
    if set(m.get("systems", {})) != set(SERVICES):
        bad("systems must be exactly melange, mentat, terrier")
    for name, system in m["systems"].items():
        if not IMAGE_RE.fullmatch(system.get("image", "")):
            bad(f"{name} image is not pinned by digest")
        if not COMMIT_RE.fullmatch(system.get("source", {}).get("commit", "")):
            bad(f"{name} source commit malformed")
        if system.get("services") != SERVICES[name] or system.get("data_subdir") != name:
            bad(f"{name} services/data_subdir wrong")
    snap = m.get("data_snapshot") or {}
    if not (snap.get("restic_snapshot_id") and re.fullmatch(r"[0-9a-f]{64}", snap.get("manifest_sha256", ""))
            and isinstance(snap.get("files"), int) and isinstance(snap.get("bytes"), int)):
        bad("data_snapshot incomplete")
    if not (isinstance(snap.get("restic_path"), str) and TREE_PATH.fullmatch(snap["restic_path"]) and ".." not in snap["restic_path"]):
        bad("data_snapshot.restic_path missing or not a plain absolute path (where the farm sits inside the restic snapshot)")
    for name, pkg in (m.get("packages") or {}).items():
        if not (re.fullmatch(r"[0-9a-f]{64}", pkg.get("sha256", "")) and isinstance(pkg.get("bytes"), int)):
            bad(f"package {name} malformed")
    if not isinstance(m.get("gates"), dict) or not m["gates"]:
        bad("gates missing")
    if not m.get("signer"):
        bad("signer missing")


def keygen() -> str:
    try:
        return which("ssh-keygen")
    except NotConfigured:
        if Path(FALLBACK_KEYGEN).is_file():
            return FALLBACK_KEYGEN
        raise


def sign_manifest(cfg, manifest_path: Path) -> Path:
    """ssh-keygen -Y sign writes <manifest>.sig. With signing.agent the key is used through ssh-agent."""
    need(cfg, "signing.key", "signing.namespace")
    key = cfg["signing"]["key"]
    agent = bool(cfg["signing"].get("agent"))
    if not Path(key if not agent else key + ".pub").is_file():
        raise NotConfigured(f"signing key {key}{'.pub' if agent else ''} not found; run `release.py init-signing`")
    manifest_path = Path(manifest_path)
    sig = manifest_path.with_name(manifest_path.name + ".sig")
    sig.unlink(missing_ok=True)
    argv = [keygen(), "-Y", "sign", "-f", key + ".pub" if agent else key, "-n", cfg["signing"]["namespace"]]
    run(argv + (["-U"] if agent else []) + [manifest_path.name], cwd=manifest_path.parent)
    if not sig.is_file():
        raise ReleaseError("SIGN_FAILED", "ssh-keygen produced no signature")
    return sig


def verify_manifest(manifest_path, allowed_signers, *, principal="release@melange", namespace="melange-release",
                    current_id=None, snapshot_path=None) -> dict:
    """Signature, structure, snapshot hash, then downgrade policy. Returns {'action': 'apply'|'noop', ...}."""
    manifest_path = Path(manifest_path)
    data, sig = manifest_path.read_bytes(), manifest_path.with_name(manifest_path.name + ".sig")
    if not sig.is_file():
        raise ReleaseError("SIGNATURE_MISSING", f"{sig.name} not found")
    result = run([keygen(), "-Y", "verify", "-f", str(allowed_signers), "-I", principal, "-n", namespace, "-s", str(sig)],
                 input=data, check=False)
    if result.returncode != 0:
        raise ReleaseError("SIGNATURE_BAD", "manifest does not verify against allowed_signers")
    manifest = json.loads(data.decode("utf-8"))
    validate(manifest)
    if snapshot_path and sha256_file(snapshot_path) != manifest["data_snapshot"]["manifest_sha256"]:
        raise ReleaseError("SNAPSHOT_MISMATCH", "snapshot_manifest.json does not match the signed hash")
    if current_id is not None:
        have, want = parse_id(current_id), parse_id(manifest["release_id"])
        if want < have:
            raise ReleaseError("DOWNGRADE", f"{manifest['release_id']} is older than the installed {current_id}")
        if want == have:
            return {"action": "noop", "release_id": manifest["release_id"]}
    return {"action": "apply", "release_id": manifest["release_id"], "manifest": manifest}


# ------------------------------------------------------------------ lock envelope + downloads
def is_lock_envelope(path) -> bool:
    """True only if the file parses as the lock_dashboard wrapper (base64 salt(16)/iv(12)/ciphertext + PBKDF2 count) AND
    passes the security gate's own check (AES-GCM page, small wrapper, incompressible ciphertext), so plaintext cannot
    ride along with an envelope. The second check is security_gate.is_locked_envelope itself, imported not copied."""
    try:
        match = ENVELOPE.search(Path(path).read_bytes())
        if not match:
            return False
        salt, iv, ct = (base64.b64decode(match.group(i), validate=True) for i in (1, 2, 3))
        return (len(salt) == 16 and len(iv) == 12 and len(ct) > 16 and int(match.group(4)) >= 100_000
                and security_gate.is_locked_envelope(path))
    except (OSError, ValueError):
        return False


class NoInvitedLock(ReleaseError):
    def __init__(self, message: str):
        super().__init__("NO_INVITED_LOCK", message)


def pin_invited_desk(cfg, out_html: Path) -> dict:
    """D-171 / R3: third parties (reader downloads, Netlify) only ever get the INVITED desk, never the full Moneyball.html.
    Copies deploy/index.html to out_html iff the facts ops/deploy_desk.py preflights hold: it is a lock envelope,
    publish_dashboard.py's receipt matches it, and the receipt's source is outputs/invited_build/Moneyball.html as
    INVITED_BUILD.json describes it. Anything else raises NoInvitedLock; callers then omit the desk, never fall back."""
    live, folder = Path(cfg["melange_live"]), invited_dir(cfg)
    index = live / "deploy" / "index.html"
    Path(out_html).unlink(missing_ok=True)                                  # a copy pinned by an earlier attempt must not survive a refusal
    try:
        receipt = json.loads((live / "data" / "engine" / "dashboard_deployment_receipt.json").read_text(encoding="utf-8"))
        want = json.loads((folder / "INVITED_BUILD.json").read_text(encoding="utf-8"))["outputs"]["Moneyball.html"]["sha256"]
        built, locked = sha256_file(folder / "Moneyball.html"), sha256_file(index)
        source, artifact = receipt["source"]["sha256"], receipt["local_deploy_artifact"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise NoInvitedLock("outputs/invited_build, deploy/index.html or its receipt is missing or unreadable")
    if not is_lock_envelope(index):
        raise NoInvitedLock("deploy/index.html does not parse as a lock envelope")
    if artifact != locked:
        raise NoInvitedLock("the deployment receipt does not match deploy/index.html")
    if not source == want == built:
        raise NoInvitedLock("deploy/index.html is not a lock of the invited build's Moneyball.html")
    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(index, out_html)                                        # pinned: later edits to the live tree cannot change what ships
    if sha256_file(out_html) != locked:
        raise NoInvitedLock("deploy/index.html changed while it was being pinned")
    return {"path": str(out_html), "sha256": locked, "source_sha256": source}


def build_downloads(cfg, release_id, stage_root: Path, desk: Path, pocket: Path | None, data_subdir="melange") -> dict:
    """downloads/<id>/: locked desk (only if it is a lock envelope) + locked Pocket + optional workbooks + SHA256SUMS,
    and the same files again under downloads/latest/ with the release id dropped from their names (melange-desk.html):
    desk_server serves /downloads/<id|latest>/<name> from DESK_ROOT/downloads, and a data snapshot cannot hold a symlink.
    Returns {'extra': [(file, dest)], 'notes': [...]}. The email package is never an input."""
    notes, names = [], {}
    for label, source in (("melange-desk", desk), ("mentat-pocket", pocket)):
        if source and Path(source).is_file() and is_lock_envelope(source):
            names[f"{label}-{release_id}.html"] = Path(source)
        else:
            notes.append(f"{label} omitted: missing or not a lock envelope")
    for workbook in cfg.get("downloads", {}).get("workbooks", []):
        if denied(Path(workbook).parts) or never_ship(Path(workbook).parts):
            raise ReleaseError("DENYLIST", f"{Path(workbook).name}: the email package and secret files are never shipped")
        names[Path(workbook).name] = Path(workbook)
    extra = []
    for sub, drop in ((release_id, ""), ("latest", f"-{release_id}")):
        folder = Path(stage_root) / "downloads" / sub
        folder.mkdir(parents=True, exist_ok=True)
        served, sums = {name.replace(drop, "") if drop else name: source for name, source in names.items()}, []
        for name, source in sorted(served.items()):
            link_or_copy(source, folder / name)
            sums.append(f"{sha256_file(folder / name)}  {name}")
        atomic_write(folder / "SHA256SUMS", ("\n".join(sums) + "\n").encode("utf-8"))
        extra += [(folder / n, f"{data_subdir}/downloads/{sub}/{n}") for n in [*sorted(served), "SHA256SUMS"]]
    return {"extra": extra, "notes": notes}


# ------------------------------------------------------------------ Pocket
def dashboard_password(cfg) -> Secret:
    path = cfg.get("dashboard_password_file") or os.environ.get("DASHBOARD_PASSWORD_FILE")
    if not path or not Path(path).is_file():
        raise NotConfigured("dashboard password file: set DASHBOARD_PASSWORD_FILE or dashboard_password_file")
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise NotConfigured("dashboard password file is empty")
    return Secret(value)


def pocket_dir(cfg) -> Path:
    if cfg.get("pocket", {}).get("dir"):
        return Path(cfg["pocket"]["dir"])
    setting = json.loads((Path(cfg["mentat_live"]) / "mentat" / "config" / "report-directory.json").read_text(encoding="utf-8-sig"))
    return Path(setting["directory"]) / "pocket"


def build_pocket(cfg, out_html: Path, password: Secret) -> dict:
    """Mentat's own builder (needs a running console only when pocket.console_port is set), gated by selfcheck.py,
    then locked with lock_dashboard(). A failing selfcheck stops the release."""
    tools = Path(cfg["mentat_live"]) / "mentat" / "tools" / "pocket"
    folder, port = pocket_dir(cfg), cfg.get("pocket", {}).get("console_port")
    if port:
        need(cfg, "pocket.datasets")
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("icon-180.png", "manifest.webmanifest"):
            (folder / name).write_bytes((tools / name).read_bytes())
        run([sys.executable, str(tools / "extract_pocket_data.py"), "--port", str(port), "--out-dir", str(folder),
             "--datasets", ",".join(cfg["pocket"]["datasets"])], timeout=3600)
        run([sys.executable, str(tools / "build_page.py"), "--data", str(folder / "pocket-data.json"),
             "--out", str(folder / "mentat-pocket.html")])
    page = folder / "mentat-pocket.html"
    if not page.is_file():
        raise NotConfigured(f"{page} has not been built; set pocket.console_port + pocket.datasets or publish it from the console")
    check = run([sys.executable, str(tools / "selfcheck.py"), "--dir", str(folder)], check=False, timeout=900)
    text = (check.stdout + check.stderr).decode("utf-8", "replace")
    if check.returncode != 0 or "ALL CHECKS PASSED" not in text:
        raise ReleaseError("POCKET_SELFCHECK", (text.strip().splitlines() or ["selfcheck failed"])[-1][:200])
    locker = load_live_module(Path(cfg["melange_live"]) / "fec" / "surfaces" / "lock_dashboard.py", "lock_dashboard")
    locker.lock_dashboard(str(page), str(out_html), password.reveal())
    if not is_lock_envelope(out_html):
        raise ReleaseError("POCKET_NOT_LOCKED", "locked Pocket does not parse as a lock envelope")
    return {"path": str(out_html), "sha256": sha256_file(out_html), "bytes": out_html.stat().st_size}

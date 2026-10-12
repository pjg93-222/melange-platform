"""Shared plumbing for the release command: config, secrets, subprocess, state, Glassbox, ids.

Stdlib only. Secrets live in a Secret wrapper whose repr/str hide the value and reach a child process
only through its environment (or an HTTP header); run() refuses an argv that contains one.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

OPS = Path(__file__).resolve().parents[1]
CONFIG_PATH = OPS / "release_config.json"
ID_RE = re.compile(r"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+$")   # always .fullmatch: `$` alone lets a trailing newline through
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_ZERO = "0" * 40


class ReleaseError(Exception):
    """A named, fail-closed stop. `code` is stable for tests and the plan report."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message = code, message


class NotConfigured(ReleaseError):
    def __init__(self, message: str):
        super().__init__("NOT_CONFIGURED", message)


class Pending(Exception):
    """An outward step is ready but --confirm-outward was not given. Not an error: stop cleanly."""

    def __init__(self, step: str):
        super().__init__(f"pending confirmation: {step}")
        self.step = step


# ---------------------------------------------------------------- ids / hashing / json
def parse_id(release_id: str) -> tuple[int, ...]:
    if not isinstance(release_id, str) or not ID_RE.fullmatch(release_id):
        raise ReleaseError("BAD_ID", f"release id must be YYYY.MM.DD.N, got {release_id!r}")
    return tuple(int(part) for part in release_id.split("."))


def next_release_id(existing, today=None) -> str:
    """CalVer: today's date plus one more than the highest N already used today. Never goes backward."""
    today = today or datetime.now(timezone.utc).date()
    prefix = today.strftime("%Y.%m.%d")
    known = [parse_id(value) for value in existing]
    used = [k[3] for k in known if "%04d.%02d.%02d" % k[:3] == prefix]
    candidate = f"{prefix}.{max(used, default=0) + 1}"
    if known and parse_id(candidate) <= max(known):
        raise ReleaseError("ID_ORDER", f"{candidate} would not sort after the newest release {'.'.join(map(str, max(known)))}")
    return candidate


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write(path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------- config
def _expand(value):
    if isinstance(value, str):
        return re.sub(r"%([A-Za-z0-9_]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


def _merge(base, over):
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=None, overrides=None) -> dict:
    cfg = json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))
    return _expand(_merge(cfg, overrides or {}))


def invited_dir(cfg) -> Path:
    """Where fec/surfaces/make_invited_build.py writes the invited desk (its default; serve_sets.json ships it from here)."""
    return Path(cfg["melange_live"]) / "outputs" / "invited_build"


def releases_root(cfg) -> Path:
    base = cfg.get("releases_dir") or os.path.join(os.environ.get("LOCALAPPDATA", str(Path.home())), "Melange", "releases")
    return Path(base)


def require(cfg, *keys) -> list[str]:
    """Dotted config keys that are empty. Callers raise NotConfigured(…) from the result."""
    missing = []
    for key in keys:
        node = cfg
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node in (None, "", [], {}):
            missing.append(key)
    return missing


def need(cfg, *keys) -> None:
    missing = require(cfg, *keys)
    if missing:
        raise NotConfigured("missing config: " + ", ".join(missing))


# ---------------------------------------------------------------- secrets
class Secret:
    """Holds a secret value; repr/str/format never show it. Call reveal() only at the point of use."""

    __slots__ = ("_v",)

    def __init__(self, value: str):
        object.__setattr__(self, "_v", value)

    def reveal(self) -> str:
        return self._v

    def __repr__(self):
        return "Secret(****)"

    __str__ = __repr__

    def __format__(self, spec):
        return "Secret(****)"

    def __bool__(self):
        return bool(self._v)

    def __reduce__(self):
        raise TypeError("a Secret cannot be pickled")


class _CRED(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_uint64),
        ("CredentialBlobSize", ctypes.c_uint32), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", ctypes.c_uint32), ("AttributeCount", ctypes.c_uint32), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p),
    ]


def _decode_blob(blob: bytes) -> str:
    # cmdkey and the PowerShell modules store UTF-16LE; anything else is taken as UTF-8.
    if blob and len(blob) % 2 == 0 and not any(blob[1::2]):
        return blob.decode("utf-16-le")
    return blob.decode("utf-8", "replace")


def read_credential(target: str) -> tuple[str, Secret]:
    """(username, secret) of a Windows Credential Manager generic credential. NotConfigured when absent."""
    if os.name != "nt":
        raise NotConfigured("Windows Credential Manager is only available on Windows")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.POINTER(_CRED))]
    advapi.CredReadW.restype = ctypes.c_int
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    found = ctypes.POINTER(_CRED)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(found)):          # 1 = CRED_TYPE_GENERIC
        raise NotConfigured(f"credential {target!r} is not in Windows Credential Manager")
    try:
        cred = found.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize) if cred.CredentialBlobSize else b""
        return (cred.UserName or ""), Secret(_decode_blob(blob))
    finally:
        advapi.CredFree(found)


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi_crypt(value: bytes, decrypt: bool = False) -> bytes:
    """Per-user DPAPI, mirroring crypt() in Mentat's tools/fec_key_store.py (flag 1 = no UI)."""
    if os.name != "nt":
        raise NotConfigured("DPAPI requires Windows")
    buffer = ctypes.create_string_buffer(value)
    source = _Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    out = _Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_Blob)]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(out)):
        raise ReleaseError("DPAPI", "Windows could not protect/unprotect this secret for the current user")
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(out.data)


def store_dpapi_secret(path, value: str) -> None:
    atomic_write(path, dpapi_crypt(value.encode("utf-8")))


def load_dpapi_secret(path) -> Secret:
    path = Path(path)
    if not path.is_file():
        raise NotConfigured(f"secret store {path.name} does not exist; see ops/RELEASE.md")
    return Secret(dpapi_crypt(path.read_bytes(), decrypt=True).decode("utf-8"))


# ---------------------------------------------------------------- subprocess
def which(name: str, env=None) -> str:
    found = shutil.which(name, path=(env or os.environ).get("PATH"))
    if not found:
        raise NotConfigured(f"required tool {name!r} was not found on PATH")
    return found


def scrub(text: str, secrets) -> str:
    for secret in secrets:
        if secret.reveal():
            text = text.replace(secret.reveal(), "****")
    return text


def run(argv, *, env=None, cwd=None, input=None, timeout=None, check=True) -> subprocess.CompletedProcess:
    """subprocess.run with secret hygiene. env values that are Secret are revealed here and nowhere else;
    an argv containing any of them is refused, and output is scrubbed before it can reach an error."""
    env = env or {}
    secrets = [value for value in env.values() if isinstance(value, Secret)]
    for arg in argv:
        for secret in secrets:
            if secret.reveal() and secret.reveal() in str(arg):
                raise ReleaseError("SECRET_IN_ARGV", f"refusing to put a secret on the command line of {argv[0]}")
    child = dict(os.environ)
    child.update({k: (v.reveal() if isinstance(v, Secret) else str(v)) for k, v in env.items()})
    exe = which(str(argv[0]), child)
    result = subprocess.run([exe, *map(str, argv[1:])], capture_output=True, cwd=cwd, env=child,
                            input=input, timeout=timeout)
    if check and result.returncode != 0:
        tail = scrub((result.stderr or b"").decode("utf-8", "replace")[-600:].strip(), secrets)
        raise ReleaseError("CMD_FAILED", f"{Path(str(argv[0])).name} {argv[1] if len(argv) > 1 else ''} exited "
                                         f"{result.returncode}: {tail}")
    return result


def git(*args, cwd=None, git_dir=None, env=None, input=None, check=True) -> bytes:
    """Run git and return stdout bytes. No eol conversion is ever invoked by the plumbing used here."""
    # core.hooksPath= (empty) disables every hook of the repo (reference-transaction, pre-push, ...): the release runs no repo code
    argv = ["git"] + (["--git-dir", str(git_dir)] if git_dir else []) + ["-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
                                                                          "-c", "core.hooksPath=", *args]
    if args[:1] == ("push",) and any(a in ("-f", "--mirror", "--delete") or a.startswith(("--force", "+")) for a in args):
        raise ReleaseError("FORCE_REFUSED", "the release command never force-pushes")
    return run(argv, cwd=cwd, env=env, input=input, check=check).stdout


def git_text(*args, **kw) -> str:
    return git(*args, **kw).decode("utf-8", "replace").strip()


# ---------------------------------------------------------------- state + glassbox
class State(dict):
    """The small per-release JSON. Facts are rechecked externally; this holds only fixed inputs and ids."""

    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        if self.path.is_file():
            self.update(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self):
        atomic_write(self.path, json.dumps(self, indent=1, sort_keys=True).encode("utf-8"))


def load_live_module(file: Path, name: str):
    """Import a stdlib-only module from the live tree read-only (no .pyc written there)."""
    file = Path(file)
    if not file.is_file():
        raise NotConfigured(f"{file} not found")
    folder = str(file.parent)
    if folder not in sys.path:
        sys.path.append(folder)                    # appended: sibling imports resolve, nothing is shadowed
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location(f"_live_{name}", file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


class Emitter:
    """Glassbox wrapper (fec/surfaces/glassbox_emit.py). Emission can never break a release."""

    def __init__(self, cfg=None, fn=None, run_id: str = ""):
        self.cfg, self.fn, self.run_id = cfg or {}, fn, run_id

    def __call__(self, kind: str, status: str, summary: str, **kw):
        try:
            if self.fn is None:
                module = load_live_module(Path(self.cfg["melange_live"]) / "fec" / "surfaces" / "glassbox_emit.py", "glassbox")
                self.fn = module.emit
            self.fn(kind, status, summary, source="melange-release", lane="release", run_id=self.run_id, **kw)
        except Exception:
            pass


def noop_emit(*_a, **_k):
    return None


def step(emit, name: str, fn, *args, **kw):
    """Run one release step with start/finish events and timing. Exceptions propagate after being recorded."""
    started = time.time()
    emit("release.step", "started", name, stage=name)
    try:
        result = fn(*args, **kw)
    except Pending:
        emit("release.step", "blocked", f"{name}: pending confirmation", stage=name)
        raise
    except Exception as error:
        emit("release.step", "failed", f"{name}: {type(error).__name__}", stage=name, detail=str(error)[:600])
        raise
    emit("release.step", "completed", name, stage=name, duration_ms=int((time.time() - started) * 1000))
    return result


def free_bytes(path) -> int:
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free


def temp_dir(prefix="release-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))

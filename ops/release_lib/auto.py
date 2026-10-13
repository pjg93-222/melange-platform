"""Automatic propagation, the parts that decide and remember: what changed, whether it is quiet enough to act, how many
releases today, what failed on which heads, whether signing can happen, and the local nudges after a release.

The release steps themselves (export, push, CI, manifest, publish) stay in ops/release.py; this module holds no step.
State lives beside the release dirs: auto_state.json (what was propagated, what failed, release times), auto.pause
(a file: its presence stops `auto`), auto.lock (one `auto` at a time).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import core

STATE, PAUSE, LOCK = "auto_state.json", "auto.pause", "auto.lock"
DEFAULTS = {"quiet_minutes": 20, "max_per_day": 6, "mentat_restart": True, "mentat_port": 8802, "healthchecks_url": None}
DAY = 86400
GEO_PREFIX = "terrier-"                               # the images Geo's own workflow builds; the platform repo builds the rest


def settings(cfg) -> dict:
    return {**DEFAULTS, **(cfg.get("auto") or {})}


# ---------------------------------------------------------------- heads and the decision
def head_commits(cfg) -> dict:
    """The three commits a release is made from: Melange main, Mentat's branch, Geo's image branch."""
    return {"melange": core.git_text("rev-parse", f"{cfg.get('melange_ref', 'main')}^{{commit}}", cwd=cfg["melange_live"]),
            "mentat": core.git_text("rev-parse", f"{cfg['mentat_ref']}^{{commit}}", cwd=cfg["mentat_live"]),
            "terrier": core.git_text("rev-parse", f"refs/heads/{cfg['terrier_images_ref']}^{{commit}}", cwd=cfg["terrier_checkout"])}


def repo_of(cfg, name) -> str:
    return cfg[{"melange": "melange_live", "mentat": "mentat_live", "terrier": "terrier_checkout"}[name]]


def newest_change(cfg, heads: dict, propagated: dict) -> float | None:
    """Committer time of the newest head that differs from what was propagated (None when nothing differs)."""
    times = [int(core.git_text("log", "-1", "--format=%ct", heads[name], cwd=repo_of(cfg, name)))
             for name in heads if heads[name] != propagated.get(name)]
    return max(times, default=None)


def decide(heads, propagated, st, *, now, newest, quiet_s, cap, force_retry=False) -> tuple[str, str]:
    """(action, why). Actions: idle (nothing changed), failed (these heads already failed), wait (still being edited),
    capped (enough releases in the last 24 h), go."""
    if heads == propagated:
        return "idle", "no change since the last propagation"
    failed = st.get("failed")
    if failed and failed.get("heads") == heads and not force_retry:
        return "failed", (f"these heads already failed ({failed.get('code')}: {failed.get('message', '')[:120]}); "
                          "a new commit or --force-retry is needed")
    if newest is not None and now - newest < quiet_s:
        return "wait", f"newest change is {max(0, int((now - newest) // 60))} min old; waiting for {quiet_s // 60} quiet minutes"
    if len([t for t in st.get("releases", []) if now - t < DAY]) >= cap:
        return "capped", f"{cap} releases in the last 24 h; raising auto.max_per_day is the only way past this"
    return "go", "changed and quiet"


def record_start(st, now) -> None:
    st["releases"] = [t for t in st.get("releases", []) if now - t < DAY] + [now]


# ---------------------------------------------------------------- pause, lock, alerts
def paused(root) -> str | None:
    flag = Path(root) / PAUSE
    return flag.read_text(encoding="utf-8").strip() or "paused" if flag.is_file() else None


def set_pause(root, on: bool, now) -> None:
    flag = Path(root) / PAUSE
    if on:
        core.atomic_write(flag, f"paused at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}".encode())
    else:
        flag.unlink(missing_ok=True)


@contextmanager
def single_instance(path):
    """Yields True when this process now holds the only `auto` lock (a byte-range lock the OS drops on exit), else False."""
    import msvcrt
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def http_ping(url: str, timeout: int = 10) -> None:
    """Healthchecks-style GET. Never raises, never logs the URL (it carries the check's secret id)."""
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "melange-auto-release"}), timeout=timeout).close()
    except Exception:
        pass


def http_health(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            doc = json.loads(response.read().decode("utf-8", "replace"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def launch(argv) -> int:
    return subprocess.run(argv, capture_output=True, timeout=180).returncode


def ssh_agent_keys() -> str | None:
    """`ssh-add -L` output, or None when the agent cannot be reached (ssh-add missing, service stopped)."""
    try:
        result = core.run(["ssh-add", "-L"], check=False, timeout=20)
    except (core.ReleaseError, OSError, subprocess.SubprocessError):
        return None
    return result.stdout.decode("utf-8", "replace") if result.returncode in (0, 1) else None


@dataclass
class Hooks:
    """Everything `auto` does outside the repos, replaceable in tests."""
    ping: Callable = http_ping
    health: Callable = http_health
    launch: Callable = launch
    agent_keys: Callable = ssh_agent_keys


def signing_unavailable(cfg, hooks) -> str | None:
    """Why the manifest cannot be signed unattended right now, or None. Only the ssh-agent route can be unattended: the key
    must be loaded (`ssh-add`) in the Windows agent; a key file that needs a passphrase prompt is not usable here."""
    s = cfg["signing"]
    if not s.get("agent"):
        return None                                    # key file on disk: sign_manifest's own checks apply
    pub = Path(s["key"] + ".pub")
    if not pub.is_file():
        return f"signing public key {pub.name} not found; run `release.py init-signing`"
    blob = pub.read_text(encoding="utf-8").split()
    listing = hooks.agent_keys()
    if listing is None:
        return "ssh-agent is not reachable (start the OpenSSH Authentication Agent service)"
    if len(blob) < 2 or blob[1] not in listing:
        return f"the release signing key is not loaded in ssh-agent (ssh-add {s['key']})"
    return None


# ---------------------------------------------------------------- local consoles
def restart_idle_mentat(cfg, hooks, emit) -> dict:
    """Ask Mentat's own launcher to replace an IDLE, STALE console (D-029): never a busy one, never a read-only preview.
    The same rules the launcher applies are checked first here, because it would otherwise open a second console on another
    port beside a busy stale one. Returns {'restarted': [ports], 'deferred': [ports]}; deferred ones are retried next run."""
    live = Path(cfg["mentat_live"]) / "mentat"
    script = live / "app" / "Start-MentatApp.ps1"
    done = {"restarted": [], "deferred": []}
    base = int(settings(cfg)["mentat_port"])
    for port in range(base, base + 10):
        health = hooks.health(f"http://127.0.0.1:{port}/api/health")
        if not health or health.get("application") != "MENTAT" or os.path.normcase(str(health.get("project", ""))) != os.path.normcase(str(live)):
            continue
        stale = (health.get("code_sha256") != health.get("on_disk_code_sha256_now")
                 or health.get("assets_sha256") != health.get("on_disk_assets_sha256_now"))
        if not stale:
            continue
        if health.get("read_only") is not False or not health.get("data_root"):      # a preview is never ours to replace: not retried
            emit("release.restart", "blocked", f"Mentat console on {port} is running older code but is read-only: not restarted")
            continue
        if health.get("busy") is not False:                                           # busy (or unknown): the next run asks again
            done["deferred"].append(port)
            emit("release.restart", "blocked", f"Mentat console on {port} is running older code but is busy: not restarted")
            continue
        if not script.is_file():
            done["deferred"].append(port)
            emit("release.restart", "failed", f"Mentat launcher not found: {script}")
            continue
        shell = "powershell.exe"
        code = hooks.launch([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                             "-NoBrowser", "-Port", str(port), "-DataRoot", str(health["data_root"])])
        (done["restarted"] if code == 0 else done["deferred"]).append(port)
        emit("release.restart", "completed" if code == 0 else "failed", f"Mentat console on {port}: launcher exit {code}")
    return done

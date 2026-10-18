#!/usr/bin/env python3
"""Repin the live staffer dashboard sidecar to a newly verified generation.

This runner is a driver, not a decision maker.  It never edits, recomputes or
re-verifies the underlying graph data; it only checks that a named generation
already has a PASSing verification receipt on disk (mirroring exactly the
check ``StaticGraph._load`` performs in ``staffer_graph_snapshot.py``), repins
the two module-level constants that select the served generation, runs the
existing source integration tests, and restarts *only* the staffer-live
sidecar process recorded in ``audits/staffer_live_runtime/server.json`` --
and only after confirming that PID's own command line is that sidecar.  It
never kills a process it cannot identify, never starts a crawler, and never
overwrites anything without a byte-for-byte backup made first.

The default action is a dry run: it resolves every path, reports whether each
input exists right now, prints the exact commands it would run, and changes
nothing.  Pass ``--execute`` to actually repin.

Usage
-----
  python repin_staffer_live.py                                   # dry run (default)
  python repin_staffer_live.py --execute
  python repin_staffer_live.py --execute --wait --max-wait-hours 6
  python repin_staffer_live.py --generation staffer_social_network_20260907_v6_primary --execute

Standard library only.
"""
from __future__ import annotations

import argparse
import ctypes
import shutil
import datetime as _dt
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

# --------------------------------------------------------------------------- paths

DEFAULT_SOURCE_ROOT = Path(r"C:\Users\pgoff\OneDrive\Desktop\Melange Systems")
DEFAULT_GENERATION = "staffer_social_network_20260908_v8_math_primary"

SNAPSHOT_MODULE_RELATIVE = Path("fec") / "surfaces" / "staffer_graph_snapshot.py"
RUNTIME_RELATIVE = Path("audits") / "staffer_live_runtime"
SERVER_JSON_RELATIVE = RUNTIME_RELATIVE / "server.json"
START_SCRIPT_RELATIVE = Path("fec") / "surfaces" / "start_staffer_live.ps1"
STAFFER_NETWORK_RELATIVE = Path("audits") / "staffer_network"

GENERATION_RECEIPT_NAME = "staffer_social_network_receipt.json"
VERIFICATION_RECEIPT_NAME = "staffer_social_network_verification_receipt.json"

EMIT_PS1 = Path(r"C:\Users\pgoff\.claude\glassbox\emit.ps1")
GLASSBOX_LANE = "claude"
GLASSBOX_SOURCE = "repin-staffer-live"

# The exact 8-file "Checks" command from fec/surfaces/staffer_live/README.md.
CHECK_FILES = [
    "fec/tests/surfaces/test_staffer_live_server.py",
    "fec/surfaces/test_staffer_graph_snapshot.py",
    "fec/surfaces/test_staffer_live_service.py",
    "fec/surfaces/test_staffer_routing.py",
    "fec/surfaces/test_staffer_activity_index.py",
    "fec/tests/surfaces/test_staffer_routing_evidence.py",
    "fec/tests/surfaces/test_staffer_person_inventory.py",
    "fec/tests/surfaces/test_staffer_identity_resolution.py",
]

SERVER_PROCESS_MARKER = "staffer_live_server.py"

GENERATION_PATTERN = re.compile(r'^GENERATION = "[^"]*"(?=\r?$)', re.MULTILINE)
VERIFICATION_PATTERN = re.compile(r'^VERIFICATION = "[^"]*"(?=\r?$)', re.MULTILINE)


class RepinHalt(Exception):
    """Any gate failure.  Carries the process exit code and a JSON-able detail blob."""

    def __init__(self, reason: str, detail: dict[str, Any] | None = None, exit_code: int = 1) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}
        self.exit_code = exit_code


# --------------------------------------------------------------------------- small utils


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def utc_stamp(moment: _dt.datetime | None = None) -> str:
    return (moment or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def iso_utc(moment: _dt.datetime | None = None) -> str:
    return (moment or utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write via a sibling temp file and os.replace (crash-safe, no partial writes)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
    with tmp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def derive_verification(generation: str) -> str:
    """Mirror the project's naming convention: '..._primary' -> '..._verification'."""
    match = re.search(r"_primary$", generation)
    if match:
        return generation[: match.start()] + "_verification"
    return generation + "_verification"


# --------------------------------------------------------------------------- Gate 1: receipts


def try_load_passing_receipt(path: Path) -> dict[str, Any] | None:
    """Return the parsed receipt if it exists and passes, else None.

    This is the exact predicate ``StaticGraph._load`` uses in
    staffer_graph_snapshot.py: ``status`` must equal "PASS" and ``failures``
    must be falsy.  Never loosen this check.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") != "PASS" or data.get("failures"):
        return None
    return data


def gate_receipts(source_root: Path, generation: str, verification: str, *, wait: bool,
                   max_wait_hours: float, poll_interval_s: float = 60.0,
                   log: Callable[[str], None] = print) -> tuple[Path, dict[str, Any], Path]:
    """Gate 1.  Raises RepinHalt if the receipts are not present/passing."""
    verification_path = source_root / STAFFER_NETWORK_RELATIVE / verification / VERIFICATION_RECEIPT_NAME
    generation_receipt_path = source_root / STAFFER_NETWORK_RELATIVE / generation / GENERATION_RECEIPT_NAME

    deadline = time.time() + max_wait_hours * 3600.0
    data = try_load_passing_receipt(verification_path)
    while data is None:
        if not wait or time.time() >= deadline:
            raise RepinHalt(
                f"verification receipt is not present and PASSing: {verification_path}",
                {"verification_path": str(verification_path), "waited": wait,
                 "max_wait_hours": max_wait_hours},
            )
        log(f"waiting for a PASSing verification receipt at {verification_path} "
            f"(retry in {poll_interval_s:.0f}s)")
        time.sleep(poll_interval_s)
        data = try_load_passing_receipt(verification_path)

    if not generation_receipt_path.exists():
        raise RepinHalt(
            f"generation receipt missing: {generation_receipt_path}",
            {"generation_receipt_path": str(generation_receipt_path)},
        )
    checks = data.get("twin_byte_identity")
    matches = [entry for entry in checks if isinstance(entry, dict)
               and entry.get("path") == GENERATION_RECEIPT_NAME] if isinstance(checks, list) else []
    if len(matches) != 1:
        raise RepinHalt("verification must bind exactly one generation receipt",
                        {"verification_path": str(verification_path)})
    check = matches[0]
    expected = check.get("primary_sha256")
    if (not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None
            or check.get("identical") is not True or check.get("twin_sha256") != expected):
        raise RepinHalt("generation receipt lacks matching primary/twin verification hashes")
    actual = sha256_file(generation_receipt_path)
    if actual != expected:
        raise RepinHalt("generation receipt hash differs from its verification",
                        {"path": str(generation_receipt_path), "expected": expected, "actual": actual})
    return verification_path, data, generation_receipt_path


# --------------------------------------------------------------------------- Step 2: repin


def repin_constants(snapshot_path: Path, generation: str, verification: str,
                     backup_path: Path) -> dict[str, str]:
    """Back up snapshot_path, then rewrite only the GENERATION/VERIFICATION lines.

    Refuses (raises RepinHalt, writing nothing) unless each constant appears on
    exactly one line, matched by a regex anchored at line start/end.
    """
    original_bytes = snapshot_path.read_bytes()
    original_text = original_bytes.decode("utf-8")
    gen_matches = GENERATION_PATTERN.findall(original_text)
    ver_matches = VERIFICATION_PATTERN.findall(original_text)
    if len(gen_matches) != 1:
        raise RepinHalt(f"expected exactly one GENERATION constant line in {snapshot_path}, "
                         f"found {len(gen_matches)}", {"path": str(snapshot_path)})
    if len(ver_matches) != 1:
        raise RepinHalt(f"expected exactly one VERIFICATION constant line in {snapshot_path}, "
                         f"found {len(ver_matches)}", {"path": str(snapshot_path)})

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(original_bytes)

    new_gen_line = f'GENERATION = "{generation}"'
    new_ver_line = f'VERIFICATION = "{verification}"'
    # Replacement via a function (not a raw string) sidesteps re.sub's backslash handling.
    new_text = GENERATION_PATTERN.sub(lambda _m: new_gen_line, original_text, count=1)
    new_text = VERIFICATION_PATTERN.sub(lambda _m: new_ver_line, new_text, count=1)
    atomic_write_text(snapshot_path, new_text)

    return {
        "generation_before": gen_matches[0], "generation_after": new_gen_line,
        "verification_before": ver_matches[0], "verification_after": new_ver_line,
    }


def restore_backup(snapshot_path: Path, backup_path: Path) -> None:
    atomic_write_bytes(snapshot_path, backup_path.read_bytes())


# --------------------------------------------------------------------------- Step 3: tests


def run_checks(source_root: Path, python: str | None = None) -> tuple[bool, str, str]:
    """Run the README "Checks" pytest command.  Returns (ok, tail_line, full_output)."""
    python = python or sys.executable
    cmd = [python, "-m", "pytest", *CHECK_FILES, "-q", "-p", "no:cacheprovider"]
    env = dict(os.environ)
    env["STAFFER_GRAPH_TEST_SOURCE"] = str(source_root)
    try:
        proc = subprocess.run(cmd, cwd=str(source_root), env=env, capture_output=True,
                              text=True, timeout=3600)
    except subprocess.TimeoutExpired as exc:
        raise RepinHalt('source integration checks timed out', {'timeout_seconds': 3600}, exit_code=2) from exc
    except OSError as exc:
        raise RepinHalt('source integration checks could not start', exit_code=2) from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    lines = [line for line in output.splitlines() if line.strip()]
    tail = lines[-1] if lines else ""
    return proc.returncode == 0, tail, output


# --------------------------------------------------------------------------- Step 4: sidecar restart


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def read_server_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        # start_staffer_live.ps1 writes this via `Set-Content -Encoding utf8`, which
        # emits a BOM; utf-8-sig strips it (same convention as staffer_live_server.py).
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_pid(server_json_path: Path) -> int | None:
    data = read_server_json(server_json_path)
    if not data or data.get("pid") is None:
        return None
    try:
        return int(data["pid"])
    except (TypeError, ValueError):
        return None


def lookup_command_line(pid: int) -> str | None:
    """Read-only: the full command line of a PID via Get-CimInstance, or None.

    Mirrors the pattern in audits/staffer_network/finish_staffer_social_network_chain.py's
    query_processes(): a read-only WMI query, never os.kill(pid, 0) (which is
    TerminateProcess on Windows).
    """
    script = (f"Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" | "
              "Select-Object -ExpandProperty CommandLine")
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                               capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    return out or None


def lookup_process_identity(pid: int) -> dict[str, Any] | None:
    """Read actual executable, creation identity and argv together; never print argv."""
    if type(pid) is not int or pid <= 0:
        return None
    script = (f'$p=Get-CimInstance Win32_Process -Filter "ProcessId={pid}"; '
              'if($p){[pscustomobject]@{pid=[int]$p.ProcessId;executable=[string]$p.ExecutablePath;'
              'command_line=[string]$p.CommandLine;created_at=$p.CreationDate.ToUniversalTime().ToString("o")} | ConvertTo-Json -Compress}')
    try:
        proc = subprocess.run(['powershell', '-NoProfile', '-Command', script],
                              capture_output=True, text=True, timeout=30)
        value = json.loads(proc.stdout or '') if proc.returncode == 0 else None
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _windows_command_argv(command_line):
    if (os.name != 'nt' or not isinstance(command_line, str)
            or not 0 < len(command_line) <= 32768 or '\x00' in command_line):
        raise ValueError('Process command line is unavailable or unsupported')
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not pointer:
        raise ValueError('Process command line cannot be parsed')
    try:
        if not 0 < count.value <= 32:
            raise ValueError('Process argument count is unsupported')
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))


def confirm_pid_is_sidecar(pid: int, lookup=lookup_process_identity, *,
                            source_root=None, port=None, expected_python=None) -> str:
    """Require this launcher's executable, exact script/root/port and unchanged identity.

    Re-reading CreationDate rejects a detected PID reuse. It cannot close the
    later PID/taskkill race or prove descendant-tree ownership; no such claim.
    """
    def refuse():
        raise RepinHalt('Recorded PID does not match the exact owned sidecar launch; refusing process control',
                        {'pid': pid}, exit_code=3)
    if type(pid) is not int or pid <= 0 or source_root is None or type(port) is not int or not 1 <= port <= 65535:
        refuse()
    identity = lookup(pid)
    if (not isinstance(identity, dict) or set(identity) != {'pid', 'executable', 'command_line', 'created_at'}
            or type(identity.get('pid')) is not int or identity['pid'] != pid):
        refuse()
    try:
        def canonical(value):
            candidate = Path(value)
            if not candidate.is_absolute():
                raise ValueError('Process paths must be absolute')
            return os.path.normcase(str(candidate.resolve(strict=True)))
        python = expected_python or shutil.which('python')
        root = canonical(source_root)
        executable = canonical(python)
        script = canonical(Path(source_root) / 'fec/surfaces/staffer_live_server.py')
        created = _dt.datetime.fromisoformat(identity['created_at'])
        argv = _windows_command_argv(identity['command_line'])
        valid = (created.tzinfo is not None and len(argv) == 8
                 and canonical(identity['executable']) == executable and canonical(argv[0]) == executable
                 and canonical(argv[1]) == script
                 and argv[2] == '--source-root' and canonical(argv[3]) == root
                 and argv[4] == '--port' and argv[5] == str(port)
                 and argv[6] == '--dashboard-file' and argv[7] in ('Moneyball.html', 'Moneyball_Redesign.html'))
    except (OSError, TypeError, ValueError, AttributeError):
        refuse()
    if not valid or lookup(pid) != identity:
        refuse()
    return identity['command_line']


def kill_pid(pid: int) -> subprocess.CompletedProcess:
    return subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True, timeout=30, check=False)


def wait_port_free(port: int, timeout_s: float = 60.0, poll_s: float = 1.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not port_in_use(port):
            return True
        time.sleep(poll_s)
    return not port_in_use(port)


def start_sidecar(source_root: Path, port: int) -> subprocess.CompletedProcess:
    script = source_root / START_SCRIPT_RELATIVE
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
           "-SourceRoot", str(source_root), "-Port", str(port)]
    # A detached Windows child can inherit a pipe handle after PowerShell exits.
    # File-backed output lets the launcher finish without waiting for server EOF.
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as stdout, tempfile.TemporaryFile(mode='w+', encoding='utf-8') as stderr:
        try:
            result = subprocess.run(cmd, stdout=stdout, stderr=stderr, text=True,
                                    timeout=180, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RepinHalt('sidecar launcher timed out', {'port': port, 'timeout_seconds': 180}, exit_code=3) from exc
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(cmd, result.returncode, stdout.read(), stderr.read())


def read_new_pid(server_json_path: Path, timeout_s: float = 30.0, poll_s: float = 1.0) -> int | None:
    deadline = time.time() + timeout_s
    pid = read_pid(server_json_path)
    while pid is None and time.time() < deadline:
        time.sleep(poll_s)
        pid = read_pid(server_json_path)
    return pid


def restart_sidecar(source_root: Path, port: int, *,
                     lookup: Callable[[int], dict[str, Any] | None] = lookup_process_identity) -> tuple[int | None, int | None]:
    """Step 4.  Returns (old_pid, new_pid).  Raises RepinHalt (exit_code=3) on a foreign PID."""
    server_json_path = source_root / SERVER_JSON_RELATIVE
    old_pid = read_pid(server_json_path)
    listening = port_in_use(port)

    if not listening:
        # "If no listener is present at all, just start."  A stale PID in server.json
        # (if any) is not touched.
        pass
    elif old_pid is None:
        raise RepinHalt(f"port {port} has a listener but no PID is recorded in {server_json_path}; "
                         "refusing to touch an unidentified process", {"port": port}, exit_code=3)
    else:
        confirm_pid_is_sidecar(old_pid, lookup=lookup, source_root=source_root, port=port)
        kill_pid(old_pid)
        if not wait_port_free(port):
            raise RepinHalt(f"port {port} did not free up after stopping PID {old_pid}",
                             {"pid": old_pid, "port": port}, exit_code=3)

    result = start_sidecar(source_root, port)
    if result.returncode != 0:
        raise RepinHalt(f"start_staffer_live.ps1 exited {result.returncode}",
                         {"stdout": result.stdout, "stderr": result.stderr}, exit_code=3)
    new_pid = read_new_pid(server_json_path)
    return old_pid, new_pid


# --------------------------------------------------------------------------- Step 5: verify live


def http_get_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback only
        return json.loads(response.read().decode("utf-8"))


def wait_for_live_ready(port: int, timeout_s: float = 600.0, poll_s: float = 5.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}/api/staffer-live?limit=5"
    deadline = time.time() + timeout_s
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        try:
            last = http_get_json(url)
        except (urllib.error.URLError, OSError, ValueError):
            last = None
        if last and last.get("ready"):
            return last
        time.sleep(poll_s)
    raise RepinHalt("live endpoint did not report ready==true within the wait window",
                     {"url": url, "last_response": last}, exit_code=4)


def verify_live_pin(port: int, generation: str, timeout_s: float = 600.0) -> dict[str, Any]:
    """Step 5.  Raises RepinHalt (exit_code=4) if the live pin cannot be confirmed."""
    live = wait_for_live_ready(port, timeout_s=timeout_s)
    if live.get("errors"):
        raise RepinHalt("live endpoint reports errors", {"errors": live["errors"]}, exit_code=4)
    if live.get("verification_status") != "PASS":
        raise RepinHalt(f"live verification_status is not PASS: {live.get('verification_status')!r}",
                         {"live": live}, exit_code=4)
    sources = live.get("sources") or []
    matching = [s for s in sources if isinstance(s, dict)
                and s.get("role") == "graph_receipt" and s.get("verified") is True
                and str(s.get("path", "")).replace("\\", "/").split("/")[-2:]
                == [generation, GENERATION_RECEIPT_NAME]]
    if not matching:
        raise RepinHalt(f"no verified source entry in the live response references {generation}",
                         {"sources": sources}, exit_code=4)
    return live


# --------------------------------------------------------------------------- glassbox


class Glassbox:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.enabled = EMIT_PS1.exists()

    def emit(self, kind: str, status: str, summary: str) -> None:
        if not self.enabled:
            return
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(EMIT_PS1),
                "-Lane", GLASSBOX_LANE, "-Kind", kind, "-Status", status,
                "-Summary", summary[:900], "-Source", GLASSBOX_SOURCE, "-RunId", self.run_id,
                "-Quiet"]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
            if result.returncode != 0:
                print(f"WARNING: glassbox emit ({kind}) exited {result.returncode}: "
                      f"{(result.stderr or '').strip()[:300]}")
        except Exception as exc:  # glassbox is never fatal
            print(f"WARNING: glassbox emit ({kind}) raised {exc!r}")


# --------------------------------------------------------------------------- halt/receipt files


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, default=str))
    return path


def write_halt(source_root: Path, run_id: str, reason: str, detail: dict[str, Any]) -> Path:
    path = source_root / RUNTIME_RELATIVE / f"repin_halt_{run_id}.json"
    return write_json(path, {"run_id": run_id, "reason": reason, "detail": detail,
                              "halted_at": iso_utc()})


def write_receipt(source_root: Path, run_id: str, payload: dict[str, Any]) -> Path:
    path = source_root / RUNTIME_RELATIVE / f"repin_{run_id}.json"
    return write_json(path, payload)


# --------------------------------------------------------------------------- dry run


def plan(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root)
    snapshot_path = source_root / SNAPSHOT_MODULE_RELATIVE
    verification_path = source_root / STAFFER_NETWORK_RELATIVE / args.verification / VERIFICATION_RECEIPT_NAME
    generation_receipt_path = source_root / STAFFER_NETWORK_RELATIVE / args.generation / GENERATION_RECEIPT_NAME
    server_json_path = source_root / SERVER_JSON_RELATIVE
    start_script = source_root / START_SCRIPT_RELATIVE

    print("STAFFER LIVE REPIN - DRY RUN (NOTHING EXECUTED)")
    print(f"source-root:          {source_root}  exists={source_root.exists()}")
    print(f"generation:           {args.generation}")
    print(f"verification:         {args.verification}")
    print(f"port:                 {args.port}")
    print(f"wait:                 {args.wait}  (max-wait-hours={args.max_wait_hours})")
    print(f"skip-tests:           {args.skip_tests}")
    print()
    print(f"snapshot module:      {snapshot_path}  exists={snapshot_path.exists()}")
    print(f"verification receipt: {verification_path}  exists={verification_path.exists()}")
    print(f"generation receipt:   {generation_receipt_path}  exists={generation_receipt_path.exists()}")
    print(f"server.json:          {server_json_path}  exists={server_json_path.exists()}")
    print(f"start script:         {start_script}  exists={start_script.exists()}")

    receipt_check = try_load_passing_receipt(verification_path)
    print(f"verification receipt currently PASSing: {receipt_check is not None}")

    if snapshot_path.exists():
        text = snapshot_path.read_text(encoding="utf-8")
        gen_line = next((line for line in text.splitlines() if line.startswith("GENERATION = ")), None)
        ver_line = next((line for line in text.splitlines() if line.startswith("VERIFICATION = ")), None)
        print()
        print(f"current GENERATION line:   {gen_line}")
        print(f"current VERIFICATION line: {ver_line}")

    old_pid = read_pid(server_json_path)
    print()
    print(f"server.json PID:      {old_pid}")
    print(f"port {args.port} currently listening: {port_in_use(args.port)}")

    print()
    print("Would run (Gate 1): read and parse the two receipts above, requiring status==PASS")
    print("  and no failures; bind the generation receipt to matching primary/twin SHA-256 hashes.")

    print()
    print("Would back up the snapshot module to:")
    print(f"  {source_root / RUNTIME_RELATIVE / 'backups' / 'fec' / 'surfaces'}"
          f"{os.sep}staffer_graph_snapshot.py.<UTCstamp>.bak")
    print("Would then rewrite only these two lines in place:")
    print(f'  GENERATION = "{args.generation}"')
    print(f'  VERIFICATION = "{args.verification}"')

    if not args.skip_tests:
        checks_cmd = [sys.executable, "-m", "pytest", *CHECK_FILES, "-q", "-p", "no:cacheprovider"]
        print()
        print("Would run (Step 3):")
        print("  " + " ".join(checks_cmd))
        print(f"  (cwd={source_root}, env STAFFER_GRAPH_TEST_SOURCE={source_root})")
    else:
        print()
        print("Would skip Step 3 tests (--skip-tests).")

    print()
    print("Would confirm the recorded PID's actual executable, exact script/root/port argv "
          "and unchanged creation identity via Get-CimInstance, then run:")
    print(f"  taskkill /PID <pid> /T /F")
    print("  and wait for the port to free, then run:")
    print(f'  powershell -NoProfile -ExecutionPolicy Bypass -File "{start_script}" '
          f'-SourceRoot "{source_root}" -Port {args.port}')

    print()
    print(f"Would poll GET http://127.0.0.1:{args.port}/api/staffer-live?limit=5 "
          "every 5s up to 10 minutes until ready==true, then check verification_status=='PASS' "
          f"and a verified source entry containing {args.generation!r}.")

    print()
    print("Nothing above was executed.")
    return 0


# --------------------------------------------------------------------------- execute


def execute(args: argparse.Namespace) -> int:
    run_id = utc_stamp()
    glassbox = Glassbox(run_id)
    glassbox.emit("repin-start", "running",
                  f"repin_staffer_live starting: generation={args.generation}")

    source_root = Path(args.source_root)
    snapshot_path = source_root / SNAPSHOT_MODULE_RELATIVE
    backup_path = (source_root / RUNTIME_RELATIVE / "backups" / "fec" / "surfaces"
                   / f"staffer_graph_snapshot.py.{run_id}.bak")

    receipt: dict[str, Any] = {
        "run_id": run_id, "started_at": iso_utc(), "generation": args.generation,
        "verification": args.verification, "source_root": str(source_root), "port": args.port,
    }
    rewritten = False

    try:
        # ---- Gate 1
        verification_path, _verification_data, generation_receipt_path = gate_receipts(
            source_root, args.generation, args.verification,
            wait=args.wait, max_wait_hours=args.max_wait_hours)
        receipt["verification_receipt_sha256"] = sha256_file(verification_path)
        receipt["generation_receipt_sha256"] = sha256_file(generation_receipt_path)
        print(f"Gate 1 PASS: {verification_path} sha256={receipt['verification_receipt_sha256']}")
        print(f"Gate 1 PASS: {generation_receipt_path} sha256={receipt['generation_receipt_sha256']}")

        # ---- Step 2: repin
        rewrite_info = repin_constants(snapshot_path, args.generation, args.verification, backup_path)
        rewritten = True
        receipt.update(rewrite_info)
        receipt["backup_path"] = str(backup_path)
        print(f"before: {rewrite_info['generation_before']}")
        print(f"before: {rewrite_info['verification_before']}")
        print(f"after:  {rewrite_info['generation_after']}")
        print(f"after:  {rewrite_info['verification_after']}")

        # ---- Step 3: tests
        if args.skip_tests:
            receipt["test_tail"] = "SKIPPED (--skip-tests)"
            print("Step 3 skipped (--skip-tests)")
        else:
            ok, tail, _full_output = run_checks(source_root)
            receipt["test_tail"] = tail
            print(tail)
            if not ok:
                restore_backup(snapshot_path, backup_path)
                receipt["restored"] = True
                write_halt(source_root, run_id, "source integration tests failed after repin",
                           {"tail": tail})
                glassbox.emit("repin-halt", "failed",
                               "source integration tests failed; constants restored from backup")
                print("HALT: source integration tests failed; constants restored from backup")
                return 2

        # ---- Step 4: restart sidecar
        old_pid, new_pid = restart_sidecar(source_root, args.port)
        receipt["old_pid"] = old_pid
        receipt["new_pid"] = new_pid
        print(f"restarted sidecar: old_pid={old_pid} new_pid={new_pid}")

        # ---- Step 5: verify live
        try:
            live = verify_live_pin(args.port, args.generation)
        except RepinHalt as live_halt:
            rollback_pid = _rollback(source_root, snapshot_path, backup_path, args, new_pid)
            write_halt(source_root, run_id, live_halt.reason,
                       {**live_halt.detail, "old_pid": old_pid, "new_pid_with_bad_pin": new_pid,
                        "rollback_pid": rollback_pid})
            glassbox.emit("repin-halt", "failed",
                           f"live verification failed after restart; rolled back: {live_halt.reason}"[:900])
            print(f"HALT: {live_halt.reason}")
            print(f"old_pid={old_pid} new_pid_with_bad_pin={new_pid} rollback_pid={rollback_pid}")
            return live_halt.exit_code

        sources = live.get("sources") or []
        graph_receipt_source = next((s for s in sources if isinstance(s, dict)
                                      and s.get("role") == "graph_receipt"), None)
        receipt["live_revision"] = live.get("revision")
        receipt["live_totals"] = live.get("totals")
        receipt["live_verification_status"] = live.get("verification_status")
        receipt["graph_receipt_source"] = graph_receipt_source
        receipt["completed_at"] = iso_utc()

        print(f"revision={live.get('revision')} totals={live.get('totals')}")
        if graph_receipt_source:
            print(f"graph_receipt source path={graph_receipt_source.get('path')} "
                  f"sha256={graph_receipt_source.get('sha256')}")

        write_receipt(source_root, run_id, receipt)
        glassbox.emit("repin-done", "completed", f"repinned staffer-live to {args.generation}")
        print("REPIN COMPLETE")
        return 0

    except RepinHalt as halt:
        if rewritten:
            restore_backup(snapshot_path, backup_path)
            receipt["restored"] = True
        write_halt(source_root, run_id, halt.reason, halt.detail)
        glassbox.emit("repin-halt", "failed", halt.reason[:900])
        print(f"HALT: {halt.reason}")
        return halt.exit_code


def _rollback(source_root: Path, snapshot_path: Path, backup_path: Path,
               args: argparse.Namespace, bad_pid: int | None) -> int | None:
    """Restore the old constants and restart the sidecar again with the old pin.

    Never blindly kills: re-confirms the running PID's command line first, exactly
    like the forward path.  A failure here is reported but does not raise -- the
    caller still halts either way.
    """
    restore_backup(snapshot_path, backup_path)
    server_json_path = source_root / SERVER_JSON_RELATIVE
    if port_in_use(args.port):
        current_pid = read_pid(server_json_path) or bad_pid
        if current_pid is not None:
            try:
                confirm_pid_is_sidecar(current_pid, source_root=source_root, port=args.port)
                kill_pid(current_pid)
                wait_port_free(args.port)
            except RepinHalt as exc:
                print(f"WARNING: rollback could not confirm/stop PID {current_pid}: {exc.reason}")
    try:
        start_sidecar(source_root, args.port)
    except Exception as exc:
        print(f"WARNING: rollback restart raised {exc!r}")
        return None
    return read_new_pid(server_json_path)


# --------------------------------------------------------------------------- argparse/main


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Repin the live staffer dashboard sidecar to a newly verified generation.")
    ap.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT),
                     help="original checkout root (default: the live tree)")
    ap.add_argument("--generation", default=DEFAULT_GENERATION,
                     help="new GENERATION value to pin")
    ap.add_argument("--verification", default=None,
                     help="new VERIFICATION value (default: --generation with "
                          "'_primary' -> '_verification')")
    ap.add_argument("--wait", action="store_true",
                     help="poll every 60s until the verification receipt exists and PASSes")
    ap.add_argument("--execute", action="store_true",
                     help="actually repin (default is a dry run that changes nothing)")
    ap.add_argument("--port", type=int, default=8841, help="staffer-live sidecar port")
    ap.add_argument("--skip-tests", action="store_true",
                     help="skip the Step 3 source integration tests")
    ap.add_argument("--max-wait-hours", type=float, default=12.0,
                     help="give up waiting for the verification receipt after this long")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    if args.verification is None:
        args.verification = derive_verification(args.generation)
    if not args.execute:
        return plan(args)
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())

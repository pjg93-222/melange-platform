"""Fail-closed pre-release gates. Each returns {"ok": bool, "detail": str}; a gate that cannot decide fails.

Nothing here writes to a live tree and no lock is ever kept: probes take a lock only to release it.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from .core import invited_dir, load_live_module, sha256_file

DISK_HEADROOM = 10 * 1024 ** 3
COMPLETE = ("COMPLETE", "COMPLETE_WITH_KNOWN_GATED_FAILURES")      # the same pair collector_supervisor accepts
KERNEL_LINE = re.compile(r"KERNEL TESTS\W+(\d+) passed, (\d+) FAILED")


def _g(ok: bool, detail: str, **extra) -> dict:
    return {"ok": bool(ok), "detail": detail, **extra}


def pipeline_lock(live: Path) -> dict:
    """.pipeline_run.lock is held iff its writer is alive (fec/pipeline_lock.py's own liveness rule)."""
    path = live / ".pipeline_run.lock"
    if not path.exists():
        return _g(True, "no lock file")
    module = load_live_module(live / "fec" / "pipeline_lock.py", "pipeline_lock")
    payload = module._read_lock(path)
    held = not module._stale_lock_may_be_removed(path, payload)
    return _g(not held, f"held by pid {payload.get('pid', '?')} ({payload.get('owner_kind', '?')})" if held
              else "lock file is stale (dead or recycled pid)")


def weekly_lock(live: Path) -> dict:
    """.weekly_refresh.lock is held iff a non-blocking byte-range lock fails. The probe never keeps it."""
    path = live / ".weekly_refresh.lock"
    if not path.exists():
        return _g(True, "no lock file")
    import msvcrt
    try:
        handle = open(path, "r+b")
    except OSError as error:
        return _g(False, f"cannot open lock file: {error.__class__.__name__}")
    try:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return _g(False, "weekly refresh holds the lock")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return _g(True, "free")
    finally:
        handle.close()


def mentat_lane_writers(roots) -> dict:
    """Read-only mirror of api_company_cycle_fanout.assert_no_active_lane_writers (plus the supervisor lock).
    `roots` are glob patterns naming fanout run directories."""
    probes = []
    for pattern in roots:
        for run_dir in sorted(map(Path, glob.glob(str(pattern)))):
            probes += [run_dir / ".supervisor-lock.sqlite", *sorted((run_dir / "lanes").glob("lane-*/mentat.db.writer-lock.sqlite"))]
    busy = []
    for probe in probes:
        label = probe.parent.parent.parent.name + "/" + probe.parent.name if probe.parent.parent.name == "lanes" else probe.parent.name
        if not probe.is_file():
            continue
        connection = sqlite3.connect(probe, timeout=0, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower():
                busy.append(label)
            else:
                return _g(False, f"cannot probe {probe.name}: {error}")
        finally:
            connection.close()
    return _g(not busy, f"active writers in {busy}" if busy else f"{len(probes)} lock file(s) free")


def _status_lines(repo: Path, paths, excludes=()) -> list[str]:
    argv = ["git", "status", "--porcelain", "--untracked-files=normal", "--", *paths, *[f":(exclude){e}" for e in excludes]]
    out = subprocess.run(argv, cwd=repo, capture_output=True)
    if out.returncode:
        raise RuntimeError("git status failed")
    return out.stdout.decode("utf-8", "replace").splitlines()


def _head(repo: Path, ref: str) -> tuple[str, str]:
    def rp(spec):
        return subprocess.run(["git", "rev-parse", "--verify", "-q", spec + "^{commit}"], cwd=repo,
                              capture_output=True).stdout.decode().strip()
    return rp("HEAD"), rp(ref)


def repos_clean(cfg) -> dict:
    """Allowlisted paths clean in all three repos and HEAD == the released ref (DE-031)."""
    checks = [("melange", cfg["melange_live"], cfg.get("melange_ref", "main"), ["fec", "requirements.txt", "ops"],
               ["fec/_quarantine_*", "fec/attic", "fec/research", "fec/pas224.zip"]),
              ("mentat", cfg["mentat_live"], cfg["mentat_ref"], ["mentat"],
               ["mentat/inputs", "mentat/state", "mentat/audits", "mentat/.cache"]),
              ("terrier", cfg["terrier_checkout"], cfg["terrier_ref"], ["."], [".local"])]
    problems = []
    for name, repo, ref, paths, excludes in checks:
        repo = Path(repo)
        try:
            dirty = _status_lines(repo, paths, excludes)
            head, want = _head(repo, ref)
        except (OSError, RuntimeError) as error:
            problems.append(f"{name}: unreadable ({error})")
            continue
        if dirty:
            problems.append(f"{name}: {len(dirty)} dirty path(s), e.g. {dirty[0][3:].strip()}")
        if not head or head != want:
            problems.append(f"{name}: HEAD {head[:9] or '?'} is not {ref} ({want[:9] or '?'})")
    return _g(not problems, "; ".join(problems) or "clean, HEAD == released ref in all three repos")


def kernel_tests(live: Path, skip: bool = False, timeout: int = 5400) -> dict:
    if skip:
        return _g(True, "skipped by --skip-kernel", skipped=True)
    runner = live / "fec" / "tests" / "run_tests.py"
    if not runner.is_file():
        return _g(False, "fec/tests/run_tests.py not found")
    try:
        result = subprocess.run([sys.executable, str(runner)], cwd=live, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _g(False, f"kernel tests exceeded {timeout}s")
    last = None
    for last in KERNEL_LINE.finditer(result.stdout.decode("utf-8", "replace")):
        pass
    if last is None:
        return _g(False, "no 'KERNEL TESTS - N passed, M FAILED' line in the output")
    passed, failed = int(last.group(1)), int(last.group(2))
    return _g(result.returncode == 0 and failed == 0 and passed > 0, f"{passed} passed, {failed} FAILED, exit {result.returncode}")


def weekly_outcome(live: Path) -> dict:
    status = live / "data" / "engine" / "weekly_refresh_status.json"
    try:
        doc = json.loads(status.read_text(encoding="utf-8"))
        outcome, failures = doc.get("last_outcome"), [str(f) for f in doc.get("failures") or []]
    except (OSError, ValueError, AttributeError, TypeError):
        return _g(False, "weekly_refresh_status.json is missing or unreadable")
    # `failures` is the audit checks the weekly refresh tolerated on a COMPLETE_WITH_KNOWN_GATED_FAILURES run, or the failing step
    # on a hard failure; a release must show what it shipped on top of.
    shown = ", ".join(failures[:12]) + (f" (+{len(failures) - 12} more)" if len(failures) > 12 else "")
    label = "tolerated" if outcome in COMPLETE else "failures"
    return _g(outcome in COMPLETE, f"last_outcome {outcome}" + (f"; {label} ({len(failures)}): {shown}" if failures else ""),
              **({"tolerated": failures} if outcome in COMPLETE else {}))


def receipt_matches_deploy(live: Path) -> dict:
    """The deployment receipt's recorded hash equals deploy/index.html (only needed when Netlify is released)."""
    receipt, index = live / "data" / "engine" / "dashboard_deployment_receipt.json", live / "deploy" / "index.html"
    try:
        recorded = json.loads(receipt.read_text(encoding="utf-8"))["local_deploy_artifact"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return _g(False, "dashboard_deployment_receipt.json is missing or malformed")
    if not index.is_file():
        return _g(False, "deploy/index.html is missing")
    return _g(sha256_file(index) == recorded, "receipt hash equals deploy/index.html" if sha256_file(index) == recorded
              else "receipt hash differs from deploy/index.html")


def invited_build(cfg, override: bool = False) -> dict:
    """D-164/D-169/R3: what ships must be a ruled, untampered, current invited build. INVITED_BUILD.json must say
    governance "ruled" (override: Paul-only testing, recorded as governance_override), every shipped Moneyball.html /
    lobby-*.html (and .gz) must hash to what it records, and its inputs must still be the live pages (freshness)."""
    live, folder = Path(cfg["melange_live"]), invited_dir(cfg)
    try:
        doc = json.loads((folder / "INVITED_BUILD.json").read_text(encoding="utf-8"))
        outputs, inputs, profile = doc["outputs"], doc["inputs"], doc["profile"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return _g(False, "outputs/invited_build/INVITED_BUILD.json is missing or malformed (run fec/surfaces/make_invited_build.py)")
    governance, problems = doc.get("governance"), []
    if governance != "ruled" and not override:
        problems.append(f"governance is {governance!r}, not 'ruled' (--governance-override is for Paul-only testing)")
    if "Moneyball.html" not in outputs or "Moneyball.html" not in inputs:
        problems.append("INVITED_BUILD.json does not describe Moneyball.html")
    shipped = {n for n in outputs} | {n + ".gz" for n in outputs}
    for name, row in outputs.items():
        for file, key in ((name, "sha256"), (name + ".gz", "gz_sha256")):
            path = folder / file
            if not path.is_file() or sha256_file(path) != row.get(key):
                problems.append(f"{file} differs from INVITED_BUILD.json (edited or rebuilt since)")
    problems += [f"{p.name} is in outputs/invited_build but not in INVITED_BUILD.json"
                 for p in sorted(folder.iterdir()) if p.name.startswith(("Moneyball.html", "lobby-")) and p.name not in shipped]
    for name, row in inputs.items():
        path = live / name
        if not path.is_file() or sha256_file(path) != row.get("sha256"):
            problems.append(f"{name} changed since the invited build was made (rebuild it)")
    extra = {"governance_override": True} if override and governance != "ruled" else {}
    return _g(not problems, "; ".join(problems[:4]) or f"governance {governance}, {len(outputs)} page(s) match INVITED_BUILD.json"
              + (" (OVERRIDDEN)" if extra else ""), governance=governance, profile_sha256=profile, **extra)


def census(serve, override: bool = False) -> dict:
    """serve_sets.json holds upper bounds until an audit-hook census pins them (census_pending): no data goes out while it
    is pending, unless Paul says so (--census-override, recorded as census_override in the gates)."""
    if not serve.get("census_pending"):
        return _g(True, "serve sets are census-pinned")
    if override:
        return _g(True, "census_pending is true: shipping the unpinned upper bounds (OVERRIDDEN)", census_override=True)
    return _g(False, "serve_sets.json says census_pending: the sets are upper bounds, not pinned (--census-override to ship anyway)")


def disk_free(path, need_bytes: int) -> dict:
    from .core import free_bytes
    free = free_bytes(path)
    return _g(free >= need_bytes + DISK_HEADROOM, f"{free / 1024 ** 3:.1f} GB free, need {need_bytes / 1024 ** 3:.1f} GB + 10 GB")


def security_gate(ops_dir: Path, target: Path, stage: str = "export", more=(), scan_cache: Path | None = None) -> dict:
    """ops/security_gate.py <stage> --target <dir> [--target ...] must pass. Absent -> FAIL (closed)."""
    script = ops_dir / "security_gate.py"
    if not script.is_file():
        return _g(False, "ops/security_gate.py is absent (built by the security-gate lane): failing closed")
    argv = [sys.executable, str(script), stage]
    for path in (target, *more):
        argv += ["--target", str(path)]
    if scan_cache:
        argv += ["--scan-cache", str(scan_cache)]
    result = subprocess.run(argv, cwd=ops_dir.parent, capture_output=True)
    lines = (result.stdout + result.stderr).decode("utf-8", "replace").strip().splitlines()
    shown = [line for line in lines if line.startswith("FAIL")][:3] or lines[-1:] or [""]
    return _g(result.returncode == 0, f"exit {result.returncode}: " + "; ".join(line[:100] for line in shown))


def run_gates(cfg, *, skip_kernel=False, netlify=True, need_bytes=0, governance_override=False, serve_sets=None,
              census_override=False) -> dict:
    live, results = Path(cfg["melange_live"]), {}
    lane_roots = cfg.get("lane_writer_roots") or [str(Path(cfg["mentat_live"]) / "mentat" / ".cache" / "api-fanout") + "/*"]
    for name, fn in (("pipeline_lock", lambda: pipeline_lock(live)), ("weekly_lock", lambda: weekly_lock(live)),
                     ("mentat_lane_writers", lambda: mentat_lane_writers(lane_roots)),
                     ("repos_clean", lambda: repos_clean(cfg)),
                     ("kernel_tests", lambda: kernel_tests(live, skip_kernel)),
                     ("weekly_refresh", lambda: weekly_outcome(live)),
                     ("invited_build", lambda: invited_build(cfg, governance_override)),
                     *([("census", lambda: census(serve_sets, census_override))] if serve_sets is not None else []),
                     ("receipt_hash", (lambda: receipt_matches_deploy(live)) if netlify else (lambda: _g(True, "Netlify not in this release", skipped=True))),
                     ("disk", lambda: disk_free(os.environ.get("LOCALAPPDATA", str(Path.home())), need_bytes))):
        try:
            results[name] = fn()
        except Exception as error:                                  # a gate that cannot decide fails
            results[name] = _g(False, f"gate error: {type(error).__name__}: {error}"[:300])
    return results


def all_ok(gates: dict) -> bool:
    return bool(gates) and all(g["ok"] for g in gates.values())

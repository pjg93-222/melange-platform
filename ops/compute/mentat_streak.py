#!/usr/bin/env python3
"""Driver for the Mentat compute-job parity streak on the CI image digest (docket item 4; rulings D-173, D-177, D-188, Paul 2026-10-18).

Jobs: anchors, refit-detect, refit-parity, pocket-build.  One snapshot of one job is:

  freeze   copy the job's read set into DIR/S/J/inputs with a sha256 manifest (refuses a dirty or moved Mentat checkout, C: under 30 GB)
  plan     PRINT the exact A and B command lines and the environment; runs nothing
  run      A (Windows, the scheduled wrapper's launcher), then B (run_job.ps1, the container), sequentially, each from its own
           byte-identical, hash-verified copy of the frozen inputs;  needs --execute
  compare  Mentat's own tools/compute_parity.py compare, A as both references, the static mask of mentat_volatile.json;
           writes <results dir>/<job>__<snapshot>.json once (default C:\\melange-data\\compute_parity\\mentat, or $MELANGE_PARITY_DIR\\mentat)
           and deletes the scratch copies and A's launcher logs unless --keep
  status   the streak per job
  volatile show / validate the fixed list (--check-cites proves every cited Mentat file:line against the live checkout)

Rulings applied (Paul 2026-10-18, brain D-entry pending), all in mentat_volatile.json: refit-parity alone counts three repeat runs on the same pinned
inputs as three passes (`repeat_ok`); the receipt's R version, platform.* and package library paths, and the R version text inside the G0 gate detail
of the receipt and of review/gates.json (D-246 extension), are recorded as EXPLAINED differences, never anywhere near panels, bundle or coefficients
(`explained`); and a Windows A side that exits 1 is accepted for refit-parity only when its log
names the one known failing test and nothing else (`a_exit_explained`).

Method (Paul 2026-10-18): ONE Windows run serves as A1 and A2; only the fields of mentat_volatile.json are masked; money fields are never
maskable.  compute_parity.py binds a mask to the reference by tree digest only (`tree_digest(A) in (a1_digest, a2_digest)`), so a mask
built from the static list with both digests set to this A is accepted by the unmodified tool.  Nothing in C:\\Users\\pgoff\\Mentat is
written: the checkout is read (git status / rev-parse / ls-files, file reads) and everything runs from copies under the scratch folder.

Why B runs a COPY of tools/run_job.ps1: the wrapper derives MENTAT_HOST from its own location and LOCALAPPDATA_HOST from $env:LOCALAPPDATA
and lets no caller variable override either (run_job.ps1:288-292).  Run from the frozen copy, with LOCALAPPDATA pointed at the copy's
LocalAppData, B reads and writes only its own copy (the refit jobs mount the runtime read-write: the live one must never be B's).
The other seams (REPORTS_HOST, ANCHORS_OUT_HOST, LOO_HOST, CHE_HOST, ZIPC_HOST, POCKET_IN_HOST, POCKET_OUT_HOST, GLASSBOX_EVENTS_DIR,
MENTAT_REPORT_DIRECTORY) are set as the wrapper documents.  A relocated LOCALAPPDATA reaching docker.exe is unverified until the first B run.

Exit codes: 0 ok / pass; 1 the run or the comparison failed; 2 refused or bad usage (nothing ran).  Stdlib only.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

REPO_ROOT = Path(__file__).resolve().parents[2]
VOLATILE_FILE = Path(__file__).resolve().with_name("mentat_volatile.json")
DEFAULT_MENTAT = Path(r"C:\Users\pgoff\Mentat")
JOBS = ("anchors", "refit-detect", "refit-parity", "pocket-build")
FLOOR_GB = 30
STREAK_NEEDED = 3
BLOCK = 1 << 20
MASK_SCHEMA = "mentat-parity-mask-v1"     # tools/compute_parity.py SCHEMA
RESULT_SCHEMA = "mentat-streak-result-v1"
MANIFEST_SCHEMA = "mentat-streak-manifest-v1"
FREEZE_SCHEMA = "mentat-streak-freeze-v1"
VOLATILE_SCHEMA = "mentat-streak-volatile-v1"
MARKER = ".mentat_streak_snapshot"
# The judge pattern of mentat/COMPUTE.md (collector parity) plus usd|dollar|amt|contrib|giving|spend|value|share: a place whose name says money.
MONEY_RE = re.compile(r"amount|total|receipt|disburs|expend|cash|count|sum|cents|rank|percentil|threshold|usd|dollar|amt|contrib|giving|spend|value|share", re.I)
LOG_GLOB_RE = re.compile(r"(^|/)(log\.txt|[^/*?]*\.log)$")
CITE_RE = re.compile(r"^(mentat/\S+?):(\d+)(?:-(\d+))?$")
LEGACY_TASK_FILTER = "Mentat*"             # MentatNightlyAnchors, MentatRefitWatcher and the future *Container tasks
DEFAULT_RSCRIPT = r"C:\Program Files\R\R-4.6.0\bin\Rscript.exe"          # tools/mentat_env.py SEAMS; the MentatRefitWatcher task's Exe
STRIP_PREFIXES = ("MENTAT_", "RF_", "REFIT_", "ANCHORS_", "GLASSBOX_", "FEC_", "DOWNSTREAM_", "REPLAY_", "COMPOSE_")
STRIP_NAMES = {"REPORTS_HOST", "LOO_HOST", "CHE_HOST", "ZIPC_HOST", "COLLECT_HOST", "EVIDENCE_HOST", "POCKET_IN_HOST", "POCKET_OUT_HOST",
               "DIGEST", "LOCALAPPDATA_HOST", "GLASSBOX_HOST", "MENTAT_HOST", "REQUEST_POOL_CONFIG_FILE", "PYTHONPATH", "PYTHONHOME"}

# Where run_refit.R finds its pinned raw inputs on this machine (forecast/refit/R/rf_config.R:61-70); every entry is checked against the
# sha256 that registry/inputs.json pins, so a drifted vintage is refused at freeze, not discovered by gate G1.
ALIAS_SOURCES = {
    "LOO": ("path", r"C:\Users\pgoff\Documents\Codex\2026-09-26\loo", "ext/loo"),
    "CHE": ("path", r"C:\Users\pgoff\Documents\Codex\2026-09-27\che", "ext/che"),
    "SGSD": ("path", r"C:\Users\pgoff\Mentat\sector_growth_study\data", "Mentat/sector_growth_study/data"),
    "SGSA": ("path", r"C:\Users\pgoff\Mentat\sector_growth_study\archive\session_d613b40c_scratchpad",
             "Mentat/sector_growth_study/archive/session_d613b40c_scratchpad"),
    "ZIPC": ("file", r"C:\Users\pgoff\Downloads\House_Senate_Control_Forecasts_CSV.zip", "ext/House_Senate_Control_Forecasts_CSV.zip"),
    "INGEST": ("lad", "Mentat/forecast/ingest/runs", "LocalAppData/Mentat/forecast/ingest/runs"),
    "RFDATA": ("lad", "Mentat/forecast/refit/data", "LocalAppData/Mentat/forecast/refit/data"),
}
CODE_ALIASES = ("BUNDLE_V1", "CYCLE")      # travel with the tracked code (app/data/giving-models-v1.json, forecast/refit/inputs/cycle_model)
CODE_ROOTS = ("mentat/tools", "mentat/app", "mentat/forecast", "mentat/config", "mentat/inputs",
              "mentat/compute.compose.yaml", "mentat/compute.images.lock")
RUNTIME = "LocalAppData/Mentat/forecast/refit"
SECONDS_PER_MINUTE = 60
A_SLACK_MIN = 2                   # run_logged.ps1 kills the child at the cap; our outer bound is two minutes later
B_SLACK_MIN = 5 + 4 + 6           # the wrapper's own backstop, Docker Desktop's start (up to 4 min), slack
# How a snapshot is recorded when B is refused after A already ran (a gate that cannot be repeated on the same snapshot).  "aborted" is
# neither counted nor does it reset a streak; set it to "fail" if Paul rules it resets.
B_REFUSED_AFTER_A = "aborted"
GLOBAL_LOCK = ".mentat_streak.lock"
RESULTS_PARENT = r"C:\melange-data\compute_parity"       # a fixed absolute path (not a worktree); $MELANGE_PARITY_DIR overrides
DRIVER_FILE = Path(__file__).resolve()
RULING = "Paul 2026-10-18 (brain D-entry pending)"
RULING_PREFIX = "Paul 2026-10-18"
REPEAT_OK_JOBS = ("refit-parity",)
A_EXIT_JOBS = ("refit-parity",)
EXPLAINABLE_FILES = {"refit-parity": ("runs/{run_id}/receipt.json",)}               # the only file whose differences can be explained
EXPLAINABLE_LOCATIONS = ("/R_version", "/platform/*", "/packages/*/lib")             # `*` is exactly one path segment
RULING_D246 = "Paul 2026-10-18 (D-246 extension, brain entry pending)"
# Text-level explained differences: a string differs only by the R version text. The pattern lives here, in reviewed code, so the JSON list can never widen it.
# R.version.string is "R version 4.6.0 (2026-04-24 ucrt)" on Windows and "R version 4.6.0 (2026-04-24)" on Linux: one optional platform word, no free text.
R_VERSION_PLACEHOLDER = "<R-VERSION>"
TEXT_PATTERNS = {"r-version": re.compile(r"R version \d+\.\d+\.\d+ \(\d{4}-\d{2}-\d{2}(?: [A-Za-z0-9_.\-]+)?\)")}
EXPLAINABLE_TEXT = {"refit-parity": {"runs/{run_id}/receipt.json": ("/gates/0/detail",), "runs/{run_id}/review/gates.json": ("/gates/0/detail",)}}
MODEL_OUTPUT_RE = re.compile(r"panels|bundle|review|coef|projection|selected|backtest|regress|fit|model", re.I)
G_RE = {gate: re.compile(r"\b%s (PASS|FAIL)" % gate) for gate in ("G0", "G1", "G3")}
G2_RE = re.compile(r"\bG2 (PASS|FAIL): (\d+) expectations, (\d+) failed, (\d+) errors; (\d+) leaf mismatches")
FAILURE_RE = re.compile(r"(?:Failure|Error)\s*(?:\((test-[\w.\-]+\.R):(\d+)|:[^\n(]*\(@(test-[\w.\-]+\.R)#(\d+))")      # testthat 3e and 2e headers
HEAVY_PY_RE = re.compile(r"(weekly_refresh|daily_refresh|refresh_tick|run_pipeline|compute_parity|chain_parity)\.py", re.I)


class Refused(Exception):
    """A gate or an argument said no before anything ran; main() prints the message and returns 2."""


# --------------------------------------------------------------------------------------------------------- small helpers

def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(BLOCK), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def posix(path) -> str:
    return str(path).replace("\\", "/")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gb(n) -> str:
    return "%.1f GB" % (n / 2 ** 30)


def check_name(label: str, value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,30}", value or ""):
        raise Refused("%s %r must be 1-31 letters, digits, dots, dashes or underscores, starting with a letter or digit" % (label, value))
    return value


def results_default() -> Path:
    return Path(os.environ.get("MELANGE_PARITY_DIR") or RESULTS_PARENT) / "mentat"


def run_id_for(job: str, snapshot: str) -> str:
    return "parity-%s-%s" % (job, snapshot)          # valid for run_job.ps1 (letters, digits, dots, dashes, underscores; <= 80)


def load_module(path, name):
    """Import one stdlib-only Mentat file for its pure functions, without leaving bytecode in the checkout."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


# ------------------------------------------------------------------------------------------------------------------ host

@dataclass
class Proc:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class SystemHost:
    """Everything that touches the machine.  The tests substitute a fake with the same methods."""

    def run(self, argv, *, env=None, cwd=None, timeout=None) -> Proc:
        try:
            child = subprocess.Popen(list(argv), env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace")
        except OSError as error:
            return Proc(127, "", str(error))
        try:
            out, err = child.communicate(timeout=timeout)
            return Proc(child.returncode, out or "", err or "")
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(child.pid)], capture_output=True)
            else:
                child.kill()
            try:
                out, err = child.communicate(timeout=30)
            except subprocess.TimeoutExpired:         # ponytail: a grandchild still holds the pipe; its output is given up
                out, err = "", ""
            return Proc(124, out or "", err or "", True)

    def is_file(self, path) -> bool:
        return Path(path).is_file()

    def free_bytes(self, path) -> int:
        probe = Path(path)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return shutil.disk_usage(probe).free

    def system_drive(self) -> str:
        return os.environ.get("SystemDrive", "C:") + "\\"

    def lock_held(self, path) -> bool:
        """True when another process holds the file open exclusively (the wrapper opens its lock with FileShare.None)."""
        try:
            with open(path, "r+b"):
                return False
        except PermissionError:
            return True
        except OSError:
            return False

    def local_appdata(self) -> str:
        return os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    def lock_exclusive(self, path):
        """An open handle holding an exclusive lock on path (close it to release), or None when another process holds it."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        return handle

    def environ(self) -> dict:
        return dict(os.environ)

    def forbidden_roots(self) -> list:
        """Where a scratch must not be: temp folders, Mentat's and Melange's data under LOCALAPPDATA, the Melange repo (and its main checkout), ~/.claude."""
        import tempfile
        lad = Path(self.local_appdata())
        roots = {tempfile.gettempdir(), os.environ.get("TEMP", ""), os.environ.get("TMP", ""), str(lad / "Mentat"), str(lad / "Melange"),
                 str(Path.home() / ".claude"), str(REPO_ROOT)}
        if ".claude" in REPO_ROOT.parts and "worktrees" in REPO_ROOT.parts:
            roots.add(str(REPO_ROOT.parents[2]))          # <main>/.claude/worktrees/<name>
        return [r for r in roots if r]


def clean_env(environ: dict) -> dict:
    """The caller's environment without anything that would redirect a Mentat tool (a stray RF_*, MENTAT_*, ... would move a run's inputs)."""
    return {k: v for k, v in environ.items() if k not in STRIP_NAMES and not k.upper().startswith(STRIP_PREFIXES)}


# --------------------------------------------------------------------------------------------------------- the checkout

def git(host, root, *args, timeout=120) -> Proc:
    return host.run(["git", "-C", str(root), "--no-optional-locks", *args], timeout=timeout)


def mentat_identity(host, root) -> dict:
    """HEAD, branch, dirty lines (tracked changes AND untracked non-ignored files), the image lock.  Read only."""
    head = git(host, root, "rev-parse", "HEAD")
    status = git(host, root, "status", "--porcelain")
    branch = git(host, root, "branch", "--show-current")
    if head.returncode != 0 or status.returncode != 0:
        raise Refused("git could not read the Mentat checkout %s: %s" % (root, (head.stderr or status.stderr).strip()))
    lock_path = Path(root) / "mentat" / "compute.images.lock"
    try:
        lock = read_json(lock_path)
    except (OSError, ValueError) as error:
        raise Refused("cannot read %s: %s" % (lock_path, error)) from None
    return {"head": head.stdout.strip(), "branch": branch.stdout.strip(), "dirty": [l for l in status.stdout.splitlines() if l.strip()],
            "image": lock.get("image"), "digest": lock.get("digest")}


def identity_problems(ident: dict, expect_head=None, frozen=None) -> list:
    problems = []
    if ident["dirty"]:
        problems.append("the Mentat checkout is dirty (%d path(s), e.g. %s); a tracked edit or a commit resets the streak" % (len(ident["dirty"]), ident["dirty"][0]))
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(ident["digest"] or "")):
        problems.append("compute.images.lock holds no sha256 digest (got %r)" % (ident["digest"],))
    if expect_head and not ident["head"].startswith(expect_head.strip().lower()):
        problems.append("Mentat HEAD is %s, not the expected %s" % (ident["head"][:12], expect_head.strip()[:12]))
    if frozen:
        if ident["head"] != frozen["head"]:
            problems.append("Mentat HEAD moved since the freeze: %s -> %s" % (frozen["head"][:12], ident["head"][:12]))
        if ident["digest"] != frozen["digest"]:
            problems.append("compute.images.lock digest changed since the freeze")
    return problems


def job_caps(mentat_root) -> dict:
    """{job: minutes} from the JOBS registry of tools/compute_job.py, read as source (nothing is imported or run)."""
    path = Path(mentat_root) / "mentat" / "tools" / "compute_job.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError) as error:
        raise Refused("cannot read the job caps from %s: %s" % (path, error)) from None
    caps = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) and any(isinstance(t, ast.Name) and t.id == "JOBS" for t in node.targets):
            for key, value in zip(node.value.keys, node.value.values):
                if (isinstance(key, ast.Constant) and isinstance(value, ast.Call) and len(value.args) >= 2
                        and isinstance(value.args[1], ast.Constant) and isinstance(value.args[1].value, int)):
                    caps[key.value] = value.args[1].value
    missing = [j for j in JOBS if j not in caps]
    if missing:
        raise Refused("tools/compute_job.py JOBS has no cap for %s (the registry changed shape?)" % ", ".join(missing))
    return caps


def code_items(host, mentat_root) -> list:
    """Every tracked file of the code roots (the checkout is clean, so the working tree is HEAD)."""
    p = git(host, mentat_root, "ls-files", "-z", "--", *CODE_ROOTS)
    if p.returncode != 0:
        raise Refused("git ls-files failed: %s" % p.stderr.strip())
    items = []
    for rel in sorted(x for x in p.stdout.split("\0") if x):
        if not (Path(mentat_root) / rel).is_file():
            raise Refused("tracked file missing from the working tree: %s" % rel)
        items.append(Item(str(Path(mentat_root) / rel), "Mentat/" + rel, "code", "code"))
    return items


# ---------------------------------------------------------------------------------------------------- the volatile list

def _segments(pointer: str) -> list:
    return [s.replace("~1", "/").replace("~0", "~") for s in pointer.split("/")[1:]]


def money_problem(pointer: str):
    for segment in _segments(pointer):
        if MONEY_RE.search(segment):
            return "location %s has a money-like segment %r: money is never maskable" % (pointer, segment)
    return None


def validate_volatile(spec: dict) -> list:
    problems = []
    if spec.get("schema") != VOLATILE_SCHEMA:
        problems.append("schema is %r, expected %r" % (spec.get("schema"), VOLATILE_SCHEMA))
    for job, body in (spec.get("jobs") or {}).items():
        if job not in JOBS:
            problems.append("unknown job %r" % job)
            continue
        for index, entry in enumerate(body.get("mask", [])):
            where = "%s mask[%d]" % (job, index)
            file = entry.get("file", "")
            if not file.endswith(".json") or file.startswith("/") or ".." in file.split("/") or re.match(r"^[A-Za-z]:", file):
                problems.append("%s: file %r must be a relative .json path" % (where, file))
            if not CITE_RE.match(entry.get("cite", "")):
                problems.append("%s: cite %r must be mentat/<path>:<line> or <line>-<line>" % (where, entry.get("cite")))
            if not entry.get("needle") or not entry.get("reason"):
                problems.append("%s: needle and reason are required" % where)
            locations = entry.get("locations")
            if not locations or not isinstance(locations, list):
                problems.append("%s: locations must be a non-empty list" % where)
                continue
            for pointer in locations:
                if not isinstance(pointer, str) or not pointer.startswith("/") or re.search(r"[*?\[\]]", pointer):
                    problems.append("%s: location %r must be an exact JSON pointer" % (where, pointer))
                    continue
                bad = money_problem(pointer)
                if bad:
                    problems.append("%s: %s" % (where, bad))
        for index, entry in enumerate(body.get("ignore", [])):
            where = "%s ignore[%d]" % (job, index)
            if not LOG_GLOB_RE.search(entry.get("glob", "")):
                problems.append("%s: glob %r may name only log-like files (log.txt, *.log)" % (where, entry.get("glob")))
            if not CITE_RE.match(entry.get("cite", "")) or not entry.get("needle") or not entry.get("reason"):
                problems.append("%s: cite, needle and reason are required" % where)
        if "repeat_ok" in body:
            if not isinstance(body["repeat_ok"], bool):
                problems.append("%s: repeat_ok must be true or false" % job)
            elif body["repeat_ok"] and (job not in REPEAT_OK_JOBS or not str(body.get("repeat_ruling", "")).startswith(RULING_PREFIX)):
                problems.append("%s: repeat_ok needs a repeat_ruling starting %r and is allowed only for %s" % (job, RULING_PREFIX, ", ".join(REPEAT_OK_JOBS)))
        explained = body.get("explained", [])
        if explained and job not in EXPLAINABLE_FILES:
            problems.append("%s: no explained differences are allowed for this job" % job)
        for index, entry in enumerate(explained if job in EXPLAINABLE_FILES else []):
            where = "%s explained[%d]" % (job, index)
            file = entry.get("file", "")
            text = entry.get("text_pattern")
            if text is None:
                if file not in EXPLAINABLE_FILES[job] or MODEL_OUTPUT_RE.search(file):
                    problems.append("%s: file %r is not explainable (only %s; never panels, bundle, coefficients or review)" % (where, file, ", ".join(EXPLAINABLE_FILES[job])))
            else:
                if text not in TEXT_PATTERNS:
                    problems.append("%s: text_pattern %r is not one of %s" % (where, text, ", ".join(TEXT_PATTERNS)))
                if file not in EXPLAINABLE_TEXT.get(job, {}):
                    problems.append("%s: file %r is not explainable as text (only %s)" % (where, file, ", ".join(EXPLAINABLE_TEXT.get(job, {}))))
                if not CITE_RE.match(entry.get("origin_cite", "")) or not entry.get("origin_needle"):
                    problems.append("%s: origin_cite and origin_needle (where the text is built) are required" % where)
            if not CITE_RE.match(entry.get("cite", "")) or not entry.get("needle") or not entry.get("reason"):
                problems.append("%s: cite, needle and reason are required" % where)
            if not str(entry.get("ruling", "")).startswith(RULING_PREFIX):
                problems.append("%s: ruling must start %r" % (where, RULING_PREFIX))
            locations = entry.get("locations")
            if not locations or not isinstance(locations, list):
                problems.append("%s: locations must be a non-empty list" % where)
                continue
            allowed = EXPLAINABLE_LOCATIONS if text is None else EXPLAINABLE_TEXT.get(job, {}).get(file, ())
            for pointer in locations:
                if pointer not in allowed:
                    problems.append("%s: location %r is not one of %s" % (where, pointer, ", ".join(allowed) or "(none)"))
                elif money_problem(pointer):
                    problems.append("%s: %s" % (where, money_problem(pointer)))
        rule = body.get("a_exit_explained")
        if rule is not None:
            where = "%s a_exit_explained" % job
            if job not in A_EXIT_JOBS:
                problems.append("%s: not allowed for this job" % where)
            if rule.get("exit") != 1 or not isinstance(rule.get("exit"), int):
                problems.append("%s: exit must be 1" % where)
            failures = rule.get("failures")
            if not failures or not all(isinstance(f, str) and re.fullmatch(r"test-[\w.\-]+\.R:\d+", f) for f in failures):
                problems.append("%s: failures must list test-<name>.R:<line> entries" % where)
            if not str(rule.get("ruling", "")).startswith(RULING_PREFIX) or not rule.get("reason"):
                problems.append("%s: ruling (starting %r) and reason are required" % (where, RULING_PREFIX))
            cites = rule.get("cites")
            if not cites or not all(CITE_RE.match(c.get("cite", "")) and c.get("needle") for c in cites):
                problems.append("%s: cites must be a non-empty list of {cite, needle}" % where)
    return problems


def load_volatile(path=None) -> dict:
    spec = read_json(path or VOLATILE_FILE)
    problems = validate_volatile(spec)
    if problems:
        raise Refused("the volatile list is not acceptable:\n  " + "\n  ".join(problems))
    return spec


def _cited(body: dict):
    for key in ("mask", "ignore", "explained"):
        yield from body.get(key, [])
    for entry in body.get("explained", []):
        if entry.get("origin_cite"):
            yield {"cite": entry["origin_cite"], "needle": entry["origin_needle"]}
    yield from (body.get("a_exit_explained") or {}).get("cites", [])


def repeat_ok_jobs(spec: dict) -> set:
    return {job for job, body in spec["jobs"].items() if body.get("repeat_ok") is True}


def check_citations(spec: dict, mentat_root) -> list:
    """Each cited file:line range exists in the checkout and holds the entry's needle."""
    problems = []
    for job, body in spec["jobs"].items():
        for entry in _cited(body):
            match = CITE_RE.match(entry["cite"])
            rel, first, last = match.group(1), int(match.group(2)), int(match.group(3) or match.group(2))
            path = Path(mentat_root) / rel
            try:
                lines = path.read_text(encoding="utf-8-sig").splitlines()
            except OSError as error:
                problems.append("%s: cannot read %s (%s)" % (job, rel, error))
                continue
            if first < 1 or last > len(lines) or last < first:
                problems.append("%s: %s has %d lines, cite %s is out of range" % (job, rel, len(lines), entry["cite"]))
            elif entry["needle"] not in "\n".join(lines[first - 1:last]):
                problems.append("%s: %r is not on %s" % (job, entry["needle"], entry["cite"]))
    return problems


def flatten_pointers(value, prefix="", out=None) -> set:
    """The locations compute_parity.py's JSON view reports (same escaping: ~ -> ~0, / -> ~1; empty containers are leaves)."""
    out = set() if out is None else out
    if isinstance(value, dict) and value:
        for key in value:
            flatten_pointers(value[key], prefix + "/" + str(key).replace("~", "~0").replace("/", "~1"), out)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            flatten_pointers(item, prefix + "/" + str(index), out)
    else:
        out.add(prefix or "/")
    return out


def walk_rel(root: Path) -> list:
    root = Path(root)
    found = []
    for dirpath, _dirs, names in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        found.extend((n if rel_dir == "." else rel_dir + "/" + n) for n in names)
    return sorted(found)


def ignore_globs(spec: dict, job: str, run_id: str) -> list:
    return [e["glob"].format(run_id=run_id) for e in spec["jobs"][job].get("ignore", [])]


def glob_match(rel: str, pattern: str) -> bool:
    """fnmatch per path segment: a * never crosses a /."""
    r, p = rel.split("/"), pattern.split("/")
    return len(r) == len(p) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(r, p))


MISSING = object()


def pointer_match(pattern: str, pointer: str) -> bool:
    """A JSON pointer against an explained pattern: `*` stands for exactly one segment."""
    p, q = pattern.split("/"), pointer.split("/")
    return len(p) == len(q) and all(a == "*" or a == b for a, b in zip(p, q))


def resolve_pointer(doc, pointer: str):
    node = doc
    for segment in _segments(pointer):
        if isinstance(node, dict) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            node = node[int(segment)]
        else:
            return MISSING
    return node


def _shown(value, limit=200):
    if value is MISSING:
        return None
    return value if not isinstance(value, str) or len(value) <= limit else value[:limit] + "..."


def text_explains(pattern, a, b) -> bool:
    """Both are strings holding the pattern exactly once, and they are byte-identical once each occurrence is replaced by one placeholder."""
    return (isinstance(a, str) and isinstance(b, str) and len(pattern.findall(a)) == 1 and len(pattern.findall(b)) == 1
            and pattern.sub(R_VERSION_PLACEHOLDER, a) == pattern.sub(R_VERSION_PLACEHOLDER, b))


def explained_records(spec: dict, job: str, a_root, b_root, run_id: str) -> list:
    """Every location of an `explained` entry present on either side, with both values; `differs` says whether it needs the explanation.
    Only the entries of the static list are read, and only from the one allowed receipt file."""
    records = []
    for entry in spec["jobs"][job].get("explained", []):
        pattern = TEXT_PATTERNS.get(entry.get("text_pattern"))
        rel = entry["file"].format(run_id=run_id)
        docs = []
        for root in (a_root, b_root):
            path = Path(root) / rel
            try:
                docs.append(json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else None)
            except (OSError, ValueError):
                raise Refused("%s is not readable JSON, so its explained fields cannot be located" % path) from None
        pointers = set()
        for doc in docs:
            if doc is not None:
                pointers |= flatten_pointers(doc)
        for pointer in sorted(pointers):
            if not any(pointer_match(pattern, pointer) for pattern in entry["locations"]):
                continue
            bad = money_problem(pointer)
            if bad:
                raise Refused(bad)
            a = resolve_pointer(docs[0], pointer) if docs[0] is not None else MISSING
            b = resolve_pointer(docs[1], pointer) if docs[1] is not None else MISSING
            record = {"file": rel, "location": pointer, "a": _shown(a, 4000 if pattern else 200), "b": _shown(b, 4000 if pattern else 200), "differs": a != b,
                      "explains": True, "cite": entry["cite"], "ruling": entry["ruling"]}
            if pattern is not None:
                record["explains"] = text_explains(pattern, a, b)
                if record["explains"] and a != b:
                    record["normalized"] = pattern.sub(R_VERSION_PLACEHOLDER, a)
            records.append(record)
    return records


def build_mask(spec: dict, job: str, a_root, run_id: str, digest: str, explained=()) -> tuple:
    """The compute_parity mask for reference tree a_root, built from the static list only (plus the explained locations that differ).  -> (mask, notes)"""
    a_root = Path(a_root)
    available = walk_rel(a_root)
    files, notes = {}, {"absent_files": [], "absent_locations": {}}
    for entry in spec["jobs"][job].get("mask", []):
        for pointer in entry["locations"]:
            bad = money_problem(pointer)
            if bad:
                raise Refused(bad)
        pattern = entry["file"].format(run_id=run_id)
        hits = [rel for rel in available if glob_match(rel, pattern)]
        if not hits:
            notes["absent_files"].append(pattern)
        for rel in hits:
            try:
                present = flatten_pointers(json.loads((a_root / rel).read_text(encoding="utf-8-sig")))
            except (OSError, ValueError):
                raise Refused("%s is not readable JSON, so its volatile fields cannot be located" % rel) from None
            for pointer in entry["locations"]:
                if pointer in present:
                    files.setdefault(rel, set()).add(pointer)
                else:
                    notes["absent_locations"].setdefault(rel, []).append(pointer)
    for record in explained:
        if record["differs"] and record["explains"]:
            bad = money_problem(record["location"])
            if bad:
                raise Refused(bad)
            files.setdefault(record["file"], set()).add(record["location"])
    mask = {"schema": MASK_SCHEMA, "a1_digest": digest, "a2_digest": digest, "ignore": ignore_globs(spec, job, run_id),
            "unstable_files": [], "files": {rel: sorted(locs) for rel, locs in sorted(files.items())}}
    return mask, notes


# ------------------------------------------------------------------------------------------------------------ read sets

@dataclass(frozen=True)
class Item:
    src: str          # absolute source path
    dest: str         # POSIX path relative to a side root
    kind: str         # "code" (tracked Mentat files) or "data"
    role: str


def report_directory(project: Path) -> Path:
    config = read_json(Path(project) / "config" / "report-directory.json")
    directory = Path(config["directory"])
    if not directory.is_dir():
        raise Refused("the report directory %s is unavailable" % directory)
    return directory


def rebase_recorded(value: str, mentat_root: Path) -> Path:
    """Where a location recorded on Windows ('...\\Mentat\\mentat\\.cache\\x') lives under this checkout (app/receipt_path.py's rule)."""
    windows = PureWindowsPath(value)
    if windows.drive:
        parts = windows.parts[1:]
        for index, part in enumerate(parts):
            if part.lower() == "mentat":
                return Path(mentat_root).joinpath(*parts[index + 1:])
    return Path(value)


def _under(path: Path, root: Path) -> Path:
    try:
        return Path(os.path.realpath(path)).relative_to(os.path.realpath(root))
    except ValueError:
        raise Refused("%s is outside %s; a frozen tree cannot hold it" % (path, root)) from None


def anchors_items(mentat_root: Path, lad: Path) -> tuple:
    """Read set of build_giving_anchors.py (forecast/anchors/build_giving_anchors.py:121-148, :368-387, :480-495).  -> (items, meta)"""
    project = Path(mentat_root) / "mentat"
    items, notes = [], []
    reports = report_directory(project)
    ms = load_module(project / "forecast" / "anchors" / "mentat_sources.py", "_mentat_sources_live")
    registry = read_json(project / "forecast" / "anchors" / "registry.json")
    index_dir = project / ms.STATE_INDEX_DIR
    hashes = {}
    for spec in registry["industries"]:
        workbook = reports / spec["workbook"]
        if not workbook.is_file():
            notes.append("%s: workbook %s is not in the report directory (the job reports it unavailable)" % (spec["id"], spec["workbook"]))
            continue
        items.append(Item(str(workbook), "reports/" + workbook.name, "data", "workbook:" + spec["id"]))
        found = ms.find_index(index_dir, ms.sha256_file(workbook))
        if not found:
            notes.append("%s: no contribution-analytics index matches the published workbook" % spec["id"])
            continue
        index_path, manifest = found
        items.append(Item(str(index_path), "Mentat/mentat/" + _under(index_path, project).as_posix(), "data", "index:" + spec["id"]))
        source = ms.find_source_db(project, spec["source_db_candidates"], manifest["provenance"]["database_sha256"], hashes)
        if source is None:
            notes.append("%s: the pinned source database was not found under the project" % spec["id"])
            continue
        items.append(Item(str(source), "Mentat/mentat/" + _under(source, project).as_posix(), "data", "source_db:" + spec["id"]))
    if not any(i.role.startswith("source_db:") for i in items):
        raise Refused("no registry industry has a workbook, a matching index and its pinned source database: the anchors job would anchor nothing\n  " + "\n  ".join(notes))
    pointer = project / "inputs" / "active_fec_sources.json"
    if not pointer.is_file():
        raise Refused("%s is missing: the anchors job cannot find the FEC bulk files" % pointer)
    items.append(Item(str(pointer), "Mentat/mentat/inputs/active_fec_sources.json", "data", "fec_pointer"))
    fec_root = rebase_recorded(read_json(pointer)["path"], Path(mentat_root))
    fec_rel = "Mentat/mentat/" + _under(fec_root, project).as_posix()
    cycles = sorted({m.group(1) for d in (fec_root / "fec_beneficiary").glob("pas2??") if (m := re.fullmatch(r"pas2(\d\d)", d.name))})[-3:]
    if len(cycles) < 3:
        notes.append("only %d FEC cycle(s) found; the job reads open-4, open-2 and open" % len(cycles))
    for yy in cycles:
        for folder, sub, name in (("fec_beneficiary", "pas2" + yy, "itpas2.txt"), ("fec_beneficiary", "cn" + yy, "cn.txt"), ("fec_committee_master", "cm" + yy, "cm.txt")):
            for leaf in (name, "source-receipt.json"):
                path = fec_root / folder / sub / leaf
                if path.is_file():
                    items.append(Item(str(path), "%s/%s/%s/%s" % (fec_rel, folder, sub, leaf), "data", "fec:%s/%s" % (sub, leaf)))
                elif leaf == name:
                    raise Refused("FEC input missing: %s" % path)
    controls = Path(mentat_root) / "sector_growth_study" / "data" / "00_controls.xlsx"
    if not controls.is_file():
        raise Refused("%s is missing (the CPI controls)" % controls)
    items.append(Item(str(controls), "Mentat/sector_growth_study/data/00_controls.xlsx", "data", "cpi"))
    for name, dest in (("sector_spend/sector_spend.csv", "sector_spend/sector_spend.csv"), ("fullscope_receipts.json", "fullscope_receipts.json")):
        path = lad / "Melange" / "lda_rebuild" / "derived" / name
        if not path.is_file():
            raise Refused("%s is missing: the container requires it (compose bind mount, create_host_path false)" % path)
        items.append(Item(str(path), "LocalAppData/Melange/lda_rebuild/derived/" + dest, "data", "lda:" + name))
    return items, {"fec_root_rel": fec_rel, "report_dir": str(reports), "fec_cycles": cycles, "notes": notes}


def report_items(reports: Path) -> list:
    """Every ' Industry Spending Overview.xlsx' workbook and its audit manifest (forecast/refit/R/rf_agent.R:27-42)."""
    items = []
    for workbook in sorted(Path(reports).iterdir()):
        name = workbook.name
        if not (workbook.is_file() and name.endswith(" Industry Spending Overview.xlsx")) or name.startswith("~$") or "Rust FEC Verified" in name:
            continue
        items.append(Item(str(workbook), "reports/" + name, "data", "workbook"))
        manifest = Path(reports) / (name[:-len(".xlsx")] + ".audit") / "audit-manifest.json"
        if manifest.is_file():
            items.append(Item(str(manifest), "reports/%s.audit/audit-manifest.json" % name[:-len(".xlsx")], "data", "audit-manifest"))
    return items


def runtime_items(lad: Path, *folders) -> list:
    base = Path(lad) / "Mentat" / "forecast" / "refit"
    items = []
    for folder in folders:
        for rel in walk_rel(base / folder) if (base / folder).is_dir() else []:
            items.append(Item(str(base / folder / rel), "%s/%s/%s" % (RUNTIME, folder, rel), "data", "runtime:" + folder))
    return items


def parity_items(mentat_root: Path, lad: Path) -> tuple:
    """The 128 pinned raw inputs of registry/inputs.json, each checked against its pinned sha256, plus the vendored Rlib."""
    project = Path(mentat_root) / "mentat"
    manifest = read_json(project / "forecast" / "refit" / "registry" / "inputs.json")
    items, problems = [], []
    for entry in manifest["inputs"]:
        match = re.match(r"^\{([A-Z0-9_]+)\}(.*)$", entry["path"])
        if not match:
            problems.append("%s: path %r has no {ALIAS}" % (entry["role"], entry["path"]))
            continue
        alias, rest = match.group(1), match.group(2)
        if alias in CODE_ALIASES:
            source = project / "app" / "data" / "giving-models-v1.json" if alias == "BUNDLE_V1" else project / "forecast" / "refit" / "inputs" / "cycle_model" / rest.lstrip("/\\")
            kind_dest = None
        elif alias in ALIAS_SOURCES:
            how, base, dest_base = ALIAS_SOURCES[alias]
            root = lad / base if how == "lad" else Path(base)
            source = root if how == "file" else root / rest.lstrip("/\\")
            kind_dest = dest_base if how == "file" else dest_base + "/" + rest.lstrip("/\\").replace("\\", "/")
        else:
            problems.append("%s: unknown alias {%s}" % (entry["role"], alias))
            continue
        if not source.is_file():
            problems.append("%s: %s is missing" % (entry["role"], source))
            continue
        if sha256_file(source) != entry["sha256"]:
            problems.append("%s: %s no longer hashes to the pinned sha256 (a new vintage; gate G1 would fail on the Windows side too)" % (entry["role"], source))
            continue
        if kind_dest:
            items.append(Item(str(source), kind_dest, "data", "input:" + entry["role"]))
    if problems:
        raise Refused("refit-parity inputs do not match registry/inputs.json:\n  " + "\n  ".join(problems[:20]) + ("\n  (+%d more)" % (len(problems) - 20) if len(problems) > 20 else ""))
    return items + runtime_items(lad, "Rlib"), {"notes": ["the content-addressed vault (<runtime>/raw) is not copied: both sides rebuild it and the comparison covers it"]}


def readset(host, job: str, mentat_root: Path, pocket_data=None) -> tuple:
    """-> (items, meta).  Code is the tracked Mentat files; data is what the job reads from outside them."""
    lad = Path(host.local_appdata())
    items = code_items(host, mentat_root)
    project = Path(mentat_root) / "mentat"
    meta = {"notes": []}
    if job == "anchors":
        more, meta = anchors_items(mentat_root, lad)
    elif job == "refit-detect":
        reports = report_directory(project)
        more, meta = report_items(reports) + runtime_items(lad, "agent", "Rlib"), {"report_dir": str(reports), "notes": []}
        if not any(i.role == "workbook" for i in more):
            raise Refused("no ' Industry Spending Overview.xlsx' workbook in %s" % reports)
    elif job == "refit-parity":
        more, meta = parity_items(mentat_root, lad)
    elif job == "pocket-build":
        if not pocket_data:
            raise Refused("pocket-build needs --pocket-data FILE: the console writes pocket-data.json only while a publish runs, so the snapshot is a file you name")
        data = Path(pocket_data)
        if not data.is_file():
            raise Refused("--pocket-data %s is not a file" % data)
        try:
            json.loads(data.read_text(encoding="utf-8"))
        except ValueError as error:
            raise Refused("--pocket-data is not JSON: %s" % error) from None
        more, meta = [Item(str(data), "in/pocket-data.json", "data", "pocket-data")], {"notes": []}
    else:
        raise Refused("unknown job %r" % job)
    return items + more, meta


# ---------------------------------------------------------------------------------------------------- freeze and copies

def copy_hashed(src, dst, _after_read=None) -> tuple:
    """Copy src to dst, hashing the bytes read; refuses a source that changed while it was copied."""
    src, dst = Path(src), Path(dst)
    before = src.stat()
    dst.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with open(src, "rb") as reader, open(dst, "wb") as writer:
        for block in iter(lambda: reader.read(BLOCK), b""):
            h.update(block)
            writer.write(block)
    if _after_read:
        _after_read()
    after = src.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or dst.stat().st_size != before.st_size:
        raise Refused("%s changed while it was being copied; freeze again when it is quiet" % src)
    os.utime(dst, ns=(before.st_atime_ns, before.st_mtime_ns))
    return h.hexdigest(), before.st_size


def digest_of(files: dict, kind: str) -> str:
    return hashlib.sha256("\n".join("%s %s" % (rel, files[rel]["sha256"]) for rel in sorted(files) if files[rel]["kind"] == kind).encode()).hexdigest()


def build_manifest(items: list, master: Path, job: str, snapshot: str) -> dict:
    files = {}
    for item in sorted(items, key=lambda i: i.dest):
        if item.dest in files:
            if files[item.dest]["src"] != item.src:
                raise Refused("two sources for %s: %s and %s" % (item.dest, files[item.dest]["src"], item.src))
            continue
        sha, size = copy_hashed(item.src, master / item.dest)
        files[item.dest] = {"sha256": sha, "size": size, "kind": item.kind, "role": item.role, "src": item.src}
    return {"schema": MANIFEST_SCHEMA, "job": job, "snapshot": snapshot, "files": files, "count": len(files),
            "bytes": sum(f["size"] for f in files.values()), "code_digest": digest_of(files, "code"), "inputs_digest": digest_of(files, "data")}


def verify_tree(root, files: dict) -> list:
    """Problems proving root holds exactly the manifest's files, byte for byte (read back from disk)."""
    root = Path(root)
    have = set(walk_rel(root)) if root.is_dir() else set()
    want = set(files)
    problems = ["missing: " + rel for rel in sorted(want - have)[:10]] + ["unexpected: " + rel for rel in sorted(have - want)[:10]]
    for rel in sorted(want & have):
        path = root / rel
        if path.stat().st_size != files[rel]["size"] or sha256_file(path) != files[rel]["sha256"]:
            problems.append("differs from the manifest: " + rel)
    return problems


def space_problems(host, scratch: Path, need_bytes: int, floor_gb: int = FLOOR_GB) -> list:
    floor, problems = floor_gb * 2 ** 30, []
    system = host.free_bytes(host.system_drive())
    here = host.free_bytes(scratch)
    same = os.path.splitdrive(str(scratch))[0].lower() == os.path.splitdrive(host.system_drive())[0].lower()
    if system - (need_bytes if same else 0) < floor:
        problems.append("%s has %s free%s, below the %d GB floor" % (host.system_drive()[:2], gb(system), " after this run's copies (%s)" % gb(need_bytes) if same else "", floor_gb))
    if not same and here - need_bytes < floor:
        problems.append("the scratch drive has %s free after this run's copies (%s), below the %d GB floor" % (gb(here), gb(need_bytes), floor_gb))
    return problems


def check_scratch(host, scratch: Path, mentat_root=None) -> Path:
    if not scratch.is_absolute():
        raise Refused("--scratch must be an absolute path")
    scratch = Path(os.path.realpath(scratch))
    text = str(scratch)
    if re.search(r"[\s'\"`$&;|<>^%!()\[\]{}]", text):
        raise Refused("--scratch %s holds a space or a shell character; pick a plain path such as D:\\parity" % text)
    low = text.lower().replace("\\", "/")
    if "onedrive" in low:
        raise Refused("--scratch must not be under OneDrive (placeholders; Docker bind mounts)")
    for root in list(host.forbidden_roots()) + ([str(mentat_root)] if mentat_root else []):
        r = os.path.realpath(root).lower().replace("\\", "/").rstrip("/")
        if low == r or low.startswith(r + "/"):
            raise Refused("--scratch must not be under %s: a temp folder (Storage Sense and the 7-day cleaner delete it mid-run), the Mentat checkout, "
                          "%%LOCALAPPDATA%%\\Mentat or \\Melange, the Melange repository or %%USERPROFILE%%\\.claude" % root)
    return scratch


@dataclass
class Layout:
    root: Path

    @property
    def master(self): return self.root / "inputs"
    @property
    def manifest(self): return self.root / "inputs.manifest.json"
    @property
    def freeze(self): return self.root / "freeze.json"
    @property
    def run(self): return self.root / "run.json"
    @property
    def mask(self): return self.root / "mask.json"
    @property
    def marker(self): return self.root / MARKER

    def side(self, which): return self.root / which


def pointer_path(results: Path, job: str, snapshot: str) -> Path:
    return Path(results) / "_snapshots" / ("%s__%s.json" % (job, snapshot))


def result_path(results: Path, job: str, snapshot: str) -> Path:
    return Path(results) / ("%s__%s.json" % (job, snapshot))


def locate(args) -> Layout:
    check_name("--snapshot", args.snapshot)
    if getattr(args, "scratch", None):
        root = Path(os.path.realpath(args.scratch)) / args.snapshot / args.job
    else:
        pointer = pointer_path(args.results_dir, args.job, args.snapshot)
        if not pointer.is_file():
            raise Refused("no frozen snapshot %s/%s is known (freeze it, or pass --scratch)" % (args.job, args.snapshot))
        root = Path(read_json(pointer)["snapshot_root"])
    layout = Layout(root)
    if not layout.freeze.is_file():
        raise Refused("%s holds no frozen snapshot (run `freeze` first)" % root)
    return layout


def cmd_freeze(args, host) -> int:
    job, snapshot = args.job, check_name("--snapshot", args.snapshot)
    scratch = check_scratch(host, Path(args.scratch), args.mentat_root)
    results = Path(args.results_dir)
    if result_path(results, job, snapshot).exists():
        raise Refused("a result for %s/%s already exists; a snapshot name is used once, pick a new one" % (job, snapshot))
    layout = Layout(scratch / snapshot / job)
    if layout.root.exists() and any(layout.root.iterdir()):
        raise Refused("%s exists and is not empty" % layout.root)
    mentat_root = Path(args.mentat_root)
    ident = mentat_identity(host, mentat_root)
    problems = identity_problems(ident, expect_head=args.expect_head)
    problems += task_problems(host, window_minutes(job_caps(mentat_root)[job]) * SECONDS_PER_MINUTE) + lock_problems(host)
    problems += space_problems(host, scratch, 0, max(FLOOR_GB, args.min_free_gb))
    if problems:
        raise Refused("freeze refused:\n  " + "\n  ".join(problems))
    items, meta = readset(host, job, mentat_root, args.pocket_data)
    total = sum(os.path.getsize(i.src) for i in {i.src: i for i in items}.values())
    problems = space_problems(host, scratch, 3 * total, max(FLOOR_GB, args.min_free_gb))
    if problems:
        raise Refused("freeze refused (master + A + B copies need %s):\n  %s" % (gb(3 * total), "\n  ".join(problems)))
    layout.root.mkdir(parents=True, exist_ok=True)
    layout.marker.write_text("snapshot %s/%s of the Mentat streak driver; scratch only\n" % (job, snapshot), encoding="utf-8")
    manifest = build_manifest(items, layout.master, job, snapshot)
    after = mentat_identity(host, mentat_root)
    moved = identity_problems(after, frozen=ident)
    if moved or after["dirty"]:
        raise Refused("the Mentat checkout changed while freezing:\n  " + "\n  ".join(moved + after["dirty"][:3]))
    write_json(layout.manifest, manifest)
    record = {"schema": FREEZE_SCHEMA, "job": job, "snapshot": snapshot, "created_utc": utc_now(), "run_id": run_id_for(job, snapshot),
              "snapshot_root": str(layout.root), "scratch": str(scratch), "mentat_root": str(mentat_root),
              "mentat": {k: ident[k] for k in ("head", "branch", "image", "digest")}, "code_digest": manifest["code_digest"],
              "inputs_digest": manifest["inputs_digest"], "bytes": manifest["bytes"], "files": manifest["count"], "meta": meta,
              "free_bytes_at_freeze": host.free_bytes(host.system_drive())}
    write_json(layout.freeze, record)
    write_json(pointer_path(results, job, snapshot), {"snapshot_root": str(layout.root)})
    print(json.dumps({"frozen": "%s/%s" % (job, snapshot), "root": str(layout.root), "files": manifest["count"], "bytes": manifest["bytes"],
                      "head": ident["head"], "digest": ident["digest"], "inputs_digest": manifest["inputs_digest"], "notes": meta.get("notes", [])}, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------------------- commands

@dataclass
class Cmd:
    label: str
    argv: list
    env: dict = field(default_factory=dict)
    cwd: str = None
    timeout_s: int = 0

    def display(self) -> str:
        return subprocess.list2cmdline(self.argv)


def ps_quote(text) -> str:
    return "'" + str(text).replace("'", "''") + "'"


JOBSPEC = {
    "anchors": {"compare_root": "out/giving-anchors", "primary": ("giving-anchors-v1.json", "giving-anchors-v1.receipt.json")},
    "refit-detect": {"compare_root": RUNTIME, "primary": ("runs/{run_id}/detect.csv", "agent/seen.json")},
    "refit-parity": {"compare_root": RUNTIME, "primary": ("runs/{run_id}/receipt.json", "runs/{run_id}/bundle/giving-models-v1.projection.json")},
    "pocket-build": {"compare_root": "out", "primary": ("mentat-pocket.html",)},
}
POCKET_ARGS = ("--data", "/work/in/pocket-data.json", "--out", "/work/out/mentat-pocket.html")


def side_dirs(job: str, which: str) -> list:
    dirs = ["glassbox"]
    if job == "anchors":
        dirs.append("out/giving-anchors")
        if which == "B":
            dirs.append("Mentat/mentat/state/giving-anchors")       # the nested bind mount's target must exist inside the read-only /mentat
    elif job in ("refit-detect", "refit-parity"):
        dirs.append(RUNTIME)
    elif job == "pocket-build":
        dirs.append("out")
    if which == "B":
        dirs.append("LocalAppData/Mentat/compute/receipts")
    return dirs


def compare_maps(job: str, a_root) -> list:
    r = posix(a_root)
    maps = [(r + "/Mentat", "/mentat"), (r + "/LocalAppData", "/localappdata"), (r + "/reports", "/reports")]
    if job == "anchors":
        maps.append((r + "/out/giving-anchors", "/mentat/mentat/state/giving-anchors"))
    elif job == "refit-parity":
        maps += [(r + "/ext/loo", "/inputs/loo"), (r + "/ext/che", "/inputs/che"),
                 (r + "/ext/House_Senate_Control_Forecasts_CSV.zip", "/inputs/House_Senate_Control_Forecasts_CSV.zip")]
    elif job == "pocket-build":
        maps += [(r + "/in", "/work/in"), (r + "/out", "/work/out")]
    return maps


def build_plan(host, layout: Layout, record: dict, caps: dict) -> dict:
    job, snapshot, rid = record["job"], record["snapshot"], record["run_id"]
    a, b = layout.side("A"), layout.side("B")
    lad = Path(host.local_appdata())
    venv_python = lad / "Mentat" / "venv" / "Scripts" / "python.exe"
    cap = caps[job]
    a_exe, a_dir = str(venv_python), str(a / "Mentat")
    a_proj = a / "Mentat" / "mentat"
    a_env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "ANCHORS_GLASSBOX_DIR": str(a / "glassbox"),
             "GLASSBOX_EVENTS_DIR": str(a / "glassbox"), "RF_GLASSBOX_DIR": str(a / "glassbox")}
    if job == "anchors":
        a_args = [str(a_proj / "forecast" / "anchors" / "build_giving_anchors.py"), "--project-root", str(a_proj), "--output-dir", str(a / "out" / "giving-anchors"),
                  "--report-directory", str(a / "reports"), "--fec-root", str(a / record["meta"]["fec_root_rel"]),
                  "--lda-csv", str(a / "LocalAppData" / "Melange" / "lda_rebuild" / "derived" / "sector_spend" / "sector_spend.csv")]
    elif job == "pocket-build":
        a_args = [str(a_proj / "tools" / "pocket" / "build_page.py"), "--data", str(a / "in" / "pocket-data.json"), "--out", str(a / "out" / "mentat-pocket.html")]
    else:
        a_exe, a_dir = DEFAULT_RSCRIPT, str(a_proj / "forecast" / "refit")
        mode = "detect" if job == "refit-detect" else "parity"
        a_args = [str(a_proj / "forecast" / "refit" / "run_refit.R"), "--mode", mode, "--run-id", rid]
        a_env.update({"RF_RUNTIME": str(a / RUNTIME), "RF_REPORT_DIR": str(a / "reports")})
        if job == "refit-parity":
            a_env.update({"REFIT_CORES": "4", "RF_ROOT_LOO": str(a / "ext" / "loo"), "RF_ROOT_CHE": str(a / "ext" / "che"),
                          "RF_ROOT_ZIPC": str(a / "ext" / "House_Senate_Control_Forecasts_CSV.zip"),
                          "RF_ROOT_RLIBX": str(a / "ext" / "loo" / "work" / "lobbying_followup" / "Rlib"),
                          "RF_ROOT_SGSD": str(a / "Mentat" / "sector_growth_study" / "data"),
                          "RF_ROOT_SGSA": str(a / "Mentat" / "sector_growth_study" / "archive" / "session_d613b40c_scratchpad"),
                          "RF_ROOT_INGEST": str(a / "LocalAppData" / "Mentat" / "forecast" / "ingest" / "runs"),
                          "RF_ROOT_RFDATA": str(a / RUNTIME / "data")})
    run_logged = lad / "Mentat" / "scheduled" / "run_logged.ps1"
    name = "MentatParityA-%s-%s" % (job, snapshot)
    a_cmd = Cmd("A", ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(run_logged),
                      "-Name", name, "-TimeoutMinutes", str(cap), "-Exe", a_exe, "-WorkDir", a_dir, *a_args], a_env, a_dir, (cap + A_SLACK_MIN) * SECONDS_PER_MINUTE)
    b_env = {"LOCALAPPDATA": str(b / "LocalAppData"), "MENTAT_REPORT_DIRECTORY": str(b / "reports"), "REPORTS_HOST": posix(b / "reports"),
             "GLASSBOX_EVENTS_DIR": str(b / "glassbox"), "PYTHONDONTWRITEBYTECODE": "1"}
    job_args = []
    if job == "anchors":
        b_env["ANCHORS_OUT_HOST"] = posix(b / "out" / "giving-anchors")
    elif job == "pocket-build":
        b_env.update({"POCKET_IN_HOST": posix(b / "in"), "POCKET_OUT_HOST": posix(b / "out")})
        job_args = list(POCKET_ARGS)
    elif job == "refit-parity":
        b_env.update({"LOO_HOST": posix(b / "ext" / "loo"), "CHE_HOST": posix(b / "ext" / "che"),
                      "ZIPC_HOST": posix(b / "ext" / "House_Senate_Control_Forecasts_CSV.zip")})
    wrapper = b / "Mentat" / "mentat" / "tools" / "run_job.ps1"
    command = "& %s -Job %s -RunId %s -PythonPath %s" % (ps_quote(wrapper), job, rid, ps_quote(venv_python))
    if job_args:
        command += " -JobArgs " + ",".join(ps_quote(x) for x in job_args)
    command += "; exit $LASTEXITCODE"
    b_cmd = Cmd("B", ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command], b_env, str(b / "Mentat"),
                (cap + B_SLACK_MIN) * SECONDS_PER_MINUTE)       # the job's cap + the wrapper's backstop + Docker start + slack
    spec = JOBSPEC[job]
    return {"job": job, "snapshot": snapshot, "run_id": rid, "cap_minutes": cap, "A": a_cmd, "B": b_cmd,
            "compare_root": spec["compare_root"], "maps": compare_maps(job, a), "primary": [p.format(run_id=rid) for p in spec["primary"]]}


def window_minutes(cap: int) -> int:
    """How long a snapshot can take: A's bound plus B's bound."""
    return (cap + A_SLACK_MIN) + (cap + B_SLACK_MIN)


def cmd_plan(args, host) -> int:
    layout = locate(args)
    record = read_json(layout.freeze)
    plan = build_plan(host, layout, record, job_caps(args.mentat_root))
    if args.json:
        print(json.dumps({k: (v.__dict__ if isinstance(v, Cmd) else v) for k, v in plan.items()}, indent=1))
        return 0
    print("# %s / %s   run id %s   cap %d min   Mentat %s   image %s" % (plan["job"], plan["snapshot"], plan["run_id"], plan["cap_minutes"],
                                                                     record["mentat"]["head"][:12], record["mentat"]["digest"]))
    print("# Nothing runs.  Both sides start from fresh copies of %s, hash-verified against the manifest; A first, then B, never in parallel." % layout.master)
    for key in ("A", "B"):
        cmd = plan[key]
        print("\n## %s   (timeout %d s, cwd %s)" % (key, cmd.timeout_s, cmd.cwd))
        print("# environment: the caller's, minus %s*, plus" % "*, ".join(STRIP_PREFIXES) + ":")
        for name, value in sorted(cmd.env.items()):
            print("$env:%s = %s" % (name, ps_quote(value)))
        print(cmd.display())
    print("\n## compare roots   A: %s   B: %s" % (Path(layout.side("A")) / plan["compare_root"], Path(layout.side("B")) / plan["compare_root"]))
    print("## primary outputs (both sides, non-empty): " + ", ".join(plan["primary"]))
    print("## --map " + "  ".join("%s=%s" % m for m in plan["maps"]))
    return 0


# ----------------------------------------------------------------------------------------------------------------- run

def _ps(host, script, timeout=90):
    return host.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)


def task_problems(host, window_s: int) -> list:
    """No Mentat* scheduled task is running, and none that is enabled is due before this run could end (now + A's bound + B's bound)."""
    script = ("Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like '%s' } | ForEach-Object { "
              "$i = Get-ScheduledTaskInfo -TaskName $_.TaskName -TaskPath $_.TaskPath -ErrorAction SilentlyContinue; $n = ''; "
              "if ($i -and $i.NextRunTime -and $i.NextRunTime.Year -gt 2000) { $n = $i.NextRunTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }; "
              "'{0}|{1}|{2}' -f $_.TaskName, $_.State, $n }" % LEGACY_TASK_FILTER)
    p = _ps(host, script)
    if p.timed_out or p.returncode != 0:
        return ["cannot read the Mentat scheduled tasks (exit %s): refusing rather than guessing" % p.returncode]
    horizon = host.now() + dt.timedelta(seconds=window_s)
    problems = []
    for line in p.stdout.splitlines():
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        name, state, due = parts[0], parts[1].lower(), (parts[2] if len(parts) > 2 else "")
        if state == "running":
            problems.append("scheduled task %s is running; a parity run would be a second writer" % name)
        elif state != "disabled" and due:
            when = dt.datetime.strptime(due, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            if when < horizon:
                problems.append("scheduled task %s is due at %s UTC, inside this run's %d-minute window (now + A + B bounds)" % (name, due, window_s // SECONDS_PER_MINUTE))
    return problems


def docker_problems(host, image: str, allow=()) -> list:
    """The engine answers (before A, not after four hours of it), nothing else is running, and the image is in the local store (no pull during a run)."""
    info = host.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30)
    if info.timed_out:
        return ["`docker info` hung (Docker Desktop is wedged: Paul's restart, host.md section 1)"]
    if info.returncode != 0:
        return ["Docker is not answering (`docker info` exit %s): start Docker Desktop and wait for it before A, or B fails after A has spent its hours" % info.returncode]
    problems = []
    ps = host.run(["docker", "ps", "--format", "{{.Names}}"], timeout=30)
    if ps.timed_out or ps.returncode != 0:
        problems.append("`docker ps` failed (exit %s)" % ps.returncode)
    else:
        names = [n.strip() for n in ps.stdout.splitlines() if n.strip() and n.strip() not in allow]
        if names:
            problems.append("docker has running container(s): %s (one heavy job at a time; --allow-container NAME for a known light one)" % ", ".join(names))
    inspect = host.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], timeout=60)
    if inspect.timed_out or inspect.returncode != 0:
        problems.append("the image %s is not in the local Docker store; pull it by digest first (a pull inside a run adds a network dependency)" % image)
    return problems


def host_load(host) -> list:
    """Known heavy host processes (the refreshes, the pipeline, any Rscript, other parity runs), one line each."""
    script = ("Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(python|pythonw|Rscript|Rterm)' } | "
              "ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId, $_.Name, $_.CommandLine }")
    p = _ps(host, script)
    if p.timed_out or p.returncode != 0:
        return ["cannot list the host's processes (exit %s)" % p.returncode]
    found = []
    for line in p.stdout.splitlines():
        pid, _, rest = line.partition("|")
        name, _, command = rest.partition("|")
        if not pid.strip().isdigit() or int(pid) == os.getpid():
            continue
        if name.lower().startswith(("rscript", "rterm")) or HEAVY_PY_RE.search(command):
            found.append("pid %s %s %s" % (pid.strip(), name, command.strip()[:160]))
    return found


def lock_problems(host, own: Path = None) -> list:
    problems = []
    locks = Path(host.local_appdata()) / "Mentat" / "compute" / "locks"
    for path in sorted(locks.glob("*.lock")) if locks.is_dir() else []:
        if host.lock_held(path):
            problems.append("compute job lock %s is held" % path.name)
    if own is not None and own.exists():
        problems.append("%s exists: another driver run is in progress or died (delete it only after checking)" % own)
    return problems


def tool_problems(host, job: str) -> list:
    lad = Path(host.local_appdata())
    needed = [(lad / "Mentat" / "scheduled" / "run_logged.ps1", "the scheduled-task launcher run_logged.ps1"),
              (lad / "Mentat" / "venv" / "Scripts" / "python.exe", "Mentat's venv python (the wrapper's -PythonPath and the Windows side)")]
    if job in ("refit-detect", "refit-parity"):
        needed.append((Path(DEFAULT_RSCRIPT), "Rscript 4.6.0"))
    return ["missing: %s (%s)" % (path, what) for path, what in needed if not host.is_file(path)]


def preflight(host, args, frozen: dict, layout: Layout, need_bytes: int = 0, window_s: int = 0, own: Path = None) -> None:
    ident = mentat_identity(host, args.mentat_root)
    image = "%s@%s" % (frozen.get("image"), frozen["digest"])
    problems = identity_problems(ident, frozen=frozen) + tool_problems(host, args.job)
    problems += task_problems(host, window_s) + docker_problems(host, image, args.allow_container) + lock_problems(host, own)
    problems += space_problems(host, layout.root, need_bytes, max(FLOOR_GB, args.min_free_gb))
    load = host_load(host)
    if load and getattr(args, "allow_host_load", False):
        print("WARNING (--allow-host-load): heavy host work is running:\n  " + "\n  ".join(load), file=sys.stderr)
    elif load:
        problems.append("heavy host work is running (one heavy job at a time; --allow-host-load to go ahead):\n    " + "\n    ".join(load))
    if problems:
        raise Refused("refused, nothing started:\n  " + "\n  ".join(problems))


def prepare_side(host, layout: Layout, which: str, manifest: dict, job: str) -> None:
    side = layout.side(which)
    if side.exists():
        raise Refused("%s already exists; this snapshot was run before" % side)
    shutil.copytree(layout.master, side, copy_function=shutil.copy2)
    problems = verify_tree(side, manifest["files"])
    if problems:
        raise Refused("the copy %s is not byte-identical to the frozen inputs:\n  %s" % (which, "\n  ".join(problems)))
    for rel in side_dirs(job, which):
        (side / rel).mkdir(parents=True, exist_ok=True)


def tail(text: str, n=3000) -> str:
    return (text or "")[-n:]


def a_log_files(host, name: str) -> dict:
    """run_logged.ps1's files for one Name: <name>-<yyyyMMdd-HHmmss>.out.log / .err.log, oldest first, and the <name>.log summary."""
    logs = Path(host.local_appdata()) / "Mentat" / "logs" / "scheduled"
    pattern = re.compile(r"^%s(?:-\d{8}-\d{6}\.(out|err)\.log|(\.log))$" % re.escape(name))
    found = {"out": [], "err": [], "summary": []}
    for path in sorted(logs.iterdir()) if logs.is_dir() else []:
        match = pattern.match(path.name)
        if match:
            found[match.group(1) or "summary"].append(path)
    for kind in ("out", "err"):
        found[kind].sort(key=lambda p: p.stat().st_mtime)
    return found


def log_tails(host, name: str) -> dict:
    found = a_log_files(host, name)
    return {kind: tail(found[kind][-1].read_text(encoding="utf-8", errors="replace")) for kind in ("out", "err") if found[kind]}


def read_logs(host, name: str) -> str:
    found = a_log_files(host, name)
    return "\n".join(found[kind][-1].read_text(encoding="utf-8", errors="replace") for kind in ("out", "err") if found[kind])


def parse_parity_log(text: str) -> dict:
    """What a refit parity run prints: the gate lines and, only if a non-silent reporter ran, the testthat failure headers."""
    gates = {gate: (m.group(1) if (m := rx.search(text)) else None) for gate, rx in G_RE.items()}
    m = G2_RE.search(text)
    g2 = {"status": m.group(1), "expectations": int(m.group(2)), "failed": int(m.group(3)), "errors": int(m.group(4)), "leaf_mismatches": int(m.group(5))} if m else None
    failures = ["%s:%s" % (f[0] or f[2], f[1] or f[3]) for f in FAILURE_RE.findall(text)]
    return {"gates": gates, "g2": g2, "failures": failures}


def explain_a_exit(spec: dict, job: str, code: int, text: str):
    """None when the job has no such ruling.  Else whether A's non-zero exit is accepted: the exit is the ruled one, G0, G1 and G3 pass, G2 failed
    with no error and no leaf mismatch, and the testthat failure headers in the log are exactly the pinned ones (any other failing test, or a change in
    their number, means A failed).  A log that names no failing test cannot show it, so it is not accepted."""
    rule = spec["jobs"][job].get("a_exit_explained")
    if not rule:
        return None
    seen, pinned, why = parse_parity_log(text), sorted(rule["failures"]), []
    if code != rule["exit"]:
        why.append("A exited %s; the ruling covers exit %s only" % (code, rule["exit"]))
    for gate in ("G0", "G1", "G3"):
        if seen["gates"][gate] != "PASS":
            why.append("gate %s is %s in the A log, not PASS" % (gate, seen["gates"][gate] or "absent"))
    g2 = seen["g2"]
    if g2 is None:
        why.append("the A log has no `G2 FAIL: n expectations, n failed, ...` line")
    else:
        if g2["status"] != "FAIL" or g2["errors"] or g2["leaf_mismatches"]:
            why.append("G2 is %s with %d errors and %d leaf mismatches; the ruling covers failed expectations only" % (g2["status"], g2["errors"], g2["leaf_mismatches"]))
        if g2["failed"] != len(pinned):
            why.append("%d expectations failed; the ruling covers %d" % (g2["failed"], len(pinned)))
    if not seen["failures"]:
        why.append("the A log names no failing test (the suite runs with the silent reporter and prints counts only), so the failures cannot be attributed to %s"
                   % ", ".join(pinned))
    elif sorted(seen["failures"]) != pinned:
        why.append("failing tests in the A log: %s; the ruling covers only %s" % (", ".join(sorted(seen["failures"])), ", ".join(pinned)))
    return {"accepted": not why, "why": why, "ruling": rule["ruling"], "pinned_failures": pinned, "observed": seen}


def execute(host, cmd: Cmd) -> dict:
    env = clean_env(host.environ())
    env.update(cmd.env)
    started = utc_now()
    t0 = dt.datetime.now(dt.timezone.utc)
    proc = host.run(cmd.argv, env=env, cwd=cmd.cwd, timeout=cmd.timeout_s)
    return {"label": cmd.label, "exit": proc.returncode, "timed_out": proc.timed_out, "started_utc": started, "finished_utc": utc_now(),
            "seconds": round((dt.datetime.now(dt.timezone.utc) - t0).total_seconds(), 1), "argv": cmd.argv, "stdout": tail(proc.stdout), "stderr": tail(proc.stderr)}


def write_failure(results: Path, record: dict, ident: dict, run_record: dict, reasons: list, status: str = "fail") -> None:
    doc = result_skeleton(record, ident, run_record, status, reasons)
    doc["phase"] = "run"
    write_json(result_path(results, record["job"], record["snapshot"]), doc)


def result_skeleton(record: dict, ident: dict, run_record: dict, status: str, reasons: list) -> dict:
    return {"schema": RESULT_SCHEMA, "job": record["job"], "snapshot": record["snapshot"], "status": status, "reasons": reasons,
            "mentat_head": record["mentat"]["head"], "mentat_branch": record["mentat"]["branch"], "image_digest": record["mentat"]["digest"],
            "inputs_digest": record["inputs_digest"], "code_digest": record["code_digest"], "files": record["files"], "bytes": record["bytes"],
            "run_id": record["run_id"], "a": run_record.get("A"), "b": run_record.get("B"), "started_utc": run_record.get("started_utc", utc_now()),
            "finished_utc": utc_now(), "volatile_sha256": sha256_file(VOLATILE_FILE), "driver_sha256": sha256_file(DRIVER_FILE),
            "a_exit_explained": (run_record.get("A") or {}).get("exit_explained")}


def cmd_run(args, host) -> int:
    layout = locate(args)
    record = read_json(layout.freeze)
    manifest = read_json(layout.manifest)
    caps = job_caps(args.mentat_root)
    plan = build_plan(host, layout, record, caps)
    if not args.execute:
        print("dry run: nothing started.  The plan (add --execute to run it):")
        return cmd_plan(args, host)
    if layout.run.exists():
        raise Refused("%s exists: this snapshot has been run" % layout.run)
    if result_path(args.results_dir, args.job, args.snapshot).exists():
        raise Refused("a result for %s/%s already exists" % (args.job, args.snapshot))
    spec = load_volatile()
    guard_path = Path(args.results_dir) / GLOBAL_LOCK
    guard = host.lock_exclusive(guard_path)
    if guard is None:
        raise Refused("another streak run holds %s" % guard_path)
    own, created = layout.root / ".run.lock", False
    window = plan["A"].timeout_s + plan["B"].timeout_s
    try:
        preflight(host, args, record["mentat"], layout, manifest["bytes"], window, own)
        own.write_text("pid %d\n" % os.getpid(), encoding="utf-8")
        created = True
        run_record = {"schema": "mentat-streak-run-v1", "job": args.job, "snapshot": args.snapshot, "run_id": record["run_id"], "started_utc": utc_now()}
        for step, label in (("A", "Windows reference"), ("B", "container via run_job.ps1")):
            try:
                if step == "B":
                    preflight(host, args, record["mentat"], layout, manifest["bytes"], window)
                prepare_side(host, layout, step, manifest, args.job)
            except Refused as error:
                if step == "A":
                    raise
                write_json(layout.run, run_record)
                write_failure(args.results_dir, record, record["mentat"], run_record, ["B was refused after A had run: %s" % error], B_REFUSED_AFTER_A)
                print("%s: B was refused after A had run: %s" % (B_REFUSED_AFTER_A.upper(), error))
                return 1
            run_record[step] = execute(host, plan[step])
            if step == "A":
                name = "MentatParityA-%s-%s" % (args.job, args.snapshot)
                run_record["A"]["logs"] = log_tails(host, name)
                if run_record["A"]["exit"] != 0:
                    explained = explain_a_exit(spec, args.job, run_record["A"]["exit"], read_logs(host, name))
                    if explained is not None:
                        run_record["A"]["exit_explained"] = explained
            if step == "B" and run_record["B"]["timed_out"]:
                name = "mentat-compute-" + record["run_id"].lower()          # the wrapper's container name (run_job.ps1: $containerName)
                removed = host.run(["docker", "rm", "-f", name], timeout=60)
                run_record["B"]["cleanup"] = {"argv": ["docker", "rm", "-f", name], "exit": removed.returncode}
            write_json(layout.run, run_record)
            moved = identity_problems(mentat_identity(host, args.mentat_root), frozen=record["mentat"])
            if moved:
                run_record["identity"] = moved
                write_json(layout.run, run_record)
                write_failure(args.results_dir, record, record["mentat"], run_record, ["the Mentat checkout moved during the %s run: %s" % (step, "; ".join(moved))])
                print("FAIL: " + "; ".join(moved))
                return 1
            if run_record[step]["exit"] != 0 and not (step == "A" and (run_record["A"].get("exit_explained") or {}).get("accepted")):
                reason = "%s side (%s) exited %s%s" % (step, label, run_record[step]["exit"], " (timed out)" if run_record[step]["timed_out"] else "")
                explained = run_record[step].get("exit_explained")
                if explained:
                    reason += "; the exit-1 ruling does not apply: " + "; ".join(explained["why"])
                if step == "A" and args.continue_after_a_failure:
                    continue
                write_json(layout.run, run_record)
                write_failure(args.results_dir, record, record["mentat"], run_record, [reason])
                print("FAIL: " + reason)
                return 1
        run_record["finished_utc"] = utc_now()
        write_json(layout.run, run_record)
        print(json.dumps({"ran": "%s/%s" % (args.job, args.snapshot), "A": run_record["A"]["exit"], "B": run_record["B"]["exit"], "next": "compare"}))
        return 0
    finally:
        if created:
            try:
                own.unlink()
            except OSError:
                pass
        guard.close()


# -------------------------------------------------------------------------------------------------------------- compare

class MentatCompare:
    """Mentat's own tools/compute_parity.py: the digest function in-process (a pure function), the verdict by its CLI."""

    def __init__(self, host, mentat_root, python=None):
        self.host, self.path = host, Path(mentat_root) / "mentat" / "tools" / "compute_parity.py"
        self.python = python or sys.executable
        self._module = None

    def tree_digest(self, root, ignore=()) -> str:
        if self._module is None:
            self._module = load_module(self.path, "_mentat_compute_parity_live")
        return self._module.tree_digest(root, list(ignore))

    def compare(self, a, b, mask, maps, ignore, report) -> Proc:
        argv = [self.python, "-B", str(self.path), "compare", str(a), str(b), "--mask", str(mask), "--report", str(report)]
        for host_prefix, container_prefix in maps:
            argv += ["--map", "%s=%s" % (host_prefix, container_prefix)]
        for glob in ignore:
            argv += ["--ignore", glob]
        return self.host.run(argv, timeout=6 * 3600)


def derived_problems(job: str, root: Path, label: str) -> list:
    problems = []
    if job == "anchors":
        snapshot, receipt = root / "giving-anchors-v1.json", root / "giving-anchors-v1.receipt.json"
        if snapshot.is_file() and receipt.is_file():
            try:
                recorded = read_json(receipt).get("snapshot_sha256")
            except (OSError, ValueError, AttributeError):
                return ["%s: giving-anchors-v1.receipt.json is not readable JSON" % label]
            if recorded != sha256_file(snapshot):
                problems.append("%s: the receipt's snapshot_sha256 is not the sha256 of the snapshot beside it" % label)
    return problems


def anchors_coverage(root: Path) -> dict:
    path = root / "giving-anchors-v1.json"
    if not path.is_file():
        return {}
    data = read_json(path)
    statuses = {k: v.get("status") for k, v in (data.get("industries") or {}).items()}
    not_ready = {k: {b: s for b, s in st.items() if s != "ready"} for k, st in statuses.items() if any(s != "ready" for s in st.values())}
    return {"industries": sorted(statuses), "unavailable": data.get("unavailable") or {}, "blocks_not_ready": not_ready}


def safe_rmtree(layout: Layout, scratch_root: Path) -> None:
    root = Path(os.path.realpath(layout.root))
    if not layout.marker.is_file() or Path(os.path.realpath(scratch_root)) not in root.parents or len(root.parts) < 4:
        raise Refused("refusing to delete %s: it is not a marked snapshot folder under the scratch root" % root)

    def fix(function, path, _excinfo):
        os.chmod(path, 0o700)
        function(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(root, onexc=lambda f, p, e: fix(f, p, e))
    else:
        shutil.rmtree(root, onerror=fix)


def delete_a_logs(host, job: str, snapshot: str) -> None:
    """A's run_logged.ps1 files for this snapshot (new and unique to it)."""
    for paths in a_log_files(host, "MentatParityA-%s-%s" % (job, snapshot)).values():
        for path in paths:
            try:
                path.unlink()
            except OSError:
                pass


def cmd_compare(args, host, tool=None) -> int:
    layout = locate(args)
    record = read_json(layout.freeze)
    results = Path(args.results_dir)
    target = result_path(results, args.job, args.snapshot)
    if target.exists():
        existing = read_json(target)
        if existing.get("phase") != "run":
            raise Refused("%s exists: a result is written once" % target)
        print(json.dumps({"job": args.job, "snapshot": args.snapshot, "status": existing["status"], "reasons": existing["reasons"], "result": str(target),
                          "note": "recorded by run; there is nothing left to compare", "scratch_kept": bool(args.keep)}, indent=1))
        if not args.keep:
            safe_rmtree(layout, Path(record["scratch"]))
            delete_a_logs(host, args.job, args.snapshot)
        return 1
    if not layout.run.is_file():
        raise Refused("%s has not been run" % layout.root)
    run_record = read_json(layout.run)
    spec = load_volatile()
    ident = mentat_identity(host, args.mentat_root)
    if ident["dirty"]:
        raise Refused("the Mentat checkout is dirty at compare time (%s); nothing was judged and the scratch is kept. Restore the tracked state and compare again"
                      % ident["dirty"][0])
    plan = build_plan(host, layout, record, job_caps(args.mentat_root))
    job, rid = args.job, record["run_id"]
    reasons = identity_problems(ident, frozen=record["mentat"])
    for side in ("A", "B"):
        info = run_record.get(side)
        if info is None:
            reasons.append("the %s side never ran" % side)
        elif info["exit"] != 0 and not (side == "A" and (info.get("exit_explained") or {}).get("accepted")):
            reasons.append("%s side exited %s" % (side, info["exit"]))
    a_cmp, b_cmp = layout.side("A") / plan["compare_root"], layout.side("B") / plan["compare_root"]
    report_path = layout.root / "report.json"
    out = result_skeleton(record, ident, run_record, "fail", [])
    out.update({"phase": "compare", "mentat_head_at_compare": ident["head"], "compare_root": plan["compare_root"], "maps": plan["maps"]})
    if a_cmp.is_dir() and b_cmp.is_dir():
        for side, root in (("A", a_cmp), ("B", b_cmp)):
            for primary in plan["primary"]:
                path = root / primary
                if not path.is_file() or path.stat().st_size == 0:
                    reasons.append("%s side has no non-empty %s" % (side, primary))
            reasons += derived_problems(job, root, side)
        ignore = ignore_globs(spec, job, rid)
        venv = Path(host.local_appdata()) / "Mentat" / "venv" / "Scripts" / "python.exe"
        tool = tool or MentatCompare(host, args.mentat_root, python=args.python or (str(venv) if venv.is_file() else sys.executable))
        digest = tool.tree_digest(a_cmp, ignore)
        records = explained_records(spec, job, a_cmp, b_cmp, rid)
        mask, notes = build_mask(spec, job, a_cmp, rid, digest, records)
        out["explained"] = [{"class": "EXPLAINED", **{k: r[k] for k in ("file", "location", "a", "b", "cite", "ruling", "normalized") if k in r}}
                            for r in records if r["differs"] and r["explains"]]
        out["explained_rejected"] = [{k: r[k] for k in ("file", "location", "a", "b")} | {"why": "not byte-identical once the R version text is replaced by one placeholder"}
                                     for r in records if r["differs"] and not r["explains"]]
        write_json(layout.mask, mask)
        proc = tool.compare(a_cmp, b_cmp, layout.mask, plan["maps"], ignore, report_path)
        report = read_json(report_path) if report_path.is_file() else None
        out.update({"mask": mask, "mask_notes": notes, "ignore": ignore, "compare_exit": proc.returncode, "compare_stderr": tail(proc.stderr, 1500)})
        if report is None:
            reasons.append("compute_parity.py produced no report (exit %s): %s" % (proc.returncode, tail(proc.stderr, 400).strip()))
        else:
            counts = report.get("counts", {})
            files = report.get("files", {})
            out["compare"] = {"ok": report.get("ok"), "counts": counts, "only_in_a": report.get("only_in_a"), "only_in_b": report.get("only_in_b"),
                              "different": {r: v["where"] for r, v in files.items() if v["class"] == "DIFFERENT"},
                              "needs_acceptance": {r: {"class": v["class"], "where": v["where"]} for r, v in files.items() if v["class"] in ("PATH_ONLY", "EOL_ONLY", "ZIP_CONTENT", "MASKED")}}
            if proc.returncode != 0 or not report.get("ok"):
                reasons.append("compute_parity.py compare failed: %d DIFFERENT, %d only in A, %d only in B" % (
                    counts.get("DIFFERENT", 0), len(report.get("only_in_a", [])), len(report.get("only_in_b", []))))
            out["compare"]["explained"] = len(out["explained"])
            if not files:
                reasons.append("nothing was compared (an empty tree proves nothing)")
        if job == "anchors":
            out["coverage"] = anchors_coverage(a_cmp)
    else:
        reasons.append("an output tree is missing (A: %s, B: %s)" % (a_cmp.is_dir(), b_cmp.is_dir()))
    out["status"] = "pass" if not reasons else "fail"
    out["reasons"] = reasons
    out["finished_utc"] = utc_now()
    out["scratch_kept"] = bool(args.keep)
    if out["status"] == "fail" and report_path.is_file():
        kept = results / ("%s__%s.report.json" % (job, args.snapshot))
        shutil.copyfile(report_path, kept)
        out["report_file"] = str(kept)
    write_json(target, out)
    if not args.keep:
        safe_rmtree(layout, Path(record["scratch"]))
        delete_a_logs(host, job, args.snapshot)
    print(json.dumps({"job": job, "snapshot": args.snapshot, "status": out["status"], "reasons": reasons, "counts": (out.get("compare") or {}).get("counts"),
                      "result": str(target), "scratch_kept": out["scratch_kept"]}, indent=1))
    return 0 if out["status"] == "pass" else 1


# ---------------------------------------------------------------------------------------------------------------- status

def load_results(results: Path) -> list:
    found = []
    for path in sorted(Path(results).glob("*__*.json")) if Path(results).is_dir() else []:
        try:
            doc = read_json(path)
        except (OSError, ValueError):
            continue
        if doc.get("schema") == RESULT_SCHEMA:
            found.append(doc)
    return found


def compute_streaks(results: list, need: int = STREAK_NEEDED, repeat_ok=None) -> dict:
    """Per job, in the order the snapshots STARTED: consecutive passes on distinct snapshots (distinct name AND distinct data inputs) with one
    Mentat HEAD, one image digest and one volatile list.  A failure empties the streak; a pass under another HEAD, digest or list restarts it at
    that pass; a pass that repeats a snapshot or its inputs neither counts nor resets; any other status (aborted) is ignored.  A job in `repeat_ok`
    (refit-parity: its inputs are pinned by sha256, Paul 2026-10-18) may repeat its inputs, never its snapshot name."""
    repeat_ok = repeat_ok_jobs(load_volatile()) if repeat_ok is None else set(repeat_ok)
    out = {}
    for job in JOBS:
        mine = sorted((r for r in results if r["job"] == job), key=lambda r: (r["started_utc"], r["snapshot"]))
        streak, history = [], []
        for r in mine:
            history.append({"snapshot": r["snapshot"], "status": r["status"], "head": r["mentat_head"][:12], "started_utc": r["started_utc"]})
            if r["status"] not in ("pass", "fail"):
                continue
            if r["status"] == "fail":
                streak = []
                continue
            if streak and ident_of(r) != ident_of(streak[0]):
                streak = []
            if any(r["snapshot"] == s["snapshot"] or (job not in repeat_ok and r["inputs_digest"] == s["inputs_digest"]) for s in streak):
                continue
            streak.append(r)
        out[job] = {"streak": len(streak), "needed": need, "repeat_ok": job in repeat_ok, "snapshots": [s["snapshot"] for s in streak],
                    "head": streak[0]["mentat_head"] if streak else None, "digest": streak[0]["image_digest"] if streak else None,
                    "volatile_sha256": streak[0].get("volatile_sha256") if streak else None,
                    "last": history[-1] if history else None, "results": len(mine)}
    return out


def ident_of(result: dict) -> tuple:
    return result["mentat_head"], result["image_digest"], result.get("volatile_sha256")


def pending_snapshots(results: Path) -> dict:
    """{job: [snapshot]} that have run (run.json in the scratch) but have no result: a compare is owed, and it might have been a failure."""
    out = {}
    folder = Path(results) / "_snapshots"
    for path in sorted(folder.glob("*__*.json")) if folder.is_dir() else []:
        job, _, snapshot = path.stem.partition("__")
        try:
            root = Path(read_json(path)["snapshot_root"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if (root / "run.json").is_file() and not result_path(results, job, snapshot).exists():
            out.setdefault(job, []).append(snapshot)
    return out


def cmd_status(args, host) -> int:
    streaks = compute_streaks(load_results(Path(args.results_dir)))
    pending = pending_snapshots(Path(args.results_dir))
    try:
        live = mentat_identity(host, args.mentat_root)
    except Refused as error:
        live = {"head": None, "digest": None, "dirty": [str(error)]}
    ready = True
    for job in JOBS:
        s = streaks[job]
        same = s["streak"] > 0 and live["head"] == s["head"] and live["digest"] == s["digest"]
        s["live_identity_matches"] = same
        s["pending"] = pending.get(job, [])
        s["list_matches"] = s["streak"] > 0 and s["volatile_sha256"] == sha256_file(VOLATILE_FILE)
        s["ready"] = s["streak"] >= s["needed"] and same and s["list_matches"] and not s["pending"] and not live["dirty"]
        ready = ready and s["ready"]
    if args.json:
        print(json.dumps({"live": {"head": live["head"], "digest": live["digest"], "dirty": len(live["dirty"])}, "jobs": streaks, "all_ready": ready}, indent=1))
        return 0
    print("Mentat HEAD %s   image %s   %s" % ((live["head"] or "?")[:12], live["digest"], "DIRTY" if live["dirty"] else "clean"))
    for job in JOBS:
        s = streaks[job]
        if s["ready"]:
            note = "READY"
        elif s["pending"]:
            note = "PENDING compare: " + ",".join(s["pending"])
        elif s["streak"] and not s["live_identity_matches"]:
            note = "live HEAD/digest differ from the streak's"
        elif s["streak"] and not s["list_matches"]:
            note = "the volatile list changed since the streak"
        else:
            note = ""
        print("%-13s %d of %d  %-24s %s %s" % (job, s["streak"], s["needed"], ",".join(s["snapshots"]) or "-",
                                                "last %s %s" % (s["last"]["snapshot"], s["last"]["status"]) if s["last"] else "no results", note))
    print("ALL FOUR READY" if ready else "not all four jobs are at %d" % STREAK_NEEDED)
    return 0


def cmd_volatile(args, host) -> int:
    spec = read_json(VOLATILE_FILE)
    problems = validate_volatile(spec)
    if args.check_cites:
        problems += check_citations(spec, args.mentat_root) if not problems else []
    for job, body in spec["jobs"].items():
        print("%s: %d mask entr%s, %d ignore" % (job, len(body.get("mask", [])), "y" if len(body.get("mask", [])) == 1 else "ies", len(body.get("ignore", []))))
        for e in body.get("mask", []):
            print("   %-40s %-48s %s" % (e["file"], ", ".join(e["locations"]), e["cite"]))
        for e in body.get("ignore", []):
            print("   ignore %-33s %-48s %s" % (e["glob"], "", e["cite"]))
        for e in body.get("explained", []):
            print("   EXPLAINED %-30s %-48s %s" % (e["file"], ", ".join(e["locations"]), e["cite"]))
        if body.get("repeat_ok"):
            print("   repeat runs on the same inputs count: " + body["repeat_ruling"][:90])
        if body.get("a_exit_explained"):
            print("   A exit %d accepted only if the log names exactly %s" % (body["a_exit_explained"]["exit"], ", ".join(body["a_exit_explained"]["failures"])))
    for p in problems:
        print("PROBLEM: " + p)
    print("ok" if not problems else "%d problem(s)" % len(problems))
    return 1 if problems else 0


# ------------------------------------------------------------------------------------------------------------------ main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mentat-root", default=str(DEFAULT_MENTAT), help="the Mentat checkout (read only)")
    p.add_argument("--results-dir", default=str(results_default()), help="one JSON per job x snapshot, written once (default: $MELANGE_PARITY_DIR or C:\\melange-data\\compute_parity, plus \\mentat)")
    p.add_argument("--min-free-gb", type=int, default=FLOOR_GB, help="free-space floor; it can only be raised (the floor is %d GB)" % FLOOR_GB)
    sub = p.add_subparsers(dest="command", required=True)

    def common(s):
        s.add_argument("--job", required=True, choices=JOBS)
        s.add_argument("--snapshot", required=True, help="a name used once per job, e.g. S1")
        s.add_argument("--scratch", help="the scratch folder: a plain path off OneDrive and the temp folder (later commands find a frozen snapshot without it)")
    f = sub.add_parser("freeze", help="copy the job's read set into DIR/S/J/inputs with a sha256 manifest")
    common(f)
    f.add_argument("--expect-head", help="refuse unless the Mentat HEAD starts with this")
    f.add_argument("--pocket-data", help="pocket-build: the pocket-data.json snapshot to inline")
    pl = sub.add_parser("plan", help="print the exact A and B command lines; runs nothing")
    common(pl)
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(allow_container=(), execute=False)
    r = sub.add_parser("run", help="A then B, sequentially (needs --execute)")
    common(r)
    r.add_argument("--execute", action="store_true")
    r.add_argument("--allow-container", action="append", default=[], metavar="NAME", help="a running container that is known to be light")
    r.add_argument("--allow-host-load", action="store_true", help="go ahead although a refresh, the pipeline, an Rscript or another parity run is running")
    r.add_argument("--continue-after-a-failure", action="store_true", help="still run B when the Windows reference failed (default: stop, record the failure)")
    r.add_argument("--json", action="store_true")
    c = sub.add_parser("compare", help="Mentat's compute_parity.py compare with the static mask; writes the result; deletes the scratch")
    common(c)
    c.add_argument("--keep", action="store_true", help="keep the scratch copies")
    c.add_argument("--python", help="interpreter for compute_parity.py (default: Mentat's venv; needs openpyxl only for workbooks)")
    s = sub.add_parser("status", help="the streak per job")
    s.add_argument("--json", action="store_true")
    v = sub.add_parser("volatile", help="show and validate the fixed list")
    v.add_argument("--check-cites", action="store_true", help="prove every cited Mentat file:line against the checkout")
    return p


def main(argv=None, host=None) -> int:
    args = build_parser().parse_args(argv)
    host = host or SystemHost()
    try:
        if args.command == "freeze":
            if not args.scratch:
                raise Refused("freeze needs --scratch DIR")
            return cmd_freeze(args, host)
        handler = {"plan": cmd_plan, "run": cmd_run, "compare": cmd_compare, "status": cmd_status, "volatile": cmd_volatile}[args.command]
        return handler(args, host)
    except Refused as error:
        print("REFUSED: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

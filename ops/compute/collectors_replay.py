#!/usr/bin/env python3
"""Collectors replay test (D-180): prove a collector in the compute-collector container writes what the Windows run writes,
from the exact responses the Windows run saw. Writes audits/compute_parity/collectors_replay.json, the evidence
`cutover.py set collect container` reads (result PASS).

    python ops/compute/collectors_replay.py run --date 2025-09-02 --glassbox-dir <events dir> [--execute]
    python ops/compute/collectors_replay.py stage --src social_media.db --dest staged.db
    python ops/compute/collectors_replay.py keyenv --service collect|llm

`run` without --execute prints the plan (both commands, every path, the free disk) and runs nothing. With --execute:
  1. freeze   the code (http_replay.py, paths.py, the collector: nothing else, so no key file rides along) and the collector's
              read set into scratch roots; stage social_media.db into A1 through the SQLite backup API (a consistent single file,
              -wal and -shm folded in, journal mode DELETE); drop the test date from that COPY so the collector must fetch it;
              copy A1 to B and prove both roots are byte-identical to one manifest (chain_parity snapshot / verify);
  2. A1       Windows: http_replay.py record -- fetch_congressional_record.py crec --since D --until D, in a clean environment
              (chain_parity.ENV_ALLOW), on the scratch local dir and the given Glass Box dir; one request (the keyless CREC zip);
  3. B        container: the `replay` profile of collectors.compose.yaml (network none, tree read-only, no secret) replays the cassette;
  4. compare  exit codes; stdout (keep-awake lines masked); every table of social_media.db by row count and sha256 of its rows
              (cr_fetch_log.ts masked), plus the schema and integrity_check; every other file the run changed. Gates G0-G5;
  5. report   audits/compute_parity/collectors_replay.json in the chain_parity report shape ("result" PASS / FAIL / INCONCLUSIVE);
  6. delete   the scratch copies, the cassette and the staged database (--keep to inspect them).
It never writes the live %LOCALAPPDATA%\\Melange or the live social_media.db: the database is read once, by the backup API.
INCONCLUSIVE (exit 3): everything agreed but the date produced no speeches (a non-session day), so nothing was proven.
Exit codes: 0 PASS; 1 a gate failed; 2 usage or I/O error; 3 INCONCLUSIVE.

`keyenv` prints GOVINFO_KEY_FILE_HOST / FEC_DATA_GOV_FILE_HOST / CLAUDE_CREDENTIALS_FILE_HOST as paths, resolved from where the Windows
collectors look; it checks the files exist and never opens them. The live `collect` and `llm` profiles of collectors.compose.yaml mount
them read-only (compose `secrets:`, `${VAR:?}`: an unset variable stops the run). The replay needs none, so `run` points them at an empty file.
Stdlib only, plus chain_parity.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chain_parity as cp  # noqa: E402

REPO = cp.REPO_ROOT
COLLECTOR = "fec/collect/fetch_congressional_record.py"   # CREC: keyless zip per date, writes social_media.db (speeches, cr_fetch_log)
CODE_FILES = ("ops/compute/http_replay.py", "fec/paths.py", COLLECTOR)   # the collector imports paths and the standard library only
READ_SET = ("data/engine/congestion.json", "votes/HSall_members.csv")   # what the collector reads from MONEYBALL_ROOT besides the database
DB_NAME, DB_GLOB = "social_media.db", "social_media.db*"
COMPOSE = "ops/compute/collectors.compose.yaml"
DB_MASK = {"cr_fetch_log": ("ts",)}   # datetime.now() of the run: the one volatile column the collector writes
STDOUT_MASK = ("keep-awake",)         # Windows holds a wake lock (ctypes.windll); Linux prints "unavailable"
REPORT_NAME = "collectors_replay.json"
SERVICE_KEY_VARS = {"collect": ("GOVINFO_KEY_FILE_HOST", "FEC_DATA_GOV_FILE_HOST"), "llm": ("CLAUDE_CREDENTIALS_FILE_HOST",)}
ALL_KEY_VARS = ("GOVINFO_KEY_FILE_HOST", "FEC_DATA_GOV_FILE_HOST", "CLAUDE_CREDENTIALS_FILE_HOST")
KEY_DEFAULTS = {   # where the Windows collectors look; FEC has none (see key_files)
    "GOVINFO_KEY_FILE_HOST": lambda tree, home: Path(tree) / "fec" / "govinfo_key.txt",            # fetch_congressional_record.py:33
    "CLAUDE_CREDENTIALS_FILE_HOST": lambda tree, home: Path(home) / ".claude" / ".credentials.json",   # the CLI login
}
A1_TIMEOUT, B_TIMEOUT = 1800, 3600
FREE_FACTOR = 2.5   # free disk needed, in multiples of the database, on top of 1 GiB: A1 + B + the staging


# --------------------------------------------------------------------------------------- keys (paths only)

def key_files(service: str, environ=None, tree=REPO, home=None) -> dict:
    """{VAR: path} for the key-file variables `service` needs. A variable already in the environment wins; otherwise the default
    location of the Windows collectors. Each must be an existing file; none is opened. Fails closed, naming every problem.
    FEC_DATA_GOV_FILE_HOST has no default: the Windows collector reads FEC_API_KEY or data_gov.keys[0] of API Keys\\keys.json, but a
    compose secret is one value per file (the entrypoint exports the whole file as FEC_API_KEY), and mounting keys.json would also hand the
    container the LDA and Anthropic keys. It must name a single-line key file (the job wrapper writes one per run and deletes it)."""
    environ = os.environ if environ is None else environ
    out, problems = {}, []
    for var in SERVICE_KEY_VARS[service]:
        given = (environ.get(var) or "").strip()
        default = KEY_DEFAULTS.get(var)
        path = Path(given) if given else (default(tree, home or Path.home()) if default else None)
        if path is None:
            problems.append(f"{var} is unset and has no default (it must name a single-line key file)")
        elif not path.is_file():
            problems.append(f"{var}={path} is not an existing file")
        else:
            out[var] = str(path)
    if problems:
        raise cp.Usage("the %s service needs its key files: %s" % (service, "; ".join(problems)))
    return out


# ------------------------------------------------------------------------------- social_media.db staging

def stage_db(src, dest) -> dict:
    """A consistent single-file copy of a (WAL) SQLite database: the online backup API from a read-only connection, then
    wal_checkpoint(TRUNCATE), journal_mode DELETE (no -wal/-shm is needed to read the copy) and integrity_check. Refuses an existing dest."""
    src, dest = Path(src), Path(dest)
    if not src.is_file():
        raise cp.Usage(f"{src} is not a file")
    if any(Path(str(dest) + s).exists() for s in ("", "-wal", "-shm", "-journal")):
        raise cp.Usage(f"refusing to overwrite: {dest} (or its -wal/-shm/-journal) exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True, timeout=60)) as source, \
            closing(sqlite3.connect(dest, timeout=60)) as target:
        source.backup(target)
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        mode = target.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        verdict = target.execute("PRAGMA integrity_check").fetchone()[0]
    if verdict != "ok" or mode != "delete":
        raise cp.Usage(f"staged copy {dest}: integrity_check {verdict!r}, journal_mode {mode!r}")
    return {"path": str(dest), "sha256": cp.sha256_file(dest), "size": dest.stat().st_size, "integrity": verdict, "journal_mode": mode}


def reset_date(db, date: str) -> dict:
    """Remove one CREC date from the staged COPY, so the collector has to fetch it (a date already stored makes no request)."""
    package = "CREC-" + date
    with closing(sqlite3.connect(db, timeout=60)) as con, con:
        try:
            speeches = con.execute("DELETE FROM speeches WHERE source='CREC' AND package=?", (package,)).rowcount
            logged = con.execute("DELETE FROM cr_fetch_log WHERE key=?", ("crec:" + package,)).rowcount
        except sqlite3.OperationalError as e:
            raise cp.Usage(f"{db} is not a social_media.db the CREC collector wrote: {e}") from e
    return {"package": package, "speeches_removed": speeches, "log_rows_removed": logged}


def table_digests(db) -> dict:
    """Content digest of every table (row count, sha256 of the rows in rowid order, DB_MASK columns left out), the schema and the
    integrity check. The checkpoint folds a leftover -wal into the file first (design 2.4). Works on a scratch copy only."""
    with closing(sqlite3.connect(db, timeout=60)) as con:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        schema = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        out = {"integrity": con.execute("PRAGMA integrity_check").fetchone()[0],
               "schema_sha256": hashlib.sha256(json.dumps(schema).encode("utf-8")).hexdigest(), "tables": {}}
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            quote = lambda ident: '"' + ident.replace('"', '""') + '"'   # noqa: E731
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({quote(name)})") if r[1] not in DB_MASK.get(name, ())]
            select = ", ".join(map(quote, cols)) or "1"
            try:
                cursor = con.execute(f"SELECT {select} FROM {quote(name)} ORDER BY rowid")
            except sqlite3.OperationalError:   # WITHOUT ROWID
                cursor = con.execute(f"SELECT {select} FROM {quote(name)}")
            h, n = hashlib.sha256(), 0
            for row in cursor:
                h.update(repr(row).encode("utf-8", "surrogatepass"))
                n += 1
            out["tables"][name] = {"rows": n, "sha256": h.hexdigest(), "masked": list(DB_MASK.get(name, ()))}
        return out


def package_rows(db, date: str) -> int:
    with closing(sqlite3.connect(db, timeout=60)) as con:
        return con.execute("SELECT COUNT(*) FROM speeches WHERE source='CREC' AND package=?", ("CREC-" + date,)).fetchone()[0]


# ---------------------------------------------------------------------------------------------- commands

class Work:
    """The scratch layout of one run, all under one directory this run creates and deletes."""

    def __init__(self, root: Path):
        self.root = root
        self.code, self.a1, self.b, self.cassette = (root / n for n in ("code", "a1", "b", "cassette"))
        self.local = root / "a1-local" / "Melange"
        self.unused, self.empty = root / "unused-lda-raw", root / "empty"
        self.seed_manifest, self.code_manifest = root / "seed.manifest.json", root / "code.manifest.json"


def collector_args(date: str) -> list[str]:
    return ["crec", "--since", date, "--until", date]


def secret_files(src) -> list[Path]:
    """Key files http_replay must scrub from the cassette (paths only; http_replay reads them). The frozen code root has none of its own."""
    # keys.json is left out on purpose: http_replay scrubs every string leaf of a .json (dates, notes), which would alter bodies it need not
    return [p for p in (Path(src) / "fec" / "govinfo_key.txt", Path(src) / "fec" / "anthropic_key.txt") if p.is_file()]


def a1_command(cfg, work: Work):
    """-> (argv, env, cwd) of the Windows record run: a clean environment (only cp.ENV_ALLOW is inherited), a scratch root, scratch local store."""
    argv = [sys.executable, str(work.code / "ops" / "compute" / "http_replay.py"), "record", str(work.cassette)]
    for file in secret_files(cfg.src):
        argv += ["--secret-file", str(file)]
    argv += ["--", str(work.code / COLLECTOR), *collector_args(cfg.date)]
    env = {"MONEYBALL_ROOT": str(work.a1), "MELANGE_LOCAL_ROOT": str(work.local), "LOCALAPPDATA": str(work.local.parent),
           "GLASSBOX_EVENTS_DIR": os.path.abspath(cfg.glassbox_dir), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
    return argv, {**{k: os.environ[k] for k in cp.ENV_ALLOW if k in os.environ}, **env}, str(work.a1)


def b_command(cfg, work: Work, name: str):
    """-> (argv, env, cwd) of the container replay: the `replay` profile (network none, tree read-only, no secret). The key-file variables
    only have to parse (compose interpolates the whole file), so they name an empty file."""
    env = {**os.environ, "IMAGE_COMPUTE_COLLECTOR": cfg.image, "MELANGE_HOST_ROOT": str(work.code),
           "GLASSBOX_HOST_EVENTS": os.path.abspath(cfg.glassbox_dir), "LDA_RAW_HOST_DIR": str(work.unused),
           "CASSETTE_HOST_DIR": str(work.cassette), "REPLAY_OUT_HOST_DIR": str(work.b), **dict.fromkeys(ALL_KEY_VARS, str(work.empty))}
    argv = ["docker", "compose", "-f", str(Path(cfg.repo) / COMPOSE), "--profile", "replay", "run", "--rm", "-T", "--name", name, "replay",
            "python", "/melange/ops/compute/http_replay.py", "replay", "/cassettes", "--", "/melange/" + COLLECTOR, *collector_args(cfg.date)]
    return argv, env, str(cfg.repo)


def _run(argv, env, cwd, timeout) -> dict:
    start = time.monotonic()
    try:
        done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout)
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as e:   # ponytail: kills the child, not its grandchildren
        code, out, err = 124, e.stdout or b"", (e.stderr or b"") + f"\ntimed out after {timeout} s".encode()
    text = lambda b: b.decode("utf-8", "replace").replace("\r\n", "\n")   # noqa: E731
    return {"exit": code, "stdout": text(out), "stderr": text(err), "seconds": round(time.monotonic() - start, 1)}


def run_a1(cfg, work: Work) -> dict:
    return _run(*a1_command(cfg, work), cfg.timeout_a1)


def run_b(cfg, work: Work) -> dict:
    name = "melange-replay-" + uuid.uuid4().hex[:10]
    argv, env, cwd = b_command(cfg, work, name)
    result = _run(argv, env, cwd, cfg.timeout_b)
    if result["exit"] == 124:   # --rm removes a container that ended; a killed client leaves one running
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
    return result


def mask_stdout(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if not line.startswith(STDOUT_MASK))


def outputs_of(root: Path, pre: dict, date: str) -> dict:
    """What a run left behind: the database digests and rows of the test date, and every other changed file (path -> sha256)."""
    changed = cp.changed_paths(pre, cp.snapshot_tree(root, [DB_GLOB]))
    return {"db": table_digests(root / DB_NAME), "rows": package_rows(root / DB_NAME, date),
            "files": {rel: (cp.sha256_file(root / rel) if how != "removed" else None) for rel, how in sorted(changed.items())}}


def judge(a1: dict, b: dict | None, facts: dict) -> tuple[str, int, list[dict]]:
    """-> (result, exit code, gates). a1 / b: the side results plus outputs_of. facts: seed problems and cassette interactions."""
    g = cp._gate
    gates = [g("G0", "preconditions: A1 and B seeded byte-identically, the cassette holds at least one interaction",
               list(facts["problems"]) + ([] if facts["interactions"] else ["the cassette has no interaction (the collector made no request)"]),
               data={"interactions": facts["interactions"]})]
    p = [f"A1 exited {a1['exit']}"] if a1["exit"] != 0 else []
    p += ["B did not run (A1 left no usable cassette)"] if b is None else [f"B exited {b['exit']}"] if b["exit"] != 0 else []
    gates.append(g("G1", "A1 and B exit 0 (B's code includes http_replay's request-set check: 3 = a miss, 4 = an unused interaction)", p))
    if b is None:
        gates += [g(n, t, ["B did not run"]) for n, t in (("G2", "stdout"), ("G3", "database"), ("G4", "files"))]
    else:
        gates.append(g("G2", "stdout equal (keep-awake lines masked)", [] if mask_stdout(a1["stdout"]) == mask_stdout(b["stdout"]) else
                       ["stdout differs: A1 %d line(s), B %d line(s)" % tuple(len(mask_stdout(s["stdout"]).splitlines()) for s in (a1, b))]))
        p, da, db = [], a1["db"], b["db"]
        for lbl, d in (("A1", da), ("B", db)):
            if d["integrity"] != "ok":
                p.append(f"{lbl}: integrity_check {d['integrity']!r}")
        if da["schema_sha256"] != db["schema_sha256"]:
            p.append("schema differs")
        for t in sorted(set(da["tables"]) | set(db["tables"])):
            x, y = da["tables"].get(t), db["tables"].get(t)
            if x != y:
                p.append(f"table {t}: A1 {x and (x['rows'], x['sha256'][:12])} vs B {y and (y['rows'], y['sha256'][:12])}")
        gates.append(g("G3", "social_media.db: schema and every table's rows equal (cr_fetch_log.ts masked), integrity ok", p,
                       data={"tables": {t: v["rows"] for t, v in da["tables"].items()}}))
        p = ([f"changed files differ: A1 {sorted(a1['files'])} vs B {sorted(b['files'])}"] if set(a1["files"]) != set(b["files"]) else
             [f"{rel}: sha256 differs" for rel in sorted(a1["files"]) if a1["files"][rel] != b["files"].get(rel)])
        gates.append(g("G4", "every other changed file equal", p, data={"changed": sorted(a1["files"])}))
    gates.append(g("G5", "non-vacuous: the test date stored at least one speech", [] if a1.get("rows") else
                   ["the date produced no speeches (a non-session day?): choose a day the House or Senate met"]))
    failed = [x["name"] for x in gates if x["status"] == "FAIL"]
    if failed == ["G5"]:
        return "INCONCLUSIVE", 3, gates
    return ("FAIL", 1, gates) if failed else ("PASS", 0, gates)


def _rmtree(path: Path) -> None:
    def writable(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=writable)


def _list_file(work: Work, name: str, lines) -> str:
    path = work.root / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return str(path)


def orchestrate(cfg, run_a1_=run_a1, run_b_=run_b) -> dict:
    """The whole run; the two runners are parameters so the tests can stand in for Windows and Docker."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = Work(Path(cfg.work_parent) / f"collectors_replay_{stamp}_{uuid.uuid4().hex[:6]}")
    if work.root.exists():
        raise cp.Usage(f"{work.root} exists")
    work.root.mkdir()
    try:
        cp.require_scratch_dirs(work.local, cfg.glassbox_dir)
        work.local.mkdir(parents=True)
        work.unused.mkdir()
        work.empty.write_bytes(b"")
        # 1. freeze code and read set, stage the database, drop the date, copy A1 -> B, prove both identical
        for dest, lines, manifest in ((work.code, CODE_FILES, work.code_manifest), (work.a1, READ_SET, work.root / "a1.freeze.manifest.json")):
            if cp.main(["freeze", "--src", str(cfg.src), "--dest", str(dest), "--list", _list_file(work, dest.name + ".list", lines),
                        "--manifest", str(manifest)]) != 0:
                raise cp.Usage(f"could not freeze {', '.join(lines)} from {cfg.src}")
        staged = stage_db(Path(cfg.src) / DB_NAME, work.a1 / DB_NAME)
        reset = reset_date(work.a1 / DB_NAME, cfg.date)
        if cp.main(["snapshot", "--root", str(work.a1), "--out", str(work.seed_manifest)]) != 0:
            raise cp.Usage("could not take the seed manifest")
        shutil.copytree(work.a1, work.b)
        problems = [f"{lbl}: not byte-identical to the seed manifest" for lbl, root in (("A1", work.a1), ("B", work.b))
                    if cp.main(["verify", "--root", str(root), "--manifest", str(work.seed_manifest)]) != 0]
        pre = cp.snapshot_tree(work.a1, [DB_GLOB])
        # 2-4. record, replay, compare
        a1 = run_a1_(cfg, work)
        cassette = work.cassette / "cassette.jsonl"
        interactions = len(cassette.read_text(encoding="utf-8").splitlines()) if cassette.is_file() else 0
        if a1["exit"] == 0 and interactions:
            a1.update(outputs_of(work.a1, pre, cfg.date))
            b = run_b_(cfg, work)
            b.update(outputs_of(work.b, pre, cfg.date))
        else:
            a1.setdefault("rows", 0)
            b = None
        result, code, gates = judge(a1, b, {"problems": problems, "interactions": interactions})
        side = lambda s: {k: (v if k != "stderr" else v[-1500:]) for k, v in s.items() if k in ("exit", "seconds", "stderr")}   # noqa: E731
        report = {
            "schema": 1, "result": result, "exit_code": code, "failed_gates": [x["name"] for x in gates if x["status"] == "FAIL"],
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "inputs": {"date": cfg.date, "collector": [COLLECTOR, *collector_args(cfg.date)], "src": str(cfg.src), "image": cfg.image,
                       "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version, "code_sha256": json.loads(work.code_manifest.read_text("utf-8")),
                       "staged_db": {k: staged[k] for k in ("sha256", "size", "journal_mode", "integrity")}, "reset": reset,
                       "cassette": {"interactions": interactions, "sha256": cp.sha256_file(cassette) if cassette.is_file() else None},
                       "masks": {"db": {t: list(c) for t, c in DB_MASK.items()}, "stdout_prefix": list(STDOUT_MASK)}},
            "gates": gates, "sides": {"A1": side(a1), **({"B": side(b)} if b else {})},
            "db": {"A1": a1.get("db"), **({"B": b["db"]} if b else {})}, "speeches_for_date": a1.get("rows"),
        }
        cp.write_json(cfg.report, report)
        return report
    finally:
        if not cfg.keep:
            _rmtree(work.root)
        else:
            print(f"kept {work.root}")


# ----------------------------------------------------------------------------------------------------- cli

def _resolve_image(args) -> str:
    sys.path.insert(0, str(REPO / "fec"))
    import compute_engine as CE   # lazy: the module needs fec/, which the platform repo does not carry
    try:
        return CE.image("compute-collector", "IMAGE_COMPUTE_COLLECTOR", root=REPO, environ={**os.environ, **({"IMAGE_COMPUTE_COLLECTOR": args.image} if args.image else {})})
    except CE.EngineError as e:
        raise cp.Usage(str(e)) from e


def cmd_run(a) -> int:
    try:
        date = dt.date.fromisoformat(a.date)
    except ValueError as e:
        raise cp.Usage(f"--date must be YYYY-MM-DD: {e}") from e
    if date >= dt.date.today():
        raise cp.Usage("--date must be a past day: today's Record may not be published yet")
    src, parent = Path(a.src).resolve(), Path(a.work_parent).resolve()
    if not (src / DB_NAME).is_file():
        raise cp.Usage(f"{src / DB_NAME} does not exist")
    if not parent.is_dir() or not Path(a.glassbox_dir).is_dir():
        raise cp.Usage("--work-parent and --glassbox-dir must be existing directories")
    cfg = argparse.Namespace(date=a.date, src=src, repo=REPO, glassbox_dir=os.path.abspath(a.glassbox_dir), work_parent=parent, keep=a.keep,
                             report=Path(a.report) if a.report else Path(os.environ.get("MELANGE_PARITY_DIR") or REPO / "audits" / "compute_parity") / REPORT_NAME,
                             image=_resolve_image(a), timeout_a1=a.timeout_a1, timeout_b=a.timeout_b)   # same default as cutover.parity_dir()
    need = int((src / DB_NAME).stat().st_size * FREE_FACTOR) + (1 << 30)
    free = shutil.disk_usage(parent).free
    print("# collectors replay, " + ("running it now" if a.execute else "printed only, nothing executed"))
    work = Work(parent / "collectors_replay_<utc stamp>")
    for label, (argv, env, cwd) in (("A1 (Windows record)", a1_command(cfg, work)), ("B (container replay)", b_command(cfg, work, "melange-replay-<id>"))):
        print(f"\n[{label}] cwd={cwd}")
        for k in sorted(env):
            if os.environ.get(k) != env[k] and k not in cp.ENV_ALLOW:   # what this command adds to the caller's environment
                print(f"  {k}={env[k]}")
        print("  run=" + subprocess.list2cmdline(argv))
    print(f"\nreport={cfg.report}\nfree disk {free >> 20} MiB, needed about {need >> 20} MiB")
    if not a.execute:
        return 0
    if free < need:
        raise cp.Usage(f"not enough free disk under {parent}: {free >> 20} MiB < {need >> 20} MiB")
    if not shutil.which("docker"):
        raise cp.Usage("docker is not on PATH")
    probe = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=120)
    if probe.returncode != 0 or not probe.stdout.strip():   # Desktop's "unable to start" exits 0 with an empty ServerVersion
        raise cp.Usage("Docker Desktop is not running: " + probe.stderr.strip()[-200:])
    report = orchestrate(cfg)
    for gate in report["gates"]:
        print(f"{gate['name']} {gate['status']}  {gate['title']}")
        for line in gate["detail"]:
            print(f"    {line}")
    print(f"RESULT: {report['result']} (exit {report['exit_code']}); report {cfg.report}")
    return report["exit_code"]


def cmd_stage(a) -> int:
    info = stage_db(a.src, a.dest)
    print(f"staged {info['size']} byte(s), sha256 {info['sha256']}, journal_mode {info['journal_mode']}, integrity {info['integrity']} -> {a.dest}")
    return 0


def cmd_keyenv(a) -> int:
    for var, path in key_files(a.service).items():
        print(f"{var}={path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="collectors_replay", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run", help="record on Windows, replay in the container, compare, write the evidence")
    s.set_defaults(func=cmd_run)
    s.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="a past day the House or Senate met (one CREC package)")
    s.add_argument("--glassbox-dir", required=True, metavar="DIR", help="Glass Box events folder (required: the default is the live one)")
    s.add_argument("--src", default=str(REPO), metavar="TREE", help="the Melange tree: code, read set, social_media.db (default: this one)")
    s.add_argument("--work-parent", default=tempfile.gettempdir(), metavar="DIR", help="where the scratch directory is created (not OneDrive)")
    s.add_argument("--image", metavar="REF", help="compute-collector digest reference (default: IMAGE_COMPUTE_COLLECTOR, then the verified current release)")
    s.add_argument("--report", metavar="OUT.json", help="default: $MELANGE_PARITY_DIR or audits/compute_parity/%s" % REPORT_NAME)
    s.add_argument("--timeout-a1", type=float, default=A1_TIMEOUT, metavar="SECONDS")
    s.add_argument("--timeout-b", type=float, default=B_TIMEOUT, metavar="SECONDS")
    s.add_argument("--keep", action="store_true", help="do not delete the scratch directory afterwards")
    s.add_argument("--execute", action="store_true", help="run it; without this the plan is only printed")
    s = sub.add_parser("stage", help="copy a WAL database to a consistent single file (backup API)")
    s.set_defaults(func=cmd_stage)
    s.add_argument("--src", required=True)
    s.add_argument("--dest", required=True)
    s = sub.add_parser("keyenv", help="print the key-file path variables for a compose service (paths only)")
    s.set_defaults(func=cmd_keyenv)
    s.add_argument("--service", required=True, choices=sorted(SERVICE_KEY_VARS))
    return p


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    try:
        return args.func(args)
    except (cp.Usage, OSError, sqlite3.Error) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except AttributeError:
        pass
    sys.exit(main())

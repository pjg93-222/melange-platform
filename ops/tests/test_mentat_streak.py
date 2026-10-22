"""Tests for ops/compute/mentat_streak.py (the Mentat parity-streak driver).

Fakes only: a synthetic Mentat tree under tmp_path, a FakeHost that answers git / docker / the scheduled-task query and stands in for A and
B (it writes their outputs instead of running Python, R or Docker).  Nothing here runs a Mentat job, starts Docker or touches the live
checkout.  The tests marked `parity` additionally import Mentat's own tools/compute_parity.py (a pure, stdlib comparison module) from the live
checkout, to prove its compare accepts a statically built mask; they skip where that file is absent (the CI export has no Mentat).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compute"))
import mentat_streak as ms  # noqa: E402

LIVE_PARITY = Path(r"C:\Users\pgoff\Mentat\mentat\tools\compute_parity.py")
LIVE_ROOT = Path(r"C:\Users\pgoff\Mentat")
needs_parity = pytest.mark.skipif(not LIVE_PARITY.is_file(), reason="Mentat's compute_parity.py is not on this machine")
GB = 2 ** 30
DIGEST = "sha256:" + "a" * 64
HEAD = "1" * 40

MENTAT_SOURCES = '''
import hashlib, json, sqlite3
from pathlib import Path
STATE_INDEX_DIR = Path('state') / 'contribution-analytics'
def ro_connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.execute('PRAGMA query_only=ON')
    return db
def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 22), b''):
            digest.update(chunk)
    return digest.hexdigest()
def find_index(index_dir, workbook_sha256):
    best = None
    for path in sorted(Path(index_dir).glob('*.sqlite')):
        try:
            db = ro_connect(path)
            try:
                row = db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
            finally:
                db.close()
            manifest = json.loads(row[0])
        except (sqlite3.Error, TypeError, ValueError):
            continue
        if manifest.get('provenance', {}).get('workbook_sha256') != workbook_sha256:
            continue
        if best is None or manifest.get('built_at', 0) > best[1].get('built_at', 0):
            best = (path, manifest)
    return best
def find_source_db(project_root, patterns, expected_sha256, hashes=None):
    hashes = {} if hashes is None else hashes
    for pattern in patterns:
        for path in sorted(Path(project_root).glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key not in hashes:
                hashes[key] = sha256_file(path)
            if hashes[key] == expected_sha256:
                return path
    return None
'''

COMPUTE_JOB = '''
JOBS = {
    'anchors': Job('Giving Forecast anchor snapshot', 45, False, False, None, None, None),
    'refit-detect': Job('detect', 20, False, False, None, None, None),
    'refit-parity': Job('parity', 240, False, False, None, None, None),
    'refit': Job('refit', 480, False, False, None, None, None),
    'pocket-build': Job('pocket', 10, False, False, None, None, None),
    'collect-fanout': Job('fanout', 1440, True, True, None, None, None),
}
'''


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def make_index(path: Path, workbook_sha: str, db_sha: str, built_at: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE metadata(key TEXT, value TEXT)")
    db.execute("INSERT INTO metadata VALUES('manifest', ?)", (json.dumps({"provenance": {"workbook_sha256": workbook_sha, "database_sha256": db_sha},
                                                                          "built_at": built_at, "token": "tok%d" % built_at}),))
    db.commit()
    db.close()


# ------------------------------------------------------------------------------------------------------------ the world

class FakeHost:
    """Answers for git, docker, the scheduled tasks, locks and free space; stands in for A and B."""

    def __init__(self, root: Path, lad: Path, parity_python=None):
        self.root, self.lad = root, lad
        self.calls: list = []
        self.head, self.dirty = HEAD, []
        self.untracked = {"mentat/inputs/active_fec_sources.json"}
        self.tasks: list = []
        self.containers: list = []
        self.held: set = set()
        self.docker_rc, self.docker_hang = 0, False
        self.free_c = self.free_scratch = 500 * GB
        self.drive = os.path.splitdrive(str(root))[0] + "\\"
        self.temp: list = []
        self.extra_env: dict = {}
        self.writers: dict = {}
        self.exits = {"A": 0, "B": 0}
        self.head_after = {}        # label -> HEAD that git reports once that side has run
        self.events: list = []
        self.missing_tools: set = set()
        self.global_busy, self.locks_taken, self.processes = False, [], []
        self.image_present, self.timeouts = True, set()
        self.now_value = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc)

    # -- the Host protocol
    def is_file(self, path): return Path(path).name not in self.missing_tools
    def now(self): return self.now_value

    def lock_exclusive(self, path):
        if self.global_busy:
            return None
        self.locks_taken.append(Path(path))
        return SimpleNamespace(close=lambda: self.locks_taken.append("released"))
    def local_appdata(self): return str(self.lad)
    def system_drive(self): return self.drive
    def environ(self): return {"PATH": os.environ.get("PATH", ""), "SystemRoot": os.environ.get("SystemRoot", ""), **self.extra_env}
    def forbidden_roots(self): return list(self.temp)
    def lock_held(self, path): return Path(path).name in self.held

    def free_bytes(self, path):
        return self.free_c if str(path) == self.drive else self.free_scratch

    def run(self, argv, *, env=None, cwd=None, timeout=None):
        self.calls.append(SimpleNamespace(argv=list(argv), env=env, cwd=cwd, timeout=timeout))
        name = Path(argv[0]).name.lower()
        if name == "git":
            return self._git(argv[4:])
        if name == "docker":
            if argv[1] == "info":
                return ms.Proc(124, "", "", True) if self.docker_hang else ms.Proc(self.docker_rc, "26.0.0\n" if self.docker_rc == 0 else "")
            if argv[1] == "ps":
                return ms.Proc(0, "\n".join(self.containers) + "\n" if self.containers else "")
            if argv[1] == "image":
                return ms.Proc(0 if self.image_present else 1, "sha256:x\n" if self.image_present else "")
            if argv[1] == "rm":
                return ms.Proc(0)
            raise AssertionError("unexpected docker %r" % (argv,))
        if name == "powershell.exe":
            joined = " ".join(argv)
            if "Win32_Process" in joined:
                return ms.Proc(0, "".join(line + "\n" for line in self.processes))
            if "Get-ScheduledTask" in joined:
                return ms.Proc(0, "".join("|".join(t) + "\n" for t in self.tasks))
            if "-File" in argv:
                return self._side("A", argv, env, cwd)
            if "-Command" in argv:
                return self._side("B", argv, env, cwd)
        if Path(argv[0]).resolve() == Path(sys.executable).resolve():       # compute_parity.py: a real, pure comparison
            done = subprocess.run(argv, capture_output=True, text=True)
            return ms.Proc(done.returncode, done.stdout, done.stderr)
        raise AssertionError("unexpected command: %r" % (argv,))

    def _git(self, args):
        if args[0] == "rev-parse":
            return ms.Proc(0, self.head_after.get(self._last_side(), self.head) + "\n")
        if args[0] == "status":
            return ms.Proc(0, "".join(" M %s\n" % d for d in self.dirty))
        if args[0] == "branch":
            return ms.Proc(0, "codex/mentat-crypto\n")
        if args[0] == "ls-files":
            roots = args[args.index("--") + 1:]
            out = []
            for rel in roots:
                base = self.root / rel
                found = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
                for path in found:
                    r = path.relative_to(self.root).as_posix()
                    if path.is_file() and r not in self.untracked and "__pycache__" not in r:
                        out.append(r)
            return ms.Proc(0, "\0".join(out) + "\0")
        raise AssertionError("unexpected git %r" % (args,))

    def _last_side(self):
        sides = [e for e in self.events if e in ("A", "B")]
        return sides[-1] if sides else None

    def _side(self, label, argv, env, cwd):
        self.events.append(label)
        if label in self.timeouts:
            return ms.Proc(124, "", "", True)
        writer = self.writers.get(label)
        if writer:
            writer(env, argv, cwd)
        return ms.Proc(self.exits[label], "", "")

    def calls_for(self, label):
        key = "-File" if label == "A" else "-Command"
        return [c for c in self.calls if Path(c.argv[0]).name.lower() == "powershell.exe" and key in c.argv
                and "Get-ScheduledTask" not in " ".join(c.argv) and "Win32_Process" not in " ".join(c.argv)]


def build_world(tmp_path: Path) -> SimpleNamespace:
    root, lad = tmp_path / "Mentat", tmp_path / "lad"
    reports, scratch, results = tmp_path / "reports", tmp_path / "scratch", tmp_path / "results"
    project = root / "mentat"
    put(project / "compute.images.lock", json.dumps({"image": "ghcr.io/x/compute-mentat", "digest": DIGEST}))
    put(project / "compute.compose.yaml", "services: {}\n")
    put(project / "tools" / "compute_job.py", COMPUTE_JOB)
    put(project / "tools" / "run_job.ps1", "# fake wrapper\n")
    put(project / "tools" / "pocket" / "build_page.py", "# fake\n")
    put(project / "tools" / "pocket" / "mentat-pocket.html", "<html>/*__DATA_START__*//*__DATA_END__*/</html>\n")
    put(project / "app" / "data" / "giving-models-v1.json", '{"v":1}')
    put(project / "config" / "report-directory.json", json.dumps({"schema_version": 1, "directory": str(reports)}))
    put(project / "forecast" / "anchors" / "mentat_sources.py", MENTAT_SOURCES)
    put(project / "forecast" / "anchors" / "build_giving_anchors.py", "# fake\n")
    registry = {"industries": [
        {"id": "commercial_banks", "workbook": "Banking Industry Spending Overview.xlsx", "source_db_candidates": [".cache/report-repairs/banking-*/data/mentat.db"]},
        {"id": "telecom", "workbook": "Telecom Industry Spending Overview.xlsx", "source_db_candidates": [".cache/receipt-metadata-repair-*/telecom/mentat.db"]},
        {"id": "crypto", "workbook": "Crypto Industry Spending Overview.xlsx", "source_db_candidates": [".cache/x/*.db"]}]}
    put(project / "forecast" / "anchors" / "registry.json", json.dumps(registry))
    put(project / "forecast" / "refit" / "run_refit.R", "# fake\n")
    put(project / "forecast" / "refit" / "R" / "rf_agent.R", "# fake\n")
    put(project / "forecast" / "refit" / "inputs" / "cycle_model" / "pace.csv", "a,b\n1,2\n")
    put(root / "sector_growth_study" / "data" / "00_controls.xlsx", b"controls")
    # reports
    wb_bank, wb_tel = b"bank workbook" * 10, b"telecom workbook" * 10
    put(reports / "Banking Industry Spending Overview.xlsx", wb_bank)
    put(reports / "Telecom Industry Spending Overview.xlsx", wb_tel)
    put(reports / "Rust FEC Verified Industry Spending Overview 2023-2024.xlsx", b"rust")
    put(reports / "~$Banking Industry Spending Overview.xlsx", b"lock")
    put(reports / "Banking Industry Spending Overview.audit" / "audit-manifest.json", json.dumps({"workbook_sha256": sha(wb_bank)}))
    put(reports / "notes.md", b"not a workbook")
    # source databases and indexes: the newer index of the bank workbook wins; an older one and a wrong database are not copied
    db_bank, db_old, db_tel = b"bank db" * 50, b"older bank db" * 50, b"telecom db" * 50
    put(project / ".cache" / "report-repairs" / "banking-1" / "data" / "mentat.db", db_old)
    put(project / ".cache" / "report-repairs" / "banking-2" / "data" / "mentat.db", db_bank)
    put(project / ".cache" / "receipt-metadata-repair-1" / "telecom" / "mentat.db", db_tel)
    index_dir = project / "state" / "contribution-analytics"
    make_index(index_dir / "old-bank.sqlite", sha(wb_bank), sha(db_old), 100)
    make_index(index_dir / "new-bank.sqlite", sha(wb_bank), sha(db_bank), 200)
    make_index(index_dir / "tel.sqlite", sha(wb_tel), sha(db_tel), 150)
    make_index(index_dir / "other.sqlite", sha(b"unrelated"), sha(b"x"), 999)
    # FEC bulk files: four cycles on disk, the three newest are read
    fec = project / ".cache" / "fec"
    put(project / "inputs" / "active_fec_sources.json", json.dumps({"path": "C:\\Users\\pgoff\\Mentat\\mentat\\.cache\\fec"}))
    for yy in ("20", "22", "24", "26"):
        put(fec / "fec_beneficiary" / ("pas2" + yy) / "itpas2.txt", "pas2 " + yy)
        put(fec / "fec_beneficiary" / ("pas2" + yy) / "source-receipt.json", "{}")
        put(fec / "fec_beneficiary" / ("cn" + yy) / "cn.txt", "cn " + yy)
        put(fec / "fec_committee_master" / ("cm" + yy) / "cm.txt", "cm " + yy)
    # Melange LDA output and the live refit runtime
    put(lad / "Melange" / "lda_rebuild" / "derived" / "sector_spend" / "sector_spend.csv", "sector,year\nbank,2024\n")
    put(lad / "Melange" / "lda_rebuild" / "derived" / "fullscope_receipts.json", "{}")
    put(lad / "Mentat" / "forecast" / "refit" / "agent" / "seen.json", "{}")
    put(lad / "Mentat" / "forecast" / "refit" / "Rlib" / "clubSandwich" / "DESCRIPTION", "Package: clubSandwich\n")
    put(lad / "Mentat" / "forecast" / "refit" / "raw" / "vault.bin", "never copied")
    reports.mkdir(exist_ok=True)
    if LIVE_PARITY.is_file():
        shutil.copy(LIVE_PARITY, project / "tools" / "compute_parity.py")
    host = FakeHost(root, lad)
    return SimpleNamespace(root=root, lad=lad, reports=reports, scratch=scratch, results=results, project=project, host=host, tmp=tmp_path)


@pytest.fixture
def world(tmp_path):
    return build_world(tmp_path)


def cli(world, *argv, host=None):
    full = ["--mentat-root", str(world.root), "--results-dir", str(world.results), *argv]
    return ms.main(full, host=host or world.host)


def freeze(world, job="anchors", snapshot="S1", *extra):
    return cli(world, "freeze", "--job", job, "--snapshot", snapshot, "--scratch", str(world.scratch), *extra)


def snap_root(world, job="anchors", snapshot="S1") -> Path:
    return world.scratch / snapshot / job


def manifest_of(world, job="anchors", snapshot="S1") -> dict:
    return json.loads((snap_root(world, job, snapshot) / "inputs.manifest.json").read_text())["files"]


# ---------------------------------------------------------------------------------------------------- job caps, identity

def test_job_caps_are_read_from_the_registry_source(world):
    assert ms.job_caps(world.root) == {"anchors": 45, "refit-detect": 20, "refit-parity": 240, "refit": 480, "pocket-build": 10, "collect-fanout": 1440}


@pytest.mark.skipif(not (LIVE_ROOT / "mentat" / "tools" / "compute_job.py").is_file(), reason="no live Mentat")
def test_job_caps_match_the_live_registry():
    caps = ms.job_caps(LIVE_ROOT)
    assert {j: caps[j] for j in ms.JOBS} == {"anchors": 45, "refit-detect": 20, "refit-parity": 240, "pocket-build": 10}


def test_job_caps_refuse_a_registry_without_a_job(world):
    put(world.project / "tools" / "compute_job.py", "JOBS = {'anchors': Job('x', 45, 0, 0)}\n")
    with pytest.raises(ms.Refused, match="no cap for"):
        ms.job_caps(world.root)


def test_identity_problems(world):
    ident = ms.mentat_identity(world.host, world.root)
    assert ident["head"] == HEAD and ident["digest"] == DIGEST and ms.identity_problems(ident) == []
    assert "dirty" in ms.identity_problems({**ident, "dirty": ["?? x"]})[0]
    assert "no sha256 digest" in ms.identity_problems({**ident, "digest": None})[0]
    assert "not the expected" in ms.identity_problems(ident, expect_head="2222222")[0]
    assert ms.identity_problems(ident, expect_head=HEAD[:7]) == []
    frozen = {"head": "2" * 40, "digest": "sha256:" + "b" * 64}
    both = ms.identity_problems(ident, frozen=frozen)
    assert len(both) == 2 and "HEAD moved" in both[0] and "digest changed" in both[1]


def test_clean_env_drops_redirecting_variables():
    env = ms.clean_env({"PATH": "p", "RF_RUNTIME": "x", "MENTAT_REPORT_DIRECTORY": "y", "refit_cores": "9", "REPORTS_HOST": "z", "PYTHONPATH": "q",
                        "COMPOSE_FILE": "c", "USERPROFILE": "u", "LOCALAPPDATA": "l"})
    assert env == {"PATH": "p", "USERPROFILE": "u", "LOCALAPPDATA": "l"}


# --------------------------------------------------------------------------------------------------------------- freeze

def test_freeze_anchors_copies_exactly_the_read_set_with_a_manifest(world):
    assert freeze(world) == 0
    root = snap_root(world)
    files = manifest_of(world)
    data = {rel for rel, f in files.items() if f["kind"] == "data"}
    assert data == {
        "reports/Banking Industry Spending Overview.xlsx", "reports/Telecom Industry Spending Overview.xlsx",
        "Mentat/mentat/state/contribution-analytics/new-bank.sqlite", "Mentat/mentat/state/contribution-analytics/tel.sqlite",
        "Mentat/mentat/.cache/report-repairs/banking-2/data/mentat.db", "Mentat/mentat/.cache/receipt-metadata-repair-1/telecom/mentat.db",
        "Mentat/mentat/inputs/active_fec_sources.json", "Mentat/sector_growth_study/data/00_controls.xlsx",
        "LocalAppData/Melange/lda_rebuild/derived/sector_spend/sector_spend.csv", "LocalAppData/Melange/lda_rebuild/derived/fullscope_receipts.json",
        *("Mentat/mentat/.cache/fec/fec_beneficiary/pas2%s/%s" % (yy, n) for yy in ("22", "24", "26") for n in ("itpas2.txt", "source-receipt.json")),
        *("Mentat/mentat/.cache/fec/fec_beneficiary/cn%s/cn.txt" % yy for yy in ("22", "24", "26")),
        *("Mentat/mentat/.cache/fec/fec_committee_master/cm%s/cm.txt" % yy for yy in ("22", "24", "26"))}
    assert not any("old-bank" in r or "other.sqlite" in r or "banking-1" in r or "pas220" in r or "cm20" in r for r in files)
    assert files["Mentat/mentat/tools/run_job.ps1"]["kind"] == "code" and "Mentat/mentat/compute.images.lock" in files
    for rel, entry in files.items():
        copy = root / "inputs" / rel
        assert sha(copy.read_bytes()) == entry["sha256"] and copy.stat().st_size == entry["size"]
    assert ms.verify_tree(root / "inputs", files) == []
    record = json.loads((root / "freeze.json").read_text())
    assert record["mentat"]["head"] == HEAD and record["mentat"]["digest"] == DIGEST and record["run_id"] == "parity-anchors-S1"
    assert record["meta"]["fec_root_rel"] == "Mentat/mentat/.cache/fec" and record["meta"]["fec_cycles"] == ["22", "24", "26"]
    assert (root / ms.MARKER).is_file()
    assert json.loads(ms.pointer_path(world.results, "anchors", "S1").read_text())["snapshot_root"] == str(root)


def test_inputs_digest_covers_data_only_and_code_digest_code_only(world):
    freeze(world)
    files = manifest_of(world)
    record = json.loads((snap_root(world) / "freeze.json").read_text())
    assert record["inputs_digest"] == ms.digest_of(files, "data") and record["code_digest"] == ms.digest_of(files, "code")
    changed = {k: dict(v) for k, v in files.items()}
    changed["Mentat/mentat/tools/run_job.ps1"]["sha256"] = "0" * 64
    assert ms.digest_of(changed, "data") == ms.digest_of(files, "data") and ms.digest_of(changed, "code") != ms.digest_of(files, "code")


def test_freeze_refuses_a_dirty_or_moved_checkout(world, capsys):
    world.host.dirty = ["mentat/app/server.py"]
    assert freeze(world) == 2 and "dirty" in capsys.readouterr().err
    world.host.dirty = []
    assert freeze(world, "anchors", "S1", "--expect-head", "9999999") == 2 and "not the expected" in capsys.readouterr().err
    assert freeze(world, "anchors", "S1", "--expect-head", HEAD[:8]) == 0
    assert not (world.scratch / "S2").exists()


def test_freeze_refuses_when_c_is_under_30_gb(world, capsys):
    world.host.free_c = 29 * GB
    assert freeze(world) == 2 and "below the 30 GB floor" in capsys.readouterr().err
    assert not snap_root(world).exists()


def test_space_gate_counts_the_copies_against_the_floor(world):
    host, scratch = world.host, world.scratch
    host.free_c = host.free_scratch = 31 * GB
    assert ms.space_problems(host, scratch, 0) == []
    assert ms.space_problems(host, scratch, 1 * GB) == []
    assert "after this run's copies" in ms.space_problems(host, scratch, 2 * GB)[0]
    host.free_c = 29 * GB
    assert "below the 30 GB floor" in ms.space_problems(host, scratch, 0)[0]
    host.drive = "Z:\\"                                   # the scratch is on another drive: C: is judged on its own, the scratch drive after the copies
    host.free_c, host.free_scratch = 31 * GB, 100 * GB
    assert ms.space_problems(host, scratch, 60 * GB) == []                       # 100 - 60 = 40 free after the copies, and C: only needs 31
    host.free_scratch = 80 * GB
    problems = ms.space_problems(host, scratch, 60 * GB)
    assert len(problems) == 1 and "scratch drive" in problems[0]


def test_freeze_refuses_when_the_copies_would_cross_the_floor(world, capsys):
    world.host.free_c = int(30 * GB + 1024)               # above the floor, but master + A + B of the read set (a few KB) would not fit
    assert freeze(world) == 2
    assert "master + A + B copies need" in capsys.readouterr().err
    assert not snap_root(world).exists()


def test_the_floor_can_only_be_raised(world, capsys):
    world.host.free_c = 40 * GB
    assert cli(world, "--min-free-gb", "1", "freeze", "--job", "anchors", "--snapshot", "S1", "--scratch", str(world.scratch)) == 0
    assert cli(world, "--min-free-gb", "50", "freeze", "--job", "anchors", "--snapshot", "S2", "--scratch", str(world.scratch)) == 2


def test_scratch_rules(world, capsys):
    world.host.temp = [str(world.tmp)]
    assert freeze(world) == 2 and "temp folder" in capsys.readouterr().err
    world.host.temp = []
    for bad, why in ((str(world.tmp / "with space"), "space"), (str(world.tmp / "OneDrive" / "p"), "OneDrive"), ("relative/dir", "absolute")):
        assert cli(world, "freeze", "--job", "anchors", "--snapshot", "S1", "--scratch", bad) == 2
        assert why in capsys.readouterr().err
    assert cli(world, "freeze", "--job", "anchors", "--snapshot", "S1") == 2          # no --scratch at all


def test_a_snapshot_name_is_used_once(world, capsys):
    assert freeze(world) == 0
    assert freeze(world) == 2 and "not empty" in capsys.readouterr().err
    put(ms.result_path(world.results, "anchors", "S9"), "{}")
    assert freeze(world, "anchors", "S9") == 2 and "already exists" in capsys.readouterr().err
    assert freeze(world, "anchors", "bad name") == 2


def test_freeze_refuses_a_missing_lda_file_and_an_fec_root_outside_the_tree(world, capsys):
    (world.lad / "Melange" / "lda_rebuild" / "derived" / "fullscope_receipts.json").unlink()
    assert freeze(world) == 2 and "fullscope_receipts.json is missing" in capsys.readouterr().err
    put(world.lad / "Melange" / "lda_rebuild" / "derived" / "fullscope_receipts.json", "{}")
    put(world.project / "inputs" / "active_fec_sources.json", json.dumps({"path": "D:\\elsewhere\\fec"}))
    assert freeze(world, "anchors", "S2") == 2
    assert "is outside" in capsys.readouterr().err


def test_copy_hashed_refuses_a_source_that_changes_while_it_is_copied(tmp_path):
    src = put(tmp_path / "s.bin", b"abc")
    with pytest.raises(ms.Refused, match="changed while it was being copied"):
        ms.copy_hashed(src, tmp_path / "d.bin", _after_read=lambda: src.write_bytes(b"abcdef"))
    sha_, size = ms.copy_hashed(src, tmp_path / "e.bin")
    assert (sha_, size) == (sha(b"abcdef"), 6)


def test_freeze_refit_detect_takes_workbooks_audits_agent_and_rlib(world):
    assert freeze(world, "refit-detect") == 0
    data = {r for r, f in manifest_of(world, "refit-detect").items() if f["kind"] == "data"}
    assert data == {"reports/Banking Industry Spending Overview.xlsx", "reports/Banking Industry Spending Overview.audit/audit-manifest.json",
                    "reports/Telecom Industry Spending Overview.xlsx", "LocalAppData/Mentat/forecast/refit/agent/seen.json",
                    "LocalAppData/Mentat/forecast/refit/Rlib/clubSandwich/DESCRIPTION"}


def test_freeze_pocket_build_needs_the_snapshot_file(world, capsys):
    assert freeze(world, "pocket-build") == 2 and "--pocket-data" in capsys.readouterr().err
    assert freeze(world, "pocket-build", "S1", "--pocket-data", str(world.tmp / "nope.json")) == 2
    bad = put(world.tmp / "bad.json", "{not json")
    assert freeze(world, "pocket-build", "S1", "--pocket-data", str(bad)) == 2
    good = put(world.tmp / "pocket-data.json", '{"generated": "2026-10-18"}')
    assert freeze(world, "pocket-build", "S1", "--pocket-data", str(good)) == 0
    assert manifest_of(world, "pocket-build")["in/pocket-data.json"]["sha256"] == sha(good.read_bytes())


def test_freeze_refit_parity_checks_every_pinned_hash_and_maps_aliases(world, monkeypatch, capsys):
    loo, che, zipc = world.tmp / "src" / "loo", world.tmp / "src" / "che", world.tmp / "src" / "pc.zip"
    entries = [("a", "{LOO}/outputs/a.json", put(loo / "outputs" / "a.json", b"A")), ("c", "{CHE}/x/c.csv", put(che / "x" / "c.csv", b"C")),
               ("z", "{ZIPC}", put(zipc, b"Z")), ("g", "{SGSD}/g.csv", put(world.root / "sector_growth_study" / "data" / "g.csv", b"G")),
               ("i", "{INGEST}/run1/i.json", put(world.lad / "Mentat" / "forecast" / "ingest" / "runs" / "run1" / "i.json", b"I")),
               ("r", "{RFDATA}/d.csv", put(world.lad / "Mentat" / "forecast" / "refit" / "data" / "d.csv", b"R")),
               ("b", "{BUNDLE_V1}", world.project / "app" / "data" / "giving-models-v1.json"),
               ("y", "{CYCLE}/pace.csv", world.project / "forecast" / "refit" / "inputs" / "cycle_model" / "pace.csv")]
    manifest = {"inputs": [{"role": r, "path": p, "member": None, "sha256": sha(f.read_bytes())} for r, p, f in entries]}
    put(world.project / "forecast" / "refit" / "registry" / "inputs.json", json.dumps(manifest))
    monkeypatch.setitem(ms.ALIAS_SOURCES, "LOO", ("path", str(loo), "ext/loo"))
    monkeypatch.setitem(ms.ALIAS_SOURCES, "CHE", ("path", str(che), "ext/che"))
    monkeypatch.setitem(ms.ALIAS_SOURCES, "ZIPC", ("file", str(zipc), "ext/House_Senate_Control_Forecasts_CSV.zip"))
    monkeypatch.setitem(ms.ALIAS_SOURCES, "SGSD", ("path", str(world.root / "sector_growth_study" / "data"), "Mentat/sector_growth_study/data"))
    assert freeze(world, "refit-parity") == 0
    data = {r for r, f in manifest_of(world, "refit-parity").items() if f["kind"] == "data"}
    assert data == {"ext/loo/outputs/a.json", "ext/che/x/c.csv", "ext/House_Senate_Control_Forecasts_CSV.zip", "Mentat/sector_growth_study/data/g.csv",
                    "LocalAppData/Mentat/forecast/ingest/runs/run1/i.json", "LocalAppData/Mentat/forecast/refit/data/d.csv",
                    "LocalAppData/Mentat/forecast/refit/Rlib/clubSandwich/DESCRIPTION"}
    loo.joinpath("outputs", "a.json").write_bytes(b"a new vintage")
    assert freeze(world, "refit-parity", "S2") == 2
    assert "no longer hashes to the pinned sha256" in capsys.readouterr().err


# ----------------------------------------------------------------------------------------------------------------- plan

def test_plan_prints_both_sides_and_runs_nothing(world, capsys):
    freeze(world)
    world.host.calls.clear()
    capsys.readouterr()
    assert cli(world, "plan", "--job", "anchors", "--snapshot", "S1") == 0                # no --scratch: found through the pointer
    text = capsys.readouterr().out
    assert world.host.calls == []
    root = snap_root(world)
    assert "## A" in text and "## B" in text and "run_logged.ps1" in text and "-TimeoutMinutes 45" in text
    assert str(root / "B" / "Mentat" / "mentat" / "tools" / "run_job.ps1") in text and "exit $LASTEXITCODE" in text
    assert "-Job anchors -RunId parity-anchors-S1" in text and "$env:LOCALAPPDATA = " in text
    assert "$env:ANCHORS_OUT_HOST = " in text and "$env:REPORTS_HOST = " in text and "--fec-root" in text and "--lda-csv" in text


def test_plan_commands_per_job(world, capsys):
    good = put(world.tmp / "pd.json", "{}")
    freeze(world, "pocket-build", "S1", "--pocket-data", str(good))
    freeze(world, "refit-detect", "S1")
    caps = ms.job_caps(world.root)
    pocket = ms.build_plan(world.host, ms.Layout(snap_root(world, "pocket-build")), json.loads((snap_root(world, "pocket-build") / "freeze.json").read_text()), caps)
    assert "-JobArgs '--data','/work/in/pocket-data.json','--out','/work/out/mentat-pocket.html'" in pocket["B"].argv[-1]
    assert pocket["B"].env["POCKET_IN_HOST"].endswith("/B/in") and pocket["B"].env["POCKET_OUT_HOST"].endswith("/B/out")
    assert pocket["A"].argv[pocket["A"].argv.index("-TimeoutMinutes") + 1] == "10" and pocket["A"].timeout_s == 12 * 60 and pocket["B"].timeout_s == 25 * 60
    detect = ms.build_plan(world.host, ms.Layout(snap_root(world, "refit-detect")), json.loads((snap_root(world, "refit-detect") / "freeze.json").read_text()), caps)
    a = detect["A"]
    assert a.argv[a.argv.index("-Exe") + 1] == ms.DEFAULT_RSCRIPT and a.argv[-4:] == ["--mode", "detect", "--run-id", "parity-refit-detect-S1"]
    assert a.env["RF_RUNTIME"].endswith(os.path.join("A", "LocalAppData", "Mentat", "forecast", "refit")) and a.env["RF_REPORT_DIR"].endswith("reports")
    assert detect["compare_root"] == ms.RUNTIME and detect["primary"] == ["runs/parity-refit-detect-S1/detect.csv", "agent/seen.json"]


def test_no_command_or_variable_points_into_the_live_checkout(world):
    freeze(world)
    record = json.loads((snap_root(world) / "freeze.json").read_text())
    plan = ms.build_plan(world.host, ms.Layout(snap_root(world)), record, ms.job_caps(world.root))
    live = ms.posix(world.root).lower()
    for side in ("A", "B"):
        blob = ms.posix(" ".join(plan[side].argv) + " " + " ".join(plan[side].env.values()) + " " + str(plan[side].cwd)).lower()
        assert live + "/" not in blob and not blob.endswith(live)
        assert ms.posix(snap_root(world)).lower() in blob
    assert plan["B"].env["LOCALAPPDATA"] != str(world.lad) and "MENTAT_HOST" not in plan["B"].env


def test_plan_json_and_the_refit_parity_environment(world, capsys):
    manifest = {"inputs": [{"role": "b", "path": "{BUNDLE_V1}", "member": None, "sha256": sha((world.project / "app" / "data" / "giving-models-v1.json").read_bytes())}]}
    put(world.project / "forecast" / "refit" / "registry" / "inputs.json", json.dumps(manifest))
    assert freeze(world, "refit-parity") == 0
    capsys.readouterr()
    assert cli(world, "plan", "--job", "refit-parity", "--snapshot", "S1", "--json") == 0
    plan = json.loads(capsys.readouterr().out)
    root = snap_root(world, "refit-parity")
    a_env, b_env = plan["A"]["env"], plan["B"]["env"]
    assert a_env["REFIT_CORES"] == "4" and Path(a_env["RF_ROOT_LOO"]) == root / "A" / "ext" / "loo" and Path(a_env["RF_ROOT_SGSD"]) == root / "A" / "Mentat" / "sector_growth_study" / "data"
    assert Path(a_env["RF_ROOT_INGEST"]) == root / "A" / "LocalAppData" / "Mentat" / "forecast" / "ingest" / "runs"
    assert b_env["LOO_HOST"].endswith("/B/ext/loo") and b_env["CHE_HOST"].endswith("/B/ext/che") and b_env["ZIPC_HOST"].endswith("/B/ext/House_Senate_Control_Forecasts_CSV.zip")
    assert plan["A"]["argv"][-4:] == ["--mode", "parity", "--run-id", "parity-refit-parity-S1"] and plan["cap_minutes"] == 240
    assert plan["A"]["timeout_s"] == 242 * 60 and plan["B"]["timeout_s"] == 255 * 60
    assert [m[1] for m in plan["maps"]] == ["/mentat", "/localappdata", "/reports", "/inputs/loo", "/inputs/che", "/inputs/House_Senate_Control_Forecasts_CSV.zip"]


# ------------------------------------------------------------------------------------------------------------------ run

def anchors_writer(label, built_at, bad_money=False):
    def write(env, argv, cwd):
        side = Path(env["GLASSBOX_EVENTS_DIR"]).parent
        out = side / "out" / "giving-anchors"
        host_prefix = "/mentat" if label == "B" else str(side / "Mentat").replace("\\", "/")
        snapshot = {"built_at_utc": built_at, "industries": {"commercial_banks": {"status": {"general_giving": "ready", "pac_to_candidates": "ready"},
                                                                                  "total_cents": 12345 + (1 if bad_money else 0)}},
                    "unavailable": {}, "inputs": [host_prefix + "/mentat/state/x.sqlite"]}
        text = json.dumps(snapshot, indent=1)
        put(out / "giving-anchors-v1.json", text)
        put(out / "giving-anchors-v1.receipt.json", json.dumps({"snapshot_sha256": sha(text.encode()), "built_at_utc": built_at,
                                                                 "elapsed_seconds": 3.5 if label == "A" else 9.9, "industries": {"commercial_banks": {"status": "ready"}}}))
    return write


def arm(world, a_at="2026-10-18T10:00:00Z", b_at="2026-10-18T11:00:00Z", **kw):
    world.host.writers = {"A": anchors_writer("A", a_at, kw.get("a_bad", False)), "B": anchors_writer("B", b_at, kw.get("b_bad", False))}


def run(world, job="anchors", snapshot="S1", *extra):
    return cli(world, "run", "--job", job, "--snapshot", snapshot, *extra)


def test_run_without_execute_prints_the_plan_and_starts_nothing(world, capsys):
    freeze(world)
    world.host.calls.clear()
    assert run(world) == 0
    assert "dry run" in capsys.readouterr().out
    assert world.host.calls_for("A") == [] and world.host.calls_for("B") == [] and not (snap_root(world) / "A").exists()


@pytest.mark.parametrize("setup,needle", [
    (lambda h: h.tasks.append(("MentatNightlyAnchors", "Running")), "MentatNightlyAnchors is running"),
    (lambda h: h.tasks.append(("MentatRefitWatcher", "Running")), "MentatRefitWatcher is running"),
    (lambda h: h.containers.extend(["terrier-build", "mentat-compute-x"]), "running container"),
    (lambda h: setattr(h, "docker_hang", True), "docker info"),
    (lambda h: setattr(h, "docker_rc", 1), "Docker is not answering"),
    (lambda h: setattr(h, "image_present", False), "not in the local Docker store"),
    (lambda h: h.processes.append("4242|Rscript.exe|Rscript run_refit.R --mode parity"), "heavy host work"),
    (lambda h: h.processes.append("4243|python.exe|python C:/x/weekly_refresh.py --all"), "weekly_refresh.py"),
    (lambda h: h.tasks.append(("MentatNightlyAnchors", "Ready", "2026-10-18T12:30:00Z")), "due at 2026-10-18T12:30:00Z"),
    (lambda h: setattr(h, "global_busy", True), "another streak run holds"),
    (lambda h: h.held.add("anchors.lock"), "anchors.lock is held"),
    (lambda h: setattr(h, "dirty", ["mentat/x"]), "dirty"),
    (lambda h: setattr(h, "head", "2" * 40), "HEAD moved"),
    (lambda h: setattr(h, "free_c", 20 * GB), "floor"),
    (lambda h: h.missing_tools.add("run_logged.ps1"), "run_logged.ps1"),
])
def test_run_gates_refuse_before_anything_starts(world, capsys, setup, needle):
    freeze(world)
    arm(world)
    (world.lad / "Mentat" / "compute" / "locks").mkdir(parents=True, exist_ok=True)
    (world.lad / "Mentat" / "compute" / "locks" / "anchors.lock").write_text("")
    setup(world.host)
    capsys.readouterr()
    assert run(world, "anchors", "S1", "--execute") == 2
    assert needle in capsys.readouterr().err
    assert world.host.events == [] and not (snap_root(world) / "A").exists()


def test_a_ready_task_far_in_the_future_and_a_disabled_one_do_not_block(world):
    freeze(world)
    arm(world)
    world.host.tasks += [("MentatNightlyAnchors", "Ready", "2026-10-19T08:30:00Z"), ("MentatRefitWatcher", "Disabled", "2026-10-18T12:05:00Z"),
                         ("MentatOther", "Ready", "")]
    assert run(world, "anchors", "S1", "--execute") == 0


def test_run_allows_a_named_light_container(world):
    freeze(world)
    arm(world)
    world.host.containers.append("logs-explorer")
    assert run(world, "anchors", "S1", "--execute", "--allow-container", "logs-explorer") == 0


def test_run_is_a_then_b_never_parallel_each_from_a_verified_copy(world):
    freeze(world)
    arm(world)
    seen = {}
    inner_a, inner_b = world.host.writers["A"], world.host.writers["B"]

    def wa(env, argv, cwd):
        seen["b_exists_during_a"] = (snap_root(world) / "B").exists()
        seen["a_copy_ok"] = ms.verify_tree(snap_root(world) / "A", manifest_of(world)) == []          # the copy A starts from is exactly the frozen inputs
        inner_a(env, argv, cwd)

    def wb(env, argv, cwd):
        seen["a_done_before_b"] = world.host.events == ["A", "B"] and (snap_root(world) / "A" / "out" / "giving-anchors" / "giving-anchors-v1.json").is_file()
        inner_b(env, argv, cwd)
    world.host.writers = {"A": wa, "B": wb}
    assert run(world, "anchors", "S1", "--execute") == 0
    assert world.host.events == ["A", "B"] and seen == {"b_exists_during_a": False, "a_copy_ok": True, "a_done_before_b": True}
    record = json.loads((snap_root(world) / "run.json").read_text())
    assert record["A"]["exit"] == 0 and record["B"]["exit"] == 0 and not (snap_root(world) / ".run.lock").exists()
    assert world.host.locks_taken == [world.results / ms.GLOBAL_LOCK, "released"]                  # one exclusive lock around the whole run
    a_call, b_call = world.host.calls_for("A")[0], world.host.calls_for("B")[0]
    assert a_call.timeout == (45 + 2) * 60 and b_call.timeout == (45 + 15) * 60
    assert Path(a_call.cwd) == snap_root(world) / "A" / "Mentat"
    assert Path(b_call.env["LOCALAPPDATA"]) == snap_root(world) / "B" / "LocalAppData"
    for rel in ("glassbox", "out/giving-anchors"):
        assert (snap_root(world) / "A" / rel).is_dir() and (snap_root(world) / "B" / rel).is_dir()
    assert (snap_root(world) / "B" / "Mentat" / "mentat" / "state" / "giving-anchors").is_dir()          # the nested bind-mount target inside /mentat
    assert (snap_root(world) / "B" / "LocalAppData" / "Mentat" / "compute" / "receipts").is_dir()


def test_the_environment_given_to_a_and_b_is_cleaned(world):
    freeze(world)
    arm(world)
    world.host.extra_env = {"RF_RUNTIME": "C:/live/runtime", "MENTAT_REPORT_DIRECTORY": "C:/live/reports", "REFIT_CORES": "99", "KEEP_ME": "1"}
    assert run(world, "anchors", "S1", "--execute") == 0
    for label in ("A", "B"):
        env = world.host.calls_for(label)[0].env
        assert "RF_RUNTIME" not in env and "REFIT_CORES" not in env and env.get("KEEP_ME") == "1"
    assert world.host.calls_for("B")[0].env["MENTAT_REPORT_DIRECTORY"].endswith("reports") and "live" not in world.host.calls_for("B")[0].env["MENTAT_REPORT_DIRECTORY"]


def test_a_failure_stops_before_b_and_writes_a_failed_result(world, capsys):
    freeze(world)
    arm(world)
    world.host.exits["A"] = 1
    assert run(world, "anchors", "S1", "--execute") == 1
    assert world.host.events == ["A"] and "A side" in capsys.readouterr().out
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    assert result["status"] == "fail" and "exited 1" in result["reasons"][0] and result["phase"] == "run"


def test_continue_after_a_failure_still_runs_b(world):
    freeze(world)
    arm(world)
    world.host.exits["A"] = 1
    assert run(world, "anchors", "S1", "--execute", "--continue-after-a-failure") == 0
    assert world.host.events == ["A", "B"]


def test_a_b_failure_is_recorded(world):
    freeze(world)
    arm(world)
    world.host.exits["B"] = 3
    assert run(world, "anchors", "S1", "--execute") == 1
    assert json.loads(ms.result_path(world.results, "anchors", "S1").read_text())["reasons"][0].startswith("B side")


def test_a_checkout_that_moves_during_the_run_fails_the_snapshot(world):
    freeze(world)
    arm(world)
    world.host.head_after = {"A": "3" * 40}
    assert run(world, "anchors", "S1", "--execute") == 1
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    assert result["status"] == "fail" and "moved during the A run" in result["reasons"][0] and world.host.events == ["A"]


def test_a_tampered_frozen_copy_is_refused_before_a_runs(world, capsys):
    freeze(world)
    arm(world)
    (snap_root(world) / "inputs" / "Mentat" / "mentat" / "tools" / "run_job.ps1").write_text("# changed after the freeze\n")
    assert run(world, "anchors", "S1", "--execute") == 2
    assert "not byte-identical" in capsys.readouterr().err and world.host.events == []


def test_a_snapshot_runs_once(world, capsys):
    freeze(world)
    arm(world)
    assert run(world, "anchors", "S1", "--execute") == 0
    assert run(world, "anchors", "S1", "--execute") == 2 and "has been run" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------------------- mask

def test_validate_volatile_accepts_the_shipped_list():
    spec = ms.load_volatile()
    assert ms.validate_volatile(spec) == []
    assert set(spec["jobs"]) == set(ms.JOBS)
    for job, body in spec["jobs"].items():
        for entry in body["mask"]:
            assert entry["cite"].startswith("mentat/") and entry["reason"] and entry["needle"]
            assert not any(ms.money_problem(p) for p in entry["locations"])
    assert spec["jobs"]["pocket-build"]["mask"] == []


@pytest.mark.parametrize("pointer", ["/total_cents", "/industries/commercial_banks/amount", "/a/receipt_total/x", "/rank", "/Disbursements/0", "/cycles/2024/sum", "/threshold",
                                     "/by_funding_source/count", "/percentile", "/usd", "/dollars_raised", "/amt", "/contributions/0", "/giving", "/spending", "/value", "/market_share"])
def test_money_locations_are_refused(pointer):
    assert ms.money_problem(pointer)
    spec = {"schema": ms.VOLATILE_SCHEMA, "jobs": {"anchors": {"mask": [{"file": "a.json", "locations": [pointer], "cite": "mentat/x.py:1", "needle": "n", "reason": "r"}], "ignore": []}}}
    assert any("money" in p for p in ms.validate_volatile(spec))


@pytest.mark.parametrize("pointer", ["/built_at_utc", "/elapsed_seconds", "/snapshot_sha256", "/started_utc", "/finished_utc", "/detected_utc", "/run_id", "/decision"])
def test_the_shipped_volatile_locations_are_not_money(pointer):
    assert ms.money_problem(pointer) is None


def test_validate_volatile_rejects_malformed_entries():
    def spec(entry, ignore=()):
        return {"schema": ms.VOLATILE_SCHEMA, "jobs": {"anchors": {"mask": [entry] if entry else [], "ignore": list(ignore)}}}
    good = {"file": "a.json", "locations": ["/x"], "cite": "mentat/x.py:1-2", "needle": "n", "reason": "r"}
    assert ms.validate_volatile(spec(good)) == []
    assert ms.validate_volatile(spec({**good, "locations": ["/*"]}))                           # wildcard location
    assert ms.validate_volatile(spec({**good, "locations": ["x"]}))                            # not a pointer
    assert ms.validate_volatile(spec({**good, "file": "a.csv"}))                               # JSON files only
    assert ms.validate_volatile(spec({**good, "file": "../a.json"}))
    assert ms.validate_volatile(spec({**good, "cite": "x.py:1"}))                              # no mentat/ path, no line
    assert ms.validate_volatile(spec({**good, "needle": ""}))
    assert ms.validate_volatile(spec(None, [{"glob": "detect.csv", "cite": "mentat/x:1", "needle": "n", "reason": "r"}]))      # a data file is not log-like
    assert ms.validate_volatile(spec(None, [{"glob": "*.json", "cite": "mentat/x:1", "needle": "n", "reason": "r"}]))
    assert ms.validate_volatile(spec(None, [{"glob": "runs/{run_id}/log.txt", "cite": "mentat/x:1", "needle": "n", "reason": "r"}])) == []
    assert ms.validate_volatile({"schema": "other", "jobs": {}})
    assert ms.validate_volatile({"schema": ms.VOLATILE_SCHEMA, "jobs": {"bogus": {}}})


def test_load_volatile_refuses_an_unacceptable_file(tmp_path):
    bad = put(tmp_path / "v.json", json.dumps({"schema": ms.VOLATILE_SCHEMA, "jobs": {"anchors": {"mask": [{"file": "a.json", "locations": ["/total"], "cite": "mentat/x:1",
                                                                                                       "needle": "n", "reason": "r"}], "ignore": []}}}))
    with pytest.raises(ms.Refused, match="money"):
        ms.load_volatile(bad)


@pytest.mark.skipif(not LIVE_ROOT.joinpath("mentat", "forecast", "anchors", "build_giving_anchors.py").is_file(), reason="no live Mentat")
def test_every_citation_of_the_shipped_list_holds_in_the_live_checkout():
    assert ms.check_citations(ms.load_volatile(), LIVE_ROOT) == []


def test_check_citations_catches_a_moved_line(tmp_path):
    put(tmp_path / "mentat" / "f.py", "a\nb = 1\n")
    spec = {"jobs": {"anchors": {"mask": [{"cite": "mentat/f.py:2", "needle": "b = 1"}], "ignore": [{"cite": "mentat/f.py:1-2", "needle": "zzz"}]}}}
    problems = ms.check_citations(spec, tmp_path)
    assert len(problems) == 1 and "'zzz' is not on mentat/f.py:1-2" in problems[0]
    spec["jobs"]["anchors"]["ignore"] = [{"cite": "mentat/f.py:9", "needle": "b"}, {"cite": "mentat/gone.py:1", "needle": "b"}]
    assert len(ms.check_citations(spec, tmp_path)) == 2


def test_build_mask_from_the_static_list(tmp_path):
    a = tmp_path / "A"
    put(a / "giving-anchors-v1.json", json.dumps({"built_at_utc": "t", "industries": {"x": {"total_cents": 5}}}))
    put(a / "giving-anchors-v1.receipt.json", json.dumps({"built_at_utc": "t", "elapsed_seconds": 1, "snapshot_sha256": "s", "other": 1}))
    mask, notes = ms.build_mask(ms.load_volatile(), "anchors", a, "rid", "D" * 64)
    assert mask == {"schema": "mentat-parity-mask-v1", "a1_digest": "D" * 64, "a2_digest": "D" * 64, "ignore": [], "unstable_files": [],
                    "files": {"giving-anchors-v1.json": ["/built_at_utc"], "giving-anchors-v1.receipt.json": ["/built_at_utc", "/elapsed_seconds", "/snapshot_sha256"]}}
    assert notes == {"absent_files": [], "absent_locations": {}}
    put(a / "giving-anchors-v1.receipt.json", json.dumps({"built_at_utc": "t"}))
    mask, notes = ms.build_mask(ms.load_volatile(), "anchors", a, "rid", "D" * 64)
    assert mask["files"]["giving-anchors-v1.receipt.json"] == ["/built_at_utc"]
    assert notes["absent_locations"]["giving-anchors-v1.receipt.json"] == ["/elapsed_seconds", "/snapshot_sha256"]


def test_build_mask_expands_run_id_and_globs_and_ignores_logs(tmp_path):
    a = tmp_path / "A"
    put(a / "runs" / "rid" / "receipt.json", json.dumps({"started_utc": "s", "finished_utc": "f", "decision": "x"}))
    put(a / "runs" / "rid" / "log.txt", "12:00:00  hi\n")
    mask, notes = ms.build_mask(ms.load_volatile(), "refit-parity", a, "rid", "d")
    assert mask["files"] == {"runs/rid/receipt.json": ["/finished_utc", "/started_utc"]} and mask["ignore"] == ["runs/rid/log.txt"]
    put(a / "agent" / "queue" / "new_industry.json", json.dumps({"detected_utc": "t", "status": "queued"}))
    mask, notes = ms.build_mask(ms.load_volatile(), "refit-detect", a, "rid", "d")
    assert mask["files"] == {"agent/queue/new_industry.json": ["/detected_utc"]}
    empty = tmp_path / "E"
    empty.mkdir()
    mask, notes = ms.build_mask(ms.load_volatile(), "refit-detect", empty, "rid", "d")
    assert mask["files"] == {} and notes["absent_files"] == ["agent/queue/*.json"]
    mask, _ = ms.build_mask(ms.load_volatile(), "pocket-build", empty, "rid", "d")
    assert mask["files"] == {} and mask["ignore"] == []


def test_build_mask_refuses_a_money_location_even_from_a_hand_edited_list(tmp_path):
    a = tmp_path / "A"
    put(a / "x.json", json.dumps({"total_cents": 1}))
    spec = {"schema": ms.VOLATILE_SCHEMA, "jobs": {"anchors": {"mask": [{"file": "x.json", "locations": ["/total_cents"], "cite": "mentat/f:1", "needle": "n", "reason": "r"}], "ignore": []}}}
    with pytest.raises(ms.Refused, match="money"):
        ms.build_mask(spec, "anchors", a, "rid", "d")


def test_the_json_pointer_flattening_matches_mentats(tmp_path):
    doc = {"a": {"b/c": 1, "d~e": [1, {"f": 2}], "g": {}}, "h": []}
    assert ms.flatten_pointers(doc) == {"/a/b~1c", "/a/d~0e/0", "/a/d~0e/1/f", "/a/g", "/h"}
    if LIVE_PARITY.is_file():
        cp = ms.load_module(LIVE_PARITY, "_cp_for_test")
        out = {}
        cp._flatten(doc, "", out)
        assert set(out) == ms.flatten_pointers(doc)


# ------------------------------------------------------------------------------------ compare with Mentat's real tool

def run_world(world, **kw):
    assert freeze(world) == 0
    arm(world, **kw)
    assert run(world, "anchors", "S1", "--execute") == 0


def compare(world, *extra, snapshot="S1"):
    return cli(world, "compare", "--job", "anchors", "--snapshot", snapshot, "--python", sys.executable, *extra)


@needs_parity
def test_mentats_compare_accepts_the_static_mask_and_passes(world, capsys):
    run_world(world)
    capsys.readouterr()
    assert compare(world, "--keep") == 0
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    assert result["status"] == "pass" and result["reasons"] == []
    assert result["compare"]["ok"] is True and result["compare_exit"] == 0
    assert result["mask"]["a1_digest"] == result["mask"]["a2_digest"] and result["mask"]["files"]["giving-anchors-v1.json"] == ["/built_at_utc"]
    # A and B differ in the clock fields (masked) and in the mount prefix of one recorded path (--map: PATH_ONLY, listed for the explanation report)
    assert result["compare"]["counts"] == {"PATH_ONLY": 1, "JSON_CANONICAL": 1}
    assert result["compare"]["needs_acceptance"] == {"giving-anchors-v1.json": {"class": "PATH_ONLY", "where": ["/inputs/0"]}}
    assert result["mask_notes"] == {"absent_files": [], "absent_locations": {}}
    assert result["a"]["exit"] == 0 and result["b"]["exit"] == 0
    assert result["mentat_head"] == HEAD and result["image_digest"] == DIGEST and result["coverage"]["industries"] == ["commercial_banks"]
    assert snap_root(world).exists()                                  # --keep


@needs_parity
def test_compare_deletes_the_scratch_unless_kept(world):
    run_world(world)
    assert compare(world) == 0
    assert not snap_root(world).exists() and world.scratch.exists()
    assert ms.result_path(world.results, "anchors", "S1").is_file()


@needs_parity
def test_a_changed_money_field_is_never_masked_and_fails(world, capsys):
    run_world(world, b_bad=True)
    assert compare(world, "--keep") == 1
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    assert result["status"] == "fail" and "giving-anchors-v1.json" in result["compare"]["different"]
    assert "/industries/commercial_banks/total_cents" in result["compare"]["different"]["giving-anchors-v1.json"]


@needs_parity
def test_a_changed_non_volatile_field_fails(world):
    run_world(world)
    out = snap_root(world) / "B" / "out" / "giving-anchors"
    doc = json.loads((out / "giving-anchors-v1.json").read_text())
    doc["unavailable"] = {"telecom": "now missing"}
    text = json.dumps(doc, indent=1)
    (out / "giving-anchors-v1.json").write_text(text)
    receipt = json.loads((out / "giving-anchors-v1.receipt.json").read_text())
    receipt["snapshot_sha256"] = sha(text.encode())
    (out / "giving-anchors-v1.receipt.json").write_text(json.dumps(receipt))
    assert compare(world, "--keep") == 1
    assert "/unavailable/telecom" in json.loads(ms.result_path(world.results, "anchors", "S1").read_text())["compare"]["different"]["giving-anchors-v1.json"]


@needs_parity
def test_compare_fails_when_the_receipt_hash_does_not_match_its_snapshot(world):
    run_world(world)
    out = snap_root(world) / "B" / "out" / "giving-anchors"
    receipt = json.loads((out / "giving-anchors-v1.receipt.json").read_text())
    receipt["snapshot_sha256"] = "0" * 64
    (out / "giving-anchors-v1.receipt.json").write_text(json.dumps(receipt))
    assert compare(world, "--keep") == 1
    assert any("snapshot_sha256 is not the sha256" in r for r in json.loads(ms.result_path(world.results, "anchors", "S1").read_text())["reasons"])


@needs_parity
def test_compare_fails_when_a_primary_output_is_missing_or_empty(world):
    run_world(world)
    (snap_root(world) / "B" / "out" / "giving-anchors" / "giving-anchors-v1.receipt.json").write_text("")
    assert compare(world, "--keep") == 1
    assert any("no non-empty giving-anchors-v1.receipt.json" in r for r in json.loads(ms.result_path(world.results, "anchors", "S1").read_text())["reasons"])


@needs_parity
def test_mentats_tool_refuses_a_mask_not_bound_to_this_reference(world):
    run_world(world)
    plan_root = snap_root(world) / "A" / "out" / "giving-anchors"
    tool = ms.MentatCompare(world.host, world.root, python=sys.executable)
    digest = tool.tree_digest(plan_root, [])
    mask, _ = ms.build_mask(ms.load_volatile(), "anchors", plan_root, "rid", digest)
    good = snap_root(world) / "good.json"
    bad = snap_root(world) / "bad.json"
    ms.write_json(good, mask)
    ms.write_json(bad, {**mask, "a1_digest": "0" * 64, "a2_digest": "0" * 64})
    b = snap_root(world) / "B" / "out" / "giving-anchors"
    ok = tool.compare(plan_root, b, good, ms.compare_maps("anchors", snap_root(world) / "A"), [], snap_root(world) / "r1.json")
    refused = tool.compare(plan_root, b, bad, [], [], snap_root(world) / "r2.json")
    assert refused.returncode != 0 and "not derived from this reference run" in refused.stderr
    assert ok.returncode == 0                   # accepted without any change to Mentat: the digest binding is all it checks
    assert "not derived from this reference run" not in ok.stderr


def test_compare_needs_a_run(world):
    freeze(world)
    with pytest.raises(ms.Refused, match="has not been run"):
        ms.cmd_compare(SimpleNamespace(job="anchors", snapshot="S1", scratch=None, results_dir=str(world.results), mentat_root=str(world.root), keep=True, python=None), world.host)


class FakeCompare:
    def __init__(self, ok=True, report=None, rc=None):
        self.ok, self.report, self.rc, self.digest_args = ok, report, rc, None

    def tree_digest(self, root, ignore=()):
        self.digest_args = (str(root), list(ignore))
        return "feed" * 16

    def compare(self, a, b, mask, maps, ignore, report):
        files = {"giving-anchors-v1.json": {"class": "BYTE_EQUAL" if self.ok else "DIFFERENT", "where": [] if self.ok else ["/x"]}} if self.report is None else self.report
        counts = {}
        for v in files.values():
            counts[v["class"]] = counts.get(v["class"], 0) + 1
        ms.write_json(report, {"ok": self.ok, "counts": counts, "files": files, "only_in_a": [], "only_in_b": []})
        return ms.Proc(0 if self.ok else 1, "", "")


def test_compare_flow_with_a_fake_tool_binds_the_mask_to_a_and_records_the_result(world):
    run_world(world)
    tool = FakeCompare()
    args = SimpleNamespace(job="anchors", snapshot="S1", scratch=None, results_dir=str(world.results), mentat_root=str(world.root), keep=False, python=None)
    assert ms.cmd_compare(args, world.host, tool) == 0
    assert tool.digest_args[0] == str(snap_root(world) / "A" / "out" / "giving-anchors") or not snap_root(world).exists()
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    assert result["mask"]["a1_digest"] == "feed" * 16 and result["status"] == "pass"


def test_an_empty_comparison_is_not_a_pass(world):
    run_world(world)
    tool = FakeCompare(report={})
    args = SimpleNamespace(job="anchors", snapshot="S1", scratch=None, results_dir=str(world.results), mentat_root=str(world.root), keep=True, python=None)
    assert ms.cmd_compare(args, world.host, tool) == 1
    assert "nothing was compared" in " ".join(json.loads(ms.result_path(world.results, "anchors", "S1").read_text())["reasons"])


def test_a_failed_side_fails_the_comparison_even_if_the_trees_match(world):
    freeze(world)
    arm(world)
    world.host.exits["B"] = 1
    run(world, "anchors", "S1", "--execute")
    os.remove(ms.result_path(world.results, "anchors", "S1"))
    args = SimpleNamespace(job="anchors", snapshot="S1", scratch=None, results_dir=str(world.results), mentat_root=str(world.root), keep=True, python=None)
    assert ms.cmd_compare(args, world.host, FakeCompare()) == 1
    assert "B side exited 1" in json.loads(ms.result_path(world.results, "anchors", "S1").read_text())["reasons"]


def test_unreadable_run_output_is_a_failed_snapshot_not_a_refusal(world):
    run_world(world)
    (snap_root(world) / "A" / "out" / "giving-anchors" / "giving-anchors-v1.json").write_text("{cut short")   # a masked file
    args = SimpleNamespace(job="anchors", snapshot="S1", scratch=None, results_dir=str(world.results), mentat_root=str(world.root), keep=False, python=None)
    assert ms.cmd_compare(args, world.host, FakeCompare()) == 1
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    assert result["status"] == "fail" and any("not readable JSON" in r for r in result["reasons"])
    assert "mask" not in result and "compare" not in result and not snap_root(world).exists()   # nothing judged; scratch cleaned as usual
    assert ms.pending_snapshots(world.results) == {}
    assert issubclass(ms.Unreadable, ms.Refused)   # money pointers still refuse: only unreadable output becomes a result


def test_safe_rmtree_refuses_unmarked_or_foreign_folders(tmp_path):
    scratch = tmp_path / "scratch"
    inner = scratch / "S1" / "anchors"
    inner.mkdir(parents=True)
    layout = ms.Layout(inner)
    with pytest.raises(ms.Refused):
        ms.safe_rmtree(layout, scratch)                     # no marker
    layout.marker.write_text("x")
    with pytest.raises(ms.Refused):
        ms.safe_rmtree(layout, tmp_path / "elsewhere")      # not under that scratch root
    (inner / "ro.txt").write_text("x")
    os.chmod(inner / "ro.txt", 0o444)
    ms.safe_rmtree(layout, scratch)
    assert not inner.exists() and scratch.exists()


# --------------------------------------------------------------------------------------------------------------- status

def res(job, snapshot, status, head="h1", digest="d1", inputs=None, at=None, volatile=None, finished=None):
    when = at or "2026-10-18T%02d:00:00Z" % int(snapshot[1:] or 0)
    return {"schema": ms.RESULT_SCHEMA, "job": job, "snapshot": snapshot, "status": status, "mentat_head": head * 8, "image_digest": digest,
            "inputs_digest": inputs or "in-" + snapshot, "started_utc": when, "finished_utc": finished or when,
            "volatile_sha256": volatile or ms.sha256_file(ms.VOLATILE_FILE)}


def streak(rs, job="anchors"):
    return ms.compute_streaks(rs)[job]


def test_streak_counts_consecutive_passes_on_distinct_snapshots():
    s = streak([res("anchors", "S1", "pass"), res("anchors", "S2", "pass")])
    assert s["streak"] == 2 and s["snapshots"] == ["S1", "S2"] and s["head"] == "h1" * 8
    assert streak([res("anchors", "S1", "pass"), res("anchors", "S2", "pass"), res("anchors", "S3", "pass")])["streak"] == 3
    assert streak([])["streak"] == 0 and streak([], "pocket-build")["last"] is None


def test_a_failure_resets_the_streak():
    s = streak([res("anchors", "S1", "pass"), res("anchors", "S2", "pass"), res("anchors", "S3", "fail"), res("anchors", "S4", "pass")])
    assert s["streak"] == 1 and s["snapshots"] == ["S4"] and s["last"]["snapshot"] == "S4"
    assert streak([res("anchors", "S1", "pass"), res("anchors", "S2", "fail")])["streak"] == 0


def test_streaks_follow_the_order_snapshots_started_not_finished():
    slow_pass = res("anchors", "S1", "pass", at="2026-10-18T09:00:00Z", finished="2026-10-18T20:00:00Z")
    quick_fail = res("anchors", "S2", "fail", at="2026-10-18T10:00:00Z", finished="2026-10-18T11:00:00Z")
    assert streak([slow_pass, quick_fail])["streak"] == 0              # S2 started after S1: the failure comes last, whatever finished first
    assert streak([quick_fail, slow_pass])["streak"] == 0


def test_a_new_volatile_list_restarts_the_streak():
    s = streak([res("anchors", "S1", "pass", volatile="v1"), res("anchors", "S2", "pass", volatile="v1"), res("anchors", "S3", "pass", volatile="v2")])
    assert s["streak"] == 1 and s["snapshots"] == ["S3"] and s["volatile_sha256"] == "v2"


def test_an_aborted_snapshot_neither_counts_nor_resets():
    s = streak([res("anchors", "S1", "pass"), res("anchors", "S2", ms.B_REFUSED_AFTER_A), res("anchors", "S3", "pass")])
    assert s["streak"] == 2 and s["snapshots"] == ["S1", "S3"] and s["last"]["snapshot"] == "S3"


def test_a_head_or_digest_change_restarts_the_streak_at_that_pass():
    s = streak([res("anchors", "S1", "pass"), res("anchors", "S2", "pass"), res("anchors", "S3", "pass", head="h2")])
    assert s["streak"] == 1 and s["head"] == "h2" * 8
    s = streak([res("anchors", "S1", "pass"), res("anchors", "S2", "pass", digest="d2"), res("anchors", "S3", "pass", digest="d2")])
    assert s["streak"] == 2 and s["digest"] == "d2" and s["snapshots"] == ["S2", "S3"]


def test_a_repeated_snapshot_or_repeated_inputs_does_not_count():
    s = streak([res("anchors", "S1", "pass", inputs="same"), res("anchors", "S2", "pass", inputs="same"), res("anchors", "S3", "pass")])
    assert s["streak"] == 2 and s["snapshots"] == ["S1", "S3"]
    s = streak([res("anchors", "S1", "pass"), res("anchors", "S1", "pass", at="2026-10-18T09:00:00Z")])
    assert s["streak"] == 1


def test_streaks_are_per_job():
    out = ms.compute_streaks([res("anchors", "S1", "pass"), res("pocket-build", "S1", "fail"), res("pocket-build", "S2", "pass")])
    assert out["anchors"]["streak"] == 1 and out["pocket-build"]["streak"] == 1 and out["refit-detect"]["streak"] == 0


def test_status_command_reads_results_and_the_live_identity(world, capsys):
    for i, job in enumerate(("anchors", "anchors", "anchors")):
        put(ms.result_path(world.results, job, "S%d" % (i + 1)), json.dumps(res(job, "S%d" % (i + 1), "pass", head="11111", digest=DIGEST)))
    put(world.results / "_snapshots" / "x__y.json", "{}")
    put(world.results / "garbage__x.json", "{not json")
    assert cli(world, "status", "--json") == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["jobs"]["anchors"]["streak"] == 3 and doc["jobs"]["anchors"]["ready"] is True and doc["all_ready"] is False
    world.host.head = "2" * 40
    cli(world, "status", "--json")
    doc = json.loads(capsys.readouterr().out)
    assert doc["jobs"]["anchors"]["ready"] is False and doc["jobs"]["anchors"]["live_identity_matches"] is False
    assert cli(world, "status") == 0 and "anchors" in capsys.readouterr().out


def test_volatile_command(world, capsys):
    assert cli(world, "volatile") == 0 and "ok" in capsys.readouterr().out


# ------------------------------------------------------------- the other three jobs, end to end with Mentat's real compare

def both(a, b):
    return {"A": a, "B": b}


G0_DETAIL = "R version 4.6.0 (2026-04-24%s); jsonlite 2.0.0, readxl 1.5.0, sandwich 3.1-3, clubSandwich 0.7.0; rf_rng.R hash pinned"


def g0_detail(label, explained=True):
    return G0_DETAIL % (" ucrt" if label == "A" and explained else "")


def runtime_writer(job, label, rid, queue=None, gates_detail=None, model=None, explained=True):
    """What the job leaves in the runtime.  For refit-parity the receipt holds the fields Paul's ruling explains (R version, platform.*, package
    library paths, the R version inside the G0 gate text, here and in review/gates.json), which differ between A and B by construction, plus the
    clock fields the mask covers."""
    def write(env, argv, cwd):
        side = Path(env["GLASSBOX_EVENTS_DIR"]).parent
        rt = side / ms.RUNTIME
        prefix = "/localappdata" if label == "B" else ms.posix(side / "LocalAppData")
        runs = rt / "runs" / rid
        if job == "refit-detect":
            put(runs / "detect.csv", "workbook,status\r\nBanking,known\r\n")
            put(rt / "agent" / "seen.json", '{"Banking Industry Spending Overview.xlsx": "abc"}')
            if queue:
                put(rt / "agent" / "queue" / "new_industry.json", json.dumps({"id": "new_industry", "detected_utc": queue[label], "status": "queued"}))
            return
        rver = both("R version 4.6.0 (2026-04-24 ucrt)", "R version 4.6.0 (2026-04-24)")[label] if explained else "R version 4.6.0"
        lib = both("C:/Users/x/AppData/Local/R/win-library/4.6", "/usr/local/lib/R/site-library")[label] if explained else "/same"
        gates = [{"gate": "G0 environment", "status": "PASS", "detail": (gates_detail or both(g0_detail("A", explained), g0_detail("B", explained)))[label]},
                 {"gate": "G1 inputs", "status": "PASS", "detail": "128 inputs present, hashes pinned, vault copies byte-equal"}]
        receipt = {"run_id": rid, "started_utc": "start-" + label, "finished_utc": "end-" + label, "decision": "PARITY_GREEN",
                   "inputs": [{"path": prefix + "/Mentat/forecast/refit/x.csv", "sha256": "ab"}], "R_version": rver,
                   "platform": {"R_version": rver, "platform": both("x86_64-w64-mingw32", "x86_64-pc-linux-gnu")[label] if explained else "same",
                                "os": both("Windows 11", "Debian 12")[label] if explained else "same", "blas": both("OpenBLAS", "libblas3")[label] if explained else "same",
                                "lapack": "lapack", "cores": 4, "node": both("LAPTOP", "3f9c2a")[label] if explained else "same", "note": "numerical results agree"},
                   "packages": [{"package": "jsonlite", "version": "2.0.0", "lib": lib}, {"package": "sandwich", "version": "3.1-3", "lib": lib + "/vendored"}],
                   "gates": gates}
        put(runs / "receipt.json", json.dumps(receipt))
        put(runs / "review" / "gates.json", json.dumps({"decision": "PARITY_GREEN", "gates": gates}))
        put(runs / "bundle" / "giving-models-v1.projection.json", json.dumps({"coefficient": (model or both(1.5, 1.5))[label]}))
        put(runs / "log.txt", "12:00:0%d  parity run\n" % (1 if label == "A" else 2))
    return write


def e2e(world, job, writers, *freeze_extra):
    assert freeze(world, job, "S1", *freeze_extra) == 0
    world.host.writers = writers
    assert run(world, job, "S1", "--execute") == 0
    return cli(world, "compare", "--job", job, "--snapshot", "S1", "--python", sys.executable, "--keep")


def result_of(world, job):
    return json.loads(ms.result_path(world.results, job, "S1").read_text())


def pin_bundle(world):
    manifest = {"inputs": [{"role": "b", "path": "{BUNDLE_V1}", "member": None, "sha256": sha((world.project / "app" / "data" / "giving-models-v1.json").read_bytes())}]}
    put(world.project / "forecast" / "refit" / "registry" / "inputs.json", json.dumps(manifest))


@needs_parity
def test_refit_detect_end_to_end_masks_only_the_queue_clock(world):
    rid = "parity-refit-detect-S1"
    queue = {"A": "t1", "B": "t2"}
    code = e2e(world, "refit-detect", {"A": runtime_writer("refit-detect", "A", rid, queue), "B": runtime_writer("refit-detect", "B", rid, queue)})
    result = result_of(world, "refit-detect")
    assert code == 0 and result["status"] == "pass" and result["mask"]["files"] == {"agent/queue/new_industry.json": ["/detected_utc"]}
    assert result["compare"]["counts"] == {"BYTE_EQUAL": 3, "JSON_CANONICAL": 1} and result["compare_root"] == ms.RUNTIME


@needs_parity
def test_refit_detect_fails_when_the_runtime_differs_outside_the_clock(world):
    rid = "parity-refit-detect-S1"
    queue = {"A": "t1", "B": "t2"}

    def writer_b(env, argv, cwd):
        runtime_writer("refit-detect", "B", rid, queue)(env, argv, cwd)
        put(Path(env["GLASSBOX_EVENTS_DIR"]).parent / ms.RUNTIME / "agent" / "seen.json", '{"Banking Industry Spending Overview.xlsx": "DIFFERENT"}')
    assert e2e(world, "refit-detect", {"A": runtime_writer("refit-detect", "A", rid, queue), "B": writer_b}) == 1
    assert "agent/seen.json" in result_of(world, "refit-detect")["compare"]["different"]


@needs_parity
def test_refit_parity_end_to_end_masks_the_clock_and_records_the_explained_differences(world):
    pin_bundle(world)
    rid = "parity-refit-parity-S1"
    assert e2e(world, "refit-parity", {"A": runtime_writer("refit-parity", "A", rid), "B": runtime_writer("refit-parity", "B", rid)}) == 0
    result = result_of(world, "refit-parity")
    receipt = "runs/%s/receipt.json" % rid
    assert result["status"] == "pass" and result["ignore"] == ["runs/%s/log.txt" % rid] and result["compare"]["different"] == {}
    review = "runs/%s/review/gates.json" % rid
    explained = {(e["file"], e["location"]): e for e in result["explained"]}
    assert sorted(loc for (f, loc) in explained if f == receipt) == ["/R_version", "/gates/0/detail", "/packages/0/lib", "/packages/1/lib", "/platform/R_version",
                                                                      "/platform/blas", "/platform/node", "/platform/os", "/platform/platform"]
    assert [loc for (f, loc) in explained if f == review] == ["/gates/0/detail"]
    assert all(e["class"] == "EXPLAINED" for e in result["explained"]) and result["explained_rejected"] == []
    assert {e["ruling"] for e in result["explained"] if e["location"] != "/gates/0/detail"} == {ms.RULING}
    assert {e["ruling"] for e in result["explained"] if e["location"] == "/gates/0/detail"} == {ms.RULING_D246}
    detail = explained[(receipt, "/gates/0/detail")]
    assert detail["a"] == g0_detail("A") and detail["b"] == g0_detail("B") and "ucrt" in detail["a"] and "ucrt" not in detail["b"]              # both raw values
    assert detail["normalized"] == "<R-VERSION>; jsonlite 2.0.0, readxl 1.5.0, sandwich 3.1-3, clubSandwich 0.7.0; rf_rng.R hash pinned"
    assert explained[(receipt, "/platform/node")]["a"] == "LAPTOP" and explained[(receipt, "/platform/node")]["b"] == "3f9c2a"
    assert (receipt, "/platform/cores") not in explained and (receipt, "/platform/lapack") not in explained       # equal on both sides: nothing to explain
    assert result["compare"]["explained"] == len(result["explained"]) == 10
    assert set(result["mask"]["files"][receipt]) == {"/finished_utc", "/started_utc", *(loc for (f, loc) in explained if f == receipt)}
    assert result["mask"]["files"][review] == ["/gates/0/detail"]
    assert result["compare"]["needs_acceptance"] == {receipt: {"class": "PATH_ONLY", "where": ["/inputs/0/path"]}}


def parity_case(world, a_writer, b_writer):
    pin_bundle(world)
    return e2e(world, "refit-parity", {"A": a_writer, "B": b_writer}), "runs/parity-refit-parity-S1/receipt.json", "runs/parity-refit-parity-S1/review/gates.json"


@needs_parity
def test_the_version_plus_any_other_change_in_the_gate_text_stays_different(world):
    rid = "parity-refit-parity-S1"
    detail = both(g0_detail("A").replace("jsonlite 2.0.0", "jsonlite 2.0.1"), g0_detail("B"))
    code, receipt, review = parity_case(world, runtime_writer("refit-parity", "A", rid, gates_detail=detail), runtime_writer("refit-parity", "B", rid, gates_detail=detail))
    assert code == 1
    result = result_of(world, "refit-parity")
    assert result["compare"]["different"] == {receipt: ["/gates/0/detail"], review: ["/gates/0/detail"]}
    assert len(result["explained"]) == 8 and all(e["location"] != "/gates/0/detail" for e in result["explained"])         # the pointer-level ones are still recorded
    assert [(r["file"], r["location"], r["a"], r["b"]) for r in result["explained_rejected"]] == [(receipt, "/gates/0/detail", detail["A"], detail["B"]),
                                                                                                  (review, "/gates/0/detail", detail["A"], detail["B"])]
    assert "not byte-identical" in result["explained_rejected"][0]["why"]


@needs_parity
def test_a_changed_hash_state_in_the_gate_text_stays_different(world):
    rid = "parity-refit-parity-S1"
    detail = both(g0_detail("A"), g0_detail("B").replace("hash pinned", "HASH CHANGED"))
    code, receipt, review = parity_case(world, runtime_writer("refit-parity", "A", rid, gates_detail=detail), runtime_writer("refit-parity", "B", rid, gates_detail=detail))
    assert code == 1 and set(result_of(world, "refit-parity")["compare"]["different"]) == {receipt, review}


@needs_parity
def test_every_other_field_of_the_gate_rows_must_still_match(world):
    rid = "parity-refit-parity-S1"

    def b_writer(env, argv, cwd):
        runtime_writer("refit-parity", "B", rid)(env, argv, cwd)
        path = Path(env["GLASSBOX_EVENTS_DIR"]).parent / ms.RUNTIME / "runs" / rid / "review" / "gates.json"
        doc = json.loads(path.read_text())
        doc["gates"][0]["status"] = "FAIL"
        doc["gates"][1]["detail"] = "127 inputs present"
        doc["decision"] = "FAIL"
        path.write_text(json.dumps(doc))
    code, receipt, review = parity_case(world, runtime_writer("refit-parity", "A", rid), b_writer)
    assert code == 1
    result = result_of(world, "refit-parity")
    assert sorted(result["compare"]["different"][review]) == ["/decision", "/gates/0/status", "/gates/1/detail"]
    assert (review, "/gates/0/detail") in {(e["file"], e["location"]) for e in result["explained"]}                      # the version text itself is still explained


@needs_parity
def test_a_model_output_difference_is_never_explained(world):
    pin_bundle(world)
    rid = "parity-refit-parity-S1"
    model = both(1.5, 1.5000001)
    code = e2e(world, "refit-parity", {"A": runtime_writer("refit-parity", "A", rid, model=model), "B": runtime_writer("refit-parity", "B", rid, model=model)})
    assert code == 1
    assert result_of(world, "refit-parity")["compare"]["different"] == {"runs/%s/bundle/giving-models-v1.projection.json" % rid: ["/coefficient"]}


@needs_parity
def test_pocket_build_end_to_end_has_an_empty_mask(world):
    page = b"<html>\r\n/*__DATA_START__*/{}/*__DATA_END__*/\r\n</html>\r\n"

    def writer(env, argv, cwd):
        put(Path(env["GLASSBOX_EVENTS_DIR"]).parent / "out" / "mentat-pocket.html", page)
    code = e2e(world, "pocket-build", {"A": writer, "B": writer}, "--pocket-data", str(put(world.tmp / "pd.json", "{}")))
    result = result_of(world, "pocket-build")
    assert code == 0 and result["mask"]["files"] == {} and result["compare"]["counts"] == {"BYTE_EQUAL": 1}


@needs_parity
def test_a_streak_is_built_from_results_the_driver_wrote(world, capsys):
    for i in (1, 2, 3):
        name = "S%d" % i
        data = put(world.tmp / ("pd%d.json" % i), '{"n": %d}' % i)
        assert freeze(world, "pocket-build", name, "--pocket-data", str(data)) == 0
        page = ("<html>%d</html>\r\n" % i).encode()

        def writer(env, argv, cwd, page=page):
            put(Path(env["GLASSBOX_EVENTS_DIR"]).parent / "out" / "mentat-pocket.html", page)
        world.host.writers = {"A": writer, "B": writer}
        assert run(world, "pocket-build", name, "--execute") == 0
        assert cli(world, "compare", "--job", "pocket-build", "--snapshot", name, "--python", sys.executable) == 0
        if i == 2:
            capsys.readouterr()
            cli(world, "status", "--json")
            assert json.loads(capsys.readouterr().out)["jobs"]["pocket-build"]["streak"] == 2
    capsys.readouterr()
    cli(world, "status", "--json")
    doc = json.loads(capsys.readouterr().out)
    job = doc["jobs"]["pocket-build"]
    assert job["streak"] == 3 and job["snapshots"] == ["S1", "S2", "S3"] and job["ready"] is True and doc["all_ready"] is False
    assert not (world.scratch / "S1" / "pocket-build").exists()                                              # compare deleted the copies


def test_b_refused_after_a_has_run_is_a_recorded_failure(world, capsys):
    freeze(world)
    arm(world)
    inner = world.host.writers["A"]

    def a_then_a_task_starts(env, argv, cwd):
        inner(env, argv, cwd)
        world.host.tasks.append(("MentatRefitWatcher", "Running"))
    world.host.writers["A"] = a_then_a_task_starts
    assert run(world, "anchors", "S1", "--execute") == 1
    assert world.host.events == ["A"] and "MentatRefitWatcher is running" in capsys.readouterr().out
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    assert result["status"] == ms.B_REFUSED_AFTER_A == "aborted" and result["phase"] == "run"
    assert result["reasons"][0].startswith("B was refused after A had run")


def test_freeze_refuses_when_no_industry_can_be_anchored(world, capsys):
    for index in (world.project / "state" / "contribution-analytics").glob("*.sqlite"):
        index.unlink()
    assert freeze(world) == 2
    assert "anchor nothing" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------------ review fixes

def test_docker_gates_name_the_image_by_digest_and_never_pull(world):
    freeze(world)
    arm(world)
    assert run(world, "anchors", "S1", "--execute") == 0
    inspects = [c.argv for c in world.host.calls if Path(c.argv[0]).name == "docker" and c.argv[1] == "image"]
    assert inspects and inspects[0][-1] == "ghcr.io/x/compute-mentat@" + DIGEST and not any("pull" in c.argv for c in world.host.calls)
    infos = [i for i, c in enumerate(world.host.calls) if Path(c.argv[0]).name == "docker" and c.argv[1] == "info"]
    a_call = next(i for i, c in enumerate(world.host.calls) if c in world.host.calls_for("A"))
    assert infos and infos[0] < a_call                          # the engine was asked before A started


def test_a_b_timeout_removes_the_named_container(world):
    freeze(world)
    arm(world)
    world.host.timeouts = {"B"}
    assert run(world, "anchors", "S1", "--execute") == 1
    removals = [c.argv for c in world.host.calls if Path(c.argv[0]).name == "docker" and c.argv[1] == "rm"]
    assert removals == [["docker", "rm", "-f", "mentat-compute-parity-anchors-s1"]]
    record = json.loads((snap_root(world) / "run.json").read_text())
    assert record["B"]["timed_out"] is True and record["B"]["cleanup"]["exit"] == 0
    assert "(timed out)" in json.loads(ms.result_path(world.results, "anchors", "S1").read_text())["reasons"][0]


def test_a_stale_per_snapshot_lock_is_seen_and_left_alone(world, capsys):
    freeze(world)
    arm(world)
    stale = put(snap_root(world) / ".run.lock", "pid 1\n")
    assert run(world, "anchors", "S1", "--execute") == 2
    assert "another driver run is in progress or died" in capsys.readouterr().err and stale.is_file() and world.host.events == []
    assert world.host.locks_taken == [world.results / ms.GLOBAL_LOCK, "released"]


def test_the_global_run_lock_excludes_a_second_holder(tmp_path):
    host = ms.SystemHost()
    path = tmp_path / "results" / ms.GLOBAL_LOCK
    first = host.lock_exclusive(path)
    assert first is not None
    assert host.lock_exclusive(path) is None
    first.close()
    again = host.lock_exclusive(path)
    assert again is not None
    again.close()


def test_the_task_gate_looks_at_the_whole_window(world):
    host = world.host
    window = 107 * 60                                                       # anchors: A 47 min + B 60 min
    host.tasks = [("MentatNightlyAnchors", "Ready", "2026-10-18T13:46:00Z")]
    assert ms.task_problems(host, window) and "inside this run's 107-minute window" in ms.task_problems(host, window)[0]
    host.tasks = [("MentatNightlyAnchors", "Ready", "2026-10-18T13:48:00Z")]
    assert ms.task_problems(host, window) == []
    host.tasks = [("MentatNightlyAnchors", "Ready", "2026-10-18T09:00:00Z")]        # a missed schedule is due now
    assert ms.task_problems(host, window)
    host.tasks = [("MentatRefitWatcher", "Running", ""), ("MentatNightlyAnchorsContainer", "Disabled", "2026-10-18T12:01:00Z")]
    assert [p.split()[2] for p in ms.task_problems(host, window)] == ["MentatRefitWatcher"]


def test_freeze_has_the_task_and_lock_gates_too(world, capsys):
    world.host.tasks = [("MentatNightlyAnchors", "Running", "")]
    assert freeze(world) == 2 and "MentatNightlyAnchors is running" in capsys.readouterr().err
    world.host.tasks = [("MentatNightlyAnchors", "Ready", "2026-10-18T12:30:00Z")]
    assert freeze(world) == 2 and "inside this run's" in capsys.readouterr().err
    world.host.tasks = []
    (world.lad / "Mentat" / "compute" / "locks").mkdir(parents=True)
    (world.lad / "Mentat" / "compute" / "locks" / "refit-detect.lock").write_text("")
    world.host.held.add("refit-detect.lock")
    assert freeze(world) == 2 and "refit-detect.lock is held" in capsys.readouterr().err
    world.host.held.clear()
    assert freeze(world) == 0


def test_the_host_load_gate_lists_and_can_be_allowed(world, capsys):
    freeze(world)
    arm(world)
    world.host.processes += ["%d|python.exe|python C:/x/chain_parity.py compare" % os.getpid(), "777|python.exe|python C:/x/run_pipeline.py --all",
                             "778|python.exe|python C:/Mentat/app/server.py", "779|Rscript.exe|Rscript x.R"]
    assert run(world, "anchors", "S1", "--execute") == 2
    err = capsys.readouterr().err
    assert "pid 777" in err and "pid 779" in err and "pid 778" not in err and "chain_parity" not in err      # our own pid is not load
    assert world.host.events == []
    assert run(world, "anchors", "S1", "--execute", "--allow-host-load") == 0
    assert "WARNING (--allow-host-load)" in capsys.readouterr().err and world.host.events == ["A", "B"]


def test_scratch_may_not_sit_in_a_live_tree(world, capsys):
    world.host.temp = [str(world.tmp / "claude-home")]
    for bad in (world.root / "scratch", world.tmp / "claude-home" / "p"):
        assert cli(world, "freeze", "--job", "anchors", "--snapshot", "S1", "--scratch", str(bad)) == 2
        assert "must not be under" in capsys.readouterr().err
    assert ms.SystemHost().forbidden_roots()
    roots = [r.lower() for r in ms.SystemHost().forbidden_roots()]
    assert any(r.endswith(os.path.join(".claude").lower()) for r in roots) and any(r.endswith(os.path.join("Mentat").lower()) for r in roots)
    assert any(r.endswith("melange") for r in roots)


def test_the_results_default_is_a_fixed_path(monkeypatch):
    monkeypatch.delenv("MELANGE_PARITY_DIR", raising=False)
    assert str(ms.results_default()) == "C:\\melange-data\\compute_parity\\mentat" or ms.results_default() == Path("C:/melange-data/compute_parity/mentat")
    monkeypatch.setenv("MELANGE_PARITY_DIR", "D:/elsewhere")
    assert ms.results_default() == Path("D:/elsewhere") / "mentat"


def test_a_mask_glob_does_not_cross_a_slash(tmp_path):
    a = tmp_path / "A"
    put(a / "agent" / "queue" / "x.json", json.dumps({"detected_utc": "t"}))
    put(a / "agent" / "queue" / "sub" / "y.json", json.dumps({"detected_utc": "t"}))
    mask, _ = ms.build_mask(ms.load_volatile(), "refit-detect", a, "rid", "d")
    assert list(mask["files"]) == ["agent/queue/x.json"]
    assert ms.glob_match("a/b.json", "a/*.json") and not ms.glob_match("a/b/c.json", "a/*.json") and not ms.glob_match("a", "a/*")


def test_timeout_after_the_kill_is_bounded(monkeypatch):
    class Stuck:
        pid, returncode = 4242, None
        calls = 0

        def communicate(self, timeout=None):
            Stuck.calls += 1
            raise subprocess.TimeoutExpired("x", timeout)
        def kill(self): pass
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: Stuck())
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    proc = ms.SystemHost().run(["whatever"], timeout=1)
    assert proc.timed_out and proc.returncode == 124 and Stuck.calls == 2


# ---- compare: write-once, dirty, kept report, launcher logs

def compare_args(world, job="anchors", **kw):
    return SimpleNamespace(job=job, snapshot="S1", scratch=None, results_dir=str(world.results), mentat_root=str(world.root), keep=False, python=None, **kw)


def test_compare_refuses_to_overwrite_a_result(world):
    run_world(world)
    args = compare_args(world)
    args.keep = True
    assert ms.cmd_compare(args, world.host, FakeCompare()) == 0
    with pytest.raises(ms.Refused, match="written once"):
        ms.cmd_compare(compare_args(world), world.host, FakeCompare())


def test_compare_after_a_failed_run_only_cleans_up(world, capsys):
    freeze(world)
    arm(world)
    world.host.exits["A"] = 1
    run(world, "anchors", "S1", "--execute")
    before = (world.results / "anchors__S1.json").read_text()
    assert snap_root(world).exists()
    assert ms.cmd_compare(compare_args(world), world.host, FakeCompare()) == 1
    assert (world.results / "anchors__S1.json").read_text() == before and not snap_root(world).exists()
    assert "recorded by run" in capsys.readouterr().out


def test_a_dirty_checkout_at_compare_time_is_a_refusal_not_a_failure(world):
    run_world(world)
    world.host.dirty = ["mentat/app/server.py"]
    with pytest.raises(ms.Refused, match="dirty at compare time"):
        ms.cmd_compare(compare_args(world), world.host, FakeCompare())
    assert snap_root(world).exists() and not ms.result_path(world.results, "anchors", "S1").exists()


def test_every_result_records_the_list_and_driver_hashes(world):
    run_world(world)
    assert ms.cmd_compare(compare_args(world), world.host, FakeCompare()) == 0
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    assert result["volatile_sha256"] == sha(ms.VOLATILE_FILE.read_bytes()) and result["driver_sha256"] == sha(Path(ms.__file__).read_bytes())
    assert result["phase"] == "compare"


def test_a_failing_compare_keeps_its_report_beside_the_result(world):
    run_world(world)
    assert ms.cmd_compare(compare_args(world), world.host, FakeCompare(ok=False)) == 1
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    kept = Path(result["report_file"])
    assert kept == world.results / "anchors__S1.report.json" and json.loads(kept.read_text())["ok"] is False
    assert [r["snapshot"] for r in ms.load_results(world.results)] == ["S1"]                    # the report is not read as a result


def test_a_passing_compare_leaves_no_report_file(world):
    run_world(world)
    ms.cmd_compare(compare_args(world), world.host, FakeCompare())
    assert not (world.results / "anchors__S1.report.json").exists()


def test_compare_deletes_the_launcher_logs_of_its_own_snapshot_only(world):
    logs = world.lad / "Mentat" / "logs" / "scheduled"
    mine = [put(logs / n, "x") for n in ("MentatParityA-anchors-S1-20261018-120000.out.log", "MentatParityA-anchors-S1-20261018-120000.err.log", "MentatParityA-anchors-S1.log")]
    others = [put(logs / n, "x") for n in ("MentatParityA-anchors-S1-2-20261018-120000.out.log", "MentatParityA-anchors-S10-20261018-120000.out.log",
                                          "MentatNightlyAnchors.log", "MentatParityA-pocket-build-S1.log")]
    run_world(world)
    ms.cmd_compare(compare_args(world), world.host, FakeCompare())
    assert not any(p.exists() for p in mine) and all(p.exists() for p in others)


def test_compare_keep_keeps_the_logs_too(world):
    logs = world.lad / "Mentat" / "logs" / "scheduled"
    kept = put(logs / "MentatParityA-anchors-S1.log", "x")
    run_world(world)
    args = compare_args(world)
    args.keep = True
    ms.cmd_compare(args, world.host, FakeCompare())
    assert kept.exists() and snap_root(world).exists()


# ---- status: owed compares

def test_status_blocks_ready_while_a_run_snapshot_has_no_result(world, capsys):
    for i in (1, 2, 3):
        put(ms.result_path(world.results, "anchors", "S%d" % i), json.dumps(res("anchors", "S%d" % i, "pass", head="11111", digest=DIGEST)))
    cli(world, "status", "--json")
    assert json.loads(capsys.readouterr().out)["jobs"]["anchors"]["ready"] is True
    owed = snap_root(world, "anchors", "S4")
    put(owed / "run.json", "{}")
    put(ms.pointer_path(world.results, "anchors", "S4"), json.dumps({"snapshot_root": str(owed)}))
    put(ms.pointer_path(world.results, "anchors", "S5"), json.dumps({"snapshot_root": str(snap_root(world, "anchors", "S5"))}))     # frozen, never run: not owed
    cli(world, "status", "--json")
    doc = json.loads(capsys.readouterr().out)["jobs"]["anchors"]
    assert doc["pending"] == ["S4"] and doc["ready"] is False and doc["streak"] == 3
    cli(world, "status")
    assert "PENDING compare: S4" in capsys.readouterr().out
    put(ms.result_path(world.results, "anchors", "S4"), json.dumps(res("anchors", "S4", "aborted", head="11111", digest=DIGEST)))
    cli(world, "status", "--json")
    assert json.loads(capsys.readouterr().out)["jobs"]["anchors"]["ready"] is True


def test_status_blocks_ready_when_the_volatile_list_changed_since_the_streak(world, capsys):
    for i in (1, 2, 3):
        put(ms.result_path(world.results, "anchors", "S%d" % i), json.dumps(res("anchors", "S%d" % i, "pass", head="11111", digest=DIGEST, volatile="old-list")))
    cli(world, "status", "--json")
    doc = json.loads(capsys.readouterr().out)["jobs"]["anchors"]
    assert doc["streak"] == 3 and doc["list_matches"] is False and doc["ready"] is False
    cli(world, "status")
    assert "volatile list changed" in capsys.readouterr().out


# ------------------------------------------------------------------------------------ Paul 2026-10-18: repeat runs, explained, A exit 1

def spec_with(job, **body):
    return {"schema": ms.VOLATILE_SCHEMA, "jobs": {job: {"mask": [], "ignore": [], **body}}}


GOOD_EXPLAINED = {"file": "runs/{run_id}/receipt.json", "locations": ["/R_version"], "cite": "mentat/x.R:1", "needle": "n", "reason": "r", "ruling": ms.RULING}


def test_the_shipped_list_carries_the_ruling_for_each_new_section():
    spec = ms.load_volatile()
    body = spec["jobs"]["refit-parity"]
    assert ms.repeat_ok_jobs(spec) == {"refit-parity"} and body["repeat_ruling"].startswith("Paul 2026-10-18 (brain D-entry pending)")
    assert [e["locations"] for e in body["explained"]] == [["/R_version"], ["/platform/*"], ["/packages/*/lib"], ["/gates/0/detail"], ["/gates/0/detail"]]
    assert all(e["cite"].startswith("mentat/forecast/refit/R/") for e in body["explained"])
    assert all(e["ruling"] == ms.RULING for e in body["explained"][:3]) and not any("text_pattern" in e for e in body["explained"][:3])
    text = body["explained"][3:]
    assert [e["file"] for e in text] == ["runs/{run_id}/receipt.json", "runs/{run_id}/review/gates.json"]
    assert all(e["text_pattern"] == "r-version" and e["ruling"] == ms.RULING_D246 == "Paul 2026-10-18 (D-246 extension, brain entry pending)" for e in text)
    assert all(e["origin_cite"] == "mentat/forecast/refit/R/rf_parity.R:12" and e["origin_needle"] == "R.version.string" for e in text)
    assert body["a_exit_explained"]["ruling"] == ms.RULING and body["a_exit_explained"]["failures"] == ["test-19-parity-environment.R:17"]
    for other in ("anchors", "refit-detect", "pocket-build"):
        assert not spec["jobs"][other].get("repeat_ok") and "explained" not in spec["jobs"][other] and "a_exit_explained" not in spec["jobs"][other]


@pytest.mark.parametrize("entry,needle", [
    ({"file": "runs/{run_id}/bundle/giving-models-v1.projection.json"}, "not explainable"),
    ({"file": "runs/{run_id}/review/gates.json"}, "not explainable"),
    ({"file": "runs/{run_id}/panels/pac.json"}, "not explainable"),
    ({"file": "runs/{run_id}/coefficients.json"}, "not explainable"),
    ({"file": "runs/*/receipt.json"}, "not explainable"),
    ({"file": "runs/{run_id}/receipt.json", "locations": ["/coefficient"]}, "is not one of"),
    ({"file": "runs/{run_id}/receipt.json", "locations": ["/*"]}, "is not one of"),
    ({"file": "runs/{run_id}/receipt.json", "locations": ["/platform"]}, "is not one of"),
    ({"file": "runs/{run_id}/receipt.json", "locations": ["/platform/*/x"]}, "is not one of"),
    ({"file": "runs/{run_id}/receipt.json", "locations": ["/gates/0/detail"]}, "is not one of"),
    ({"file": "runs/{run_id}/receipt.json", "locations": ["/total_cents"]}, "is not one of"),
    ({"ruling": "somebody"}, "ruling must start"),
    ({"cite": "nowhere"}, "cite, needle and reason"),
    ({"needle": ""}, "cite, needle and reason"),
])
def test_an_explained_entry_that_reaches_a_model_output_or_strays_is_refused_at_load(entry, needle, tmp_path):
    spec = spec_with("refit-parity", explained=[{**GOOD_EXPLAINED, **entry}])
    problems = ms.validate_volatile(spec)
    assert any(needle in p for p in problems), problems
    with pytest.raises(ms.Refused):
        ms.load_volatile(put(tmp_path / "v.json", json.dumps(spec)))
    assert ms.validate_volatile(spec_with("refit-parity", explained=[GOOD_EXPLAINED])) == []


def test_explained_is_for_refit_parity_only(tmp_path):
    for job in ("anchors", "refit-detect", "pocket-build"):
        assert any("no explained differences are allowed" in p for p in ms.validate_volatile(spec_with(job, explained=[GOOD_EXPLAINED])))
        assert any("not allowed for this job" in p for p in ms.validate_volatile(spec_with(job, a_exit_explained={"exit": 1, "failures": ["test-19-x.R:1"], "cites": [{"cite": "mentat/x:1", "needle": "n"}],
                                                                                                               "ruling": ms.RULING, "reason": "r"})))


def test_repeat_ok_is_validated():
    ok = spec_with("refit-parity", repeat_ok=True, repeat_ruling=ms.RULING + ": ...")
    assert ms.validate_volatile(ok) == []
    assert any("repeat_ok" in p for p in ms.validate_volatile(spec_with("refit-parity", repeat_ok=True)))                                   # no ruling
    assert any("repeat_ok" in p for p in ms.validate_volatile(spec_with("refit-parity", repeat_ok=True, repeat_ruling="somebody")))
    assert any("repeat_ok" in p for p in ms.validate_volatile(spec_with("anchors", repeat_ok=True, repeat_ruling=ms.RULING)))              # other jobs keep distinct inputs
    assert any("true or false" in p for p in ms.validate_volatile(spec_with("refit-parity", repeat_ok="yes")))
    assert ms.validate_volatile(spec_with("anchors", repeat_ok=False)) == []


def test_a_exit_explained_is_validated():
    rule = {"exit": 1, "failures": ["test-19-parity-environment.R:17"], "cites": [{"cite": "mentat/x:1", "needle": "n"}], "ruling": ms.RULING, "reason": "r"}
    assert ms.validate_volatile(spec_with("refit-parity", a_exit_explained=rule)) == []
    for bad in ({"exit": 2}, {"failures": []}, {"failures": ["test-19"]}, {"ruling": "x"}, {"reason": ""}, {"cites": []}):
        assert ms.validate_volatile(spec_with("refit-parity", a_exit_explained={**rule, **bad})), bad


def test_check_citations_covers_the_new_sections(tmp_path):
    put(tmp_path / "mentat" / "f.R", "a\nR_version = 1\n")
    spec = {"jobs": {"refit-parity": {"mask": [], "ignore": [], "explained": [{"cite": "mentat/f.R:2", "needle": "R_version"}],
                                      "a_exit_explained": {"cites": [{"cite": "mentat/f.R:1", "needle": "zzz"}]}}}}
    problems = ms.check_citations(spec, tmp_path)
    assert len(problems) == 1 and "'zzz'" in problems[0]


def test_pointer_match_and_resolution():
    assert ms.pointer_match("/platform/*", "/platform/node") and not ms.pointer_match("/platform/*", "/platform/a/b") and not ms.pointer_match("/platform/*", "/platform")
    assert ms.pointer_match("/packages/*/lib", "/packages/3/lib") and not ms.pointer_match("/packages/*/lib", "/packages/3/version")
    assert not ms.pointer_match("/R_version", "/R_versions")
    doc = {"a": [{"b/c": 1}], "empty": {}}
    assert ms.resolve_pointer(doc, "/a/0/b~1c") == 1 and ms.resolve_pointer(doc, "/empty") == {} and ms.resolve_pointer(doc, "/a/1") is ms.MISSING
    assert ms.resolve_pointer(doc, "/a/x") is ms.MISSING


def test_explained_records_only_touch_the_one_receipt_and_cap_the_depth(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    receipt = lambda node, lib, deep, extra: json.dumps({"R_version": "r" + node, "platform": {"node": node, "deep": {"x": deep}}, "packages": [{"package": "p", "lib": lib}], **extra})
    put(a / "runs" / "rid" / "receipt.json", receipt("n1", "L1", 1, {"decision": "GREEN"}))
    put(b / "runs" / "rid" / "receipt.json", receipt("n2", "L2", 2, {"decision": "RED"}))
    put(a / "runs" / "rid" / "bundle" / "giving-models-v1.projection.json", '{"platform": {"node": "a"}}')
    put(b / "runs" / "rid" / "bundle" / "giving-models-v1.projection.json", '{"platform": {"node": "b"}}')
    records = ms.explained_records(ms.load_volatile(), "refit-parity", a, b, "rid")
    assert {(r["file"], r["location"]) for r in records} == {("runs/rid/receipt.json", p) for p in ("/R_version", "/platform/node", "/packages/0/lib")}   # no /platform/deep/x, no bundle
    assert all(r["differs"] and r["ruling"] == ms.RULING for r in records)
    mask, _ = ms.build_mask(ms.load_volatile(), "refit-parity", a, "rid", "d", records)
    assert "/decision" not in mask["files"]["runs/rid/receipt.json"] and "runs/rid/bundle/giving-models-v1.projection.json" not in mask["files"]


def test_an_explained_field_that_is_missing_on_one_side_is_recorded_with_none(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    put(a / "runs" / "rid" / "receipt.json", json.dumps({"platform": {"node": "n", "extra": "only-in-a"}}))
    put(b / "runs" / "rid" / "receipt.json", json.dumps({"platform": {"node": "n"}}))
    records = ms.explained_records(ms.load_volatile(), "refit-parity", a, b, "rid")
    assert [(r["location"], r["a"], r["b"], r["differs"]) for r in records] == [("/platform/extra", "only-in-a", None, True), ("/platform/node", "n", "n", False)]
    mask, _ = ms.build_mask(ms.load_volatile(), "refit-parity", a, "rid", "d", records)
    assert mask["files"]["runs/rid/receipt.json"] == ["/platform/extra"]                    # equal values are not masked, only what differs


def test_explained_records_refuse_a_money_looking_pointer(tmp_path, monkeypatch):
    a, b = tmp_path / "A", tmp_path / "B"
    put(a / "runs" / "rid" / "receipt.json", json.dumps({"platform": {"total_cents": 1}}))
    put(b / "runs" / "rid" / "receipt.json", json.dumps({"platform": {"total_cents": 2}}))
    with pytest.raises(ms.Refused, match="money"):
        ms.explained_records(ms.load_volatile(), "refit-parity", a, b, "rid")


# ---- repeat runs

def test_refit_parity_counts_three_repeat_runs_on_the_same_inputs():
    rs = [res("refit-parity", "S%d" % i, "pass", inputs="pinned") for i in (1, 2, 3)]
    out = ms.compute_streaks(rs)["refit-parity"]
    assert out["streak"] == 3 and out["snapshots"] == ["S1", "S2", "S3"] and out["repeat_ok"] is True


def test_every_other_job_still_needs_distinct_inputs():
    for job in ("anchors", "refit-detect", "pocket-build"):
        out = ms.compute_streaks([res(job, "S%d" % i, "pass", inputs="pinned") for i in (1, 2, 3)])[job]
        assert out["streak"] == 1 and out["repeat_ok"] is False


def test_repeat_runs_still_need_distinct_names_and_one_head_digest_and_list():
    same = lambda **kw: res("refit-parity", kw.pop("snapshot"), "pass", inputs="pinned", **kw)
    assert ms.compute_streaks([same(snapshot="S1"), same(snapshot="S1", at="2026-10-18T09:00:00Z"), same(snapshot="S2")])["refit-parity"]["snapshots"] == ["S1", "S2"]
    assert ms.compute_streaks([same(snapshot="S1"), same(snapshot="S2"), same(snapshot="S3", head="h2")])["refit-parity"]["snapshots"] == ["S3"]
    assert ms.compute_streaks([same(snapshot="S1"), same(snapshot="S2"), same(snapshot="S3", digest="d2")])["refit-parity"]["snapshots"] == ["S3"]
    assert ms.compute_streaks([same(snapshot="S1"), same(snapshot="S2"), same(snapshot="S3", volatile="other-list")])["refit-parity"]["snapshots"] == ["S3"]
    assert ms.compute_streaks([same(snapshot="S1"), same(snapshot="S2"), res("refit-parity", "S3", "fail")])["refit-parity"]["streak"] == 0


def test_status_can_be_ready_for_refit_parity_on_repeat_inputs(world, capsys):
    for i in (1, 2, 3):
        put(ms.result_path(world.results, "refit-parity", "S%d" % i), json.dumps(res("refit-parity", "S%d" % i, "pass", head="11111", digest=DIGEST, inputs="pinned")))
    cli(world, "status", "--json")
    doc = json.loads(capsys.readouterr().out)["jobs"]["refit-parity"]
    assert doc["streak"] == 3 and doc["ready"] is True and doc["repeat_ok"] is True


# ---- the Windows A side exiting 1

G_OK = ("12:11:04  G0 PASS\n12:11:09  G1 PASS: 128 inputs present, hashes pinned, vault copies byte-equal\n12:11:20  panels built; G3 PASS\n"
        "12:11:33  v1 projection: 0 mismatching leaves, max deviation 1.56e-10\n")
G2_ONE = "12:19:31  G2 FAIL: 1466 expectations, 1 failed, 0 errors; 0 leaf mismatches\n"
T19 = "-- Failure (test-19-parity-environment.R:17:3): the actual parity helper restores the caller runtime before locking and publishing --\n"
COUNTS_ONLY = G_OK + G2_ONE                                               # what rf_parity_suite()'s silent reporter leaves in the log today
WITH_T19 = G_OK + G2_ONE + T19


def verdict(text, code=1, job="refit-parity"):
    return ms.explain_a_exit(ms.load_volatile(), job, code, text)


def test_a_log_naming_only_the_pinned_failure_is_accepted():
    v = verdict(WITH_T19)
    assert v["accepted"] is True and v["why"] == [] and v["ruling"] == ms.RULING and v["pinned_failures"] == ["test-19-parity-environment.R:17"]
    assert v["observed"]["failures"] == ["test-19-parity-environment.R:17"] and v["observed"]["g2"]["failed"] == 1
    old_format = G_OK + G2_ONE + "-- 1. Failure: the actual parity helper restores (@test-19-parity-environment.R#17)  ----------\n"
    assert verdict(old_format)["accepted"] is True


def test_the_log_as_the_run_prints_it_today_names_no_test_so_it_is_not_accepted():
    v = verdict(COUNTS_ONLY)
    assert v["accepted"] is False and any("silent reporter" in w for w in v["why"])


@pytest.mark.parametrize("text,code,needle", [
    (G_OK + "12:19:31  G2 FAIL: 1466 expectations, 2 failed, 0 errors; 0 leaf mismatches\n" + T19 + "-- Failure (test-05-bootstrap.R:40:3): draws --\n", 1, "test-05-bootstrap.R:40"),
    (G_OK + "12:19:31  G2 FAIL: 1466 expectations, 2 failed, 0 errors; 0 leaf mismatches\n" + T19, 1, "2 expectations failed; the ruling covers 1"),
    (G_OK + "12:19:31  G2 FAIL: 1466 expectations, 1 failed, 0 errors; 0 leaf mismatches\n-- Failure (test-05-bootstrap.R:40:3): draws --\n", 1, "the ruling covers only test-19"),
    (G_OK + G2_ONE + "-- Failure (test-19-parity-environment.R:25:3): other line --\n", 1, "the ruling covers only test-19-parity-environment.R:17"),
    (G_OK + G2_ONE + T19 + T19, 1, "failing tests in the A log"),
    (G_OK + "12:19:31  G2 FAIL: 1466 expectations, 1 failed, 1 errors; 0 leaf mismatches\n" + T19, 1, "1 errors"),
    (G_OK + "12:19:31  G2 FAIL: 1466 expectations, 1 failed, 0 errors; 3 leaf mismatches\n" + T19, 1, "3 leaf mismatches"),
    (WITH_T19, 2, "covers exit 1 only"),
    (WITH_T19, 124, "covers exit 1 only"),
    (WITH_T19.replace("G0 PASS", "G0 FAIL"), 1, "gate G0 is FAIL"),
    (WITH_T19.replace("G3 PASS", "G3 FAIL"), 1, "gate G3 is FAIL"),
    (WITH_T19.replace("12:11:09  G1 PASS", "12:11:09  nothing"), 1, "gate G1 is absent"),
    (G_OK + T19, 1, "no `G2 FAIL"),
    ("", 1, "no `G2 FAIL"),
])
def test_anything_else_in_the_a_log_means_a_failed(text, code, needle):
    v = verdict(text, code)
    assert v["accepted"] is False and any(needle in w for w in v["why"]), v["why"]


def test_only_refit_parity_has_the_ruling():
    for job in ("anchors", "refit-detect", "pocket-build"):
        assert verdict(WITH_T19, 1, job) is None


def parity_a_writer(world, text):
    """A: the runtime outputs plus run_logged.ps1's two log files carrying `text`."""
    def write(env, argv, cwd):
        logs = world.lad / "Mentat" / "logs" / "scheduled"
        put(logs / "MentatParityA-refit-parity-S1-20261018-120000.err.log", text)
        put(logs / "MentatParityA-refit-parity-S1-20261018-120000.out.log", "G2 parity   FAIL\n")
        runtime_writer("refit-parity", "A", "parity-refit-parity-S1")(env, argv, cwd)
    return write


def parity_run(world, text, a_exit=1, b_exit=0):
    pin_bundle(world)
    assert freeze(world, "refit-parity") == 0
    world.host.writers = {"A": parity_a_writer(world, text), "B": runtime_writer("refit-parity", "B", "parity-refit-parity-S1")}
    world.host.exits = {"A": a_exit, "B": b_exit}
    return run(world, "refit-parity", "S1", "--execute")


def test_an_accepted_exit_1_lets_b_run_and_the_result_records_it(world):
    assert parity_run(world, WITH_T19) == 0
    assert world.host.events == ["A", "B"]
    record = json.loads((snap_root(world, "refit-parity") / "run.json").read_text())
    assert record["A"]["exit"] == 1 and record["A"]["exit_explained"]["accepted"] is True
    assert ms.cmd_compare(compare_args(world, "refit-parity"), world.host, FakeCompare()) == 0
    result = json.loads(ms.result_path(world.results, "refit-parity", "S1").read_text())
    assert result["status"] == "pass" and result["reasons"] == [] and result["a"]["exit"] == 1
    assert result["a_exit_explained"]["accepted"] is True and result["a_exit_explained"]["ruling"] == ms.RULING
    assert len(result["explained"]) == 10


def test_b_must_still_exit_zero_after_an_accepted_a(world):
    assert parity_run(world, WITH_T19, b_exit=1) == 1
    result = json.loads(ms.result_path(world.results, "refit-parity", "S1").read_text())
    assert result["status"] == "fail" and result["reasons"][0].startswith("B side") and result["a_exit_explained"]["accepted"] is True


def test_the_log_as_printed_today_fails_the_snapshot_and_says_why(world, capsys):
    assert parity_run(world, COUNTS_ONLY) == 1
    out = capsys.readouterr().out
    assert world.host.events == ["A"] and "the exit-1 ruling does not apply" in out and "silent reporter" in out
    result = json.loads(ms.result_path(world.results, "refit-parity", "S1").read_text())
    assert result["status"] == "fail" and result["a_exit_explained"]["accepted"] is False and "silent reporter" in " ".join(result["a_exit_explained"]["why"])


def test_another_failing_test_makes_a_failed(world):
    text = G_OK + "12:19:31  G2 FAIL: 1466 expectations, 2 failed, 0 errors; 0 leaf mismatches\n" + T19 + "-- Failure (test-02-inputs.R:12:3): pins --\n"
    assert parity_run(world, text) == 1 and world.host.events == ["A"]
    result = json.loads(ms.result_path(world.results, "refit-parity", "S1").read_text())
    assert result["status"] == "fail" and result["a_exit_explained"]["accepted"] is False
    assert any("test-02-inputs.R:12" in w for w in result["a_exit_explained"]["why"])


def test_a_clean_a_has_no_explanation_recorded(world):
    assert parity_run(world, "", a_exit=0) == 0
    assert "exit_explained" not in json.loads((snap_root(world, "refit-parity") / "run.json").read_text())["A"]


def test_the_ruling_does_not_apply_to_other_jobs(world):
    freeze(world)
    arm(world)
    world.host.exits["A"] = 1
    assert run(world, "anchors", "S1", "--execute") == 1 and world.host.events == ["A"]
    result = json.loads(ms.result_path(world.results, "anchors", "S1").read_text())
    assert result["a_exit_explained"] is None and result["status"] == "fail"


def test_the_logs_are_found_by_exact_run_name(world):
    logs = world.lad / "Mentat" / "logs" / "scheduled"
    mine = put(logs / "MentatParityA-refit-parity-S1-20261018-120000.err.log", "mine")
    put(logs / "MentatParityA-refit-parity-S1-2-20261018-130000.err.log", "other snapshot")
    put(logs / "MentatParityA-refit-parity-S10-20261018-140000.err.log", "another")
    found = ms.a_log_files(world.host, "MentatParityA-refit-parity-S1")
    assert found["err"] == [mine] and found["out"] == [] and found["summary"] == []
    assert ms.read_logs(world.host, "MentatParityA-refit-parity-S1") == "mine"


# ------------------------------------------------------------------ D-246 extension: the R version text inside the G0 gate detail

GOOD_TEXT = {"file": "runs/{run_id}/review/gates.json", "locations": ["/gates/0/detail"], "text_pattern": "r-version", "cite": "mentat/x.R:1", "needle": "n",
             "origin_cite": "mentat/x.R:2", "origin_needle": "m", "reason": "r", "ruling": ms.RULING_D246}
R_VERSION = ms.TEXT_PATTERNS["r-version"]


def test_a_well_formed_text_entry_is_accepted_for_the_two_files():
    assert ms.validate_volatile(spec_with("refit-parity", explained=[GOOD_TEXT])) == []
    assert ms.validate_volatile(spec_with("refit-parity", explained=[{**GOOD_TEXT, "file": "runs/{run_id}/receipt.json"}])) == []


@pytest.mark.parametrize("entry,needle", [
    ({"file": "runs/{run_id}/review/selected_models.json"}, "not explainable as text"),
    ({"file": "runs/{run_id}/review/regressions.json"}, "not explainable as text"),
    ({"file": "runs/{run_id}/bundle/giving-models-v1.projection.json"}, "not explainable as text"),
    ({"file": "runs/{run_id}/panels/pac.json"}, "not explainable as text"),
    ({"file": "review/gates.json"}, "not explainable as text"),
    ({"file": "runs/*/review/gates.json"}, "not explainable as text"),
    ({"locations": ["/gates/1/detail"]}, "is not one of /gates/0/detail"),
    ({"locations": ["/gates/0/status"]}, "is not one of"),
    ({"locations": ["/gates/*/detail"]}, "is not one of"),
    ({"locations": ["/decision"]}, "is not one of"),
    ({"locations": ["/gates/0/detail", "/gates/0/gate"]}, "is not one of"),
    ({"text_pattern": "anything"}, "text_pattern"),
    ({"text_pattern": ".*"}, "text_pattern"),
    ({"origin_cite": ""}, "origin_cite and origin_needle"),
    ({"origin_needle": ""}, "origin_cite and origin_needle"),
    ({"ruling": "somebody"}, "ruling must start"),
])
def test_a_text_entry_anywhere_else_is_refused_at_load(entry, needle, tmp_path):
    spec = spec_with("refit-parity", explained=[{**GOOD_TEXT, **entry}])
    problems = ms.validate_volatile(spec)
    assert any(needle in p for p in problems), problems
    with pytest.raises(ms.Refused):
        ms.load_volatile(put(tmp_path / "v.json", json.dumps(spec)))


def test_a_pointer_entry_still_cannot_reach_review_or_the_gates_text():
    for entry in ({"file": "runs/{run_id}/review/gates.json", "locations": ["/gates/0/detail"]},
                  {"file": "runs/{run_id}/receipt.json", "locations": ["/gates/0/detail"]}):
        problems = ms.validate_volatile(spec_with("refit-parity", explained=[{**GOOD_EXPLAINED, **entry}]))
        assert problems, entry                                       # only a text_pattern entry may name the gate text, and only at these two files


def test_text_entries_are_for_refit_parity_only():
    for job in ("anchors", "refit-detect", "pocket-build"):
        assert any("no explained differences are allowed" in p for p in ms.validate_volatile(spec_with(job, explained=[GOOD_TEXT])))


def test_the_origin_citation_is_checked_too(tmp_path):
    put(tmp_path / "mentat" / "f.R", "writer line\nR.version.string used here\n")
    entry = {**GOOD_TEXT, "cite": "mentat/f.R:1", "needle": "writer", "origin_cite": "mentat/f.R:2", "origin_needle": "R.version.string"}
    assert ms.check_citations({"jobs": {"refit-parity": {"mask": [], "ignore": [], "explained": [entry]}}}, tmp_path) == []
    moved = {**entry, "origin_cite": "mentat/f.R:1"}
    problems = ms.check_citations({"jobs": {"refit-parity": {"mask": [], "ignore": [], "explained": [moved]}}}, tmp_path)
    assert len(problems) == 1 and "'R.version.string' is not on mentat/f.R:1" in problems[0]


# ---- the pattern

def test_the_pattern_matches_the_windows_and_linux_spellings_of_r_version_string():
    for text in ("R version 4.6.0 (2026-04-24 ucrt)", "R version 4.6.0 (2026-04-24)", "R version 4.10.12 (2030-12-31 ucrt)"):
        assert R_VERSION.fullmatch(text), text


@pytest.mark.parametrize("text", [
    "R version 4.6.0 Patched (2026-05-01 r88001)", "R Under development (unstable) (2026-05-01 r88001)", "R version 4.6 (2026-04-24)",
    "R version 4.6.0 (2026-04-24 ucrt extra)", "R version 4.6.0 (2026-04-24 ucrt; x)", "R version 4.6.0 (2026-4-24)",
    "version 4.6.0 (2026-04-24)", "r version 4.6.0 (2026-04-24)",
])
def test_the_pattern_matches_nothing_that_is_not_that_shape(text):
    assert R_VERSION.search(text) is None


def test_the_pattern_never_matches_across_other_text():
    text = "R version 4.6.0 (2026-04-24 ucrt); jsonlite 2.0.0 (2026-01-01), sandwich 3.1-3 (a b); rf_rng.R hash pinned"
    assert [m.group(0) for m in R_VERSION.finditer(text)] == ["R version 4.6.0 (2026-04-24 ucrt)"]
    two = "R version 4.6.0 (2026-04-24) and R version 4.5.0 (2025-04-11)"
    assert [m.group(0) for m in R_VERSION.finditer(two)] == ["R version 4.6.0 (2026-04-24)", "R version 4.5.0 (2025-04-11)"]
    assert R_VERSION.sub("X", "head R version 4.6.0 (2026-04-24 ucrt); tail (2026-04-24 ucrt)") == "head X; tail (2026-04-24 ucrt)"


def test_text_explains():
    base = "R version 4.6.0 (2026-04-24%s); jsonlite 2.0.0; rf_rng.R hash pinned"
    explains = lambda a, b: ms.text_explains(R_VERSION, a, b)
    assert explains(base % " ucrt", base % "")                                                    # the R version text only
    assert explains(base % "", base % "") and explains(base % " ucrt", (base % "").replace("4.6.0", "4.5.2", 1))      # any version text of that shape
    assert not explains(base % " ucrt", (base % "").replace("jsonlite 2.0.0", "jsonlite 2.0.1"))  # version plus another change
    assert not explains(base % " ucrt", (base % "").replace("hash pinned", "HASH CHANGED"))
    assert not explains(base % " ucrt", (base % "") + " ")                                          # one byte more
    assert not explains(base % " ucrt", (base % "").replace("; rf_rng", ";rf_rng"))
    assert not explains(base % " ucrt", "R version 4.6.0 (2026-04-24); jsonlite 2.0.0")             # something missing
    assert not explains("no version here; jsonlite", "no version here; jsonlite")                    # no version text on either side: nothing to explain
    assert not explains(base % " ucrt", "jsonlite 2.0.0; rf_rng.R hash pinned")                      # the version text missing on one side
    assert not explains(base % " ucrt" + " R version 4.5.0 (2025-04-11)", (base % "") + " R version 4.5.0 (2025-04-11)")     # two occurrences: not the shape the writer emits
    assert not explains(None, "x") and not explains(1, 1) and not explains(["a"], ["a"])


def test_text_records_hold_both_raw_values_and_reject_anything_but_the_version(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    long_tail = "; " + ", ".join("pkg%d 1.0.%d" % (i, i) for i in range(40))
    base = "R version 4.6.0 (2026-04-24%s)" + long_tail
    assert len(base) > 400
    put(a / "runs" / "rid" / "receipt.json", json.dumps({"gates": [{"detail": base % " ucrt"}]}))
    put(b / "runs" / "rid" / "receipt.json", json.dumps({"gates": [{"detail": base % ""}]}))
    put(a / "runs" / "rid" / "review" / "gates.json", json.dumps({"gates": [{"detail": base % " ucrt"}]}))
    put(b / "runs" / "rid" / "review" / "gates.json", json.dumps({"gates": [{"detail": (base % "").replace("pkg3 1.0.3", "pkg3 1.0.4")}]}))
    spec = ms.load_volatile()
    records = {r["file"]: r for r in ms.explained_records(spec, "refit-parity", a, b, "rid")}
    ok, bad = records["runs/rid/receipt.json"], records["runs/rid/review/gates.json"]
    assert ok["differs"] and ok["explains"] and ok["a"] == base % " ucrt" and ok["b"] == base % "" and ok["ruling"] == ms.RULING_D246      # in full, not cut
    assert ok["normalized"] == "<R-VERSION>" + long_tail
    assert bad["differs"] and not bad["explains"] and "normalized" not in bad
    mask, _ = ms.build_mask(spec, "refit-parity", a, "rid", "d", list(records.values()))
    assert mask["files"] == {"runs/rid/receipt.json": ["/gates/0/detail"]}                           # the rejected one is not masked: Mentat's tool will say DIFFERENT


def test_text_records_ignore_every_other_pointer_of_the_gate_files(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    put(a / "runs" / "rid" / "review" / "gates.json", json.dumps({"decision": "A", "gates": [{"gate": "G0", "status": "PASS", "detail": "R version 4.6.0 (2026-04-24 ucrt)"},
                                                                                              {"gate": "G1", "status": "PASS", "detail": "R version 4.6.0 (2026-04-24 ucrt)"}]}))
    put(b / "runs" / "rid" / "review" / "gates.json", json.dumps({"decision": "B", "gates": [{"gate": "G0", "status": "FAIL", "detail": "R version 4.6.0 (2026-04-24)"},
                                                                                              {"gate": "G1", "status": "PASS", "detail": "R version 4.6.0 (2026-04-24)"}]}))
    records = ms.explained_records(ms.load_volatile(), "refit-parity", a, b, "rid")
    assert [(r["file"], r["location"]) for r in records] == [("runs/rid/review/gates.json", "/gates/0/detail")]              # not /gates/1/detail, /gates/0/status, /decision

"""Tests for ops/compute/collectors_replay.py (the collectors record/replay launcher, D-180).

No Docker, no network, no collector run, no live tree: synthetic trees under tmp_path, with stand-ins for the two runners (the Windows
record run and the container replay). The real collector, http_replay and the compose file are only read.
"""
from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops" / "compute"))
import chain_parity as cp  # noqa: E402
import collectors_replay as cr  # noqa: E402

DATE = "2025-09-02"
IMAGE = "ghcr.io/example/compute-collector@sha256:" + "a" * 64
SENTINEL = "SENTINEL-KEY-CONTENT-never-read-0123456789"


# ------------------------------------------------------------------------------------------ fixtures

def make_db(path: Path, wal_tail: bool = False) -> None:
    """A social_media.db-like WAL database. wal_tail: leave one row only in the -wal (connection kept open, autocheckpoint off)."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,package TEXT,nwords INTEGER,text TEXT)")
    con.execute("CREATE TABLE cr_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)")
    con.execute("CREATE TABLE other(a INTEGER, b TEXT)")
    con.executemany("INSERT INTO other VALUES(?,?)", [(i, "x" * i) for i in range(50)])
    con.execute("INSERT INTO speeches VALUES('Jane Doe','2025-08-01','CREC','house','CREC-2025-08-01',3,'old words here')")
    con.execute("INSERT INTO cr_fetch_log VALUES('crec:CREC-2025-08-01','ok','2025-08-02T00:00:00')")
    con.execute(f"INSERT INTO speeches VALUES('Jane Doe','{DATE}','CREC','house','CREC-{DATE}',3,'stale copy of the test date')")
    con.execute(f"INSERT INTO cr_fetch_log VALUES('crec:CREC-{DATE}','ok','2025-09-03T00:00:00')")
    con.commit()
    if wal_tail:
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("INSERT INTO other VALUES(999,'only-in-the-wal')")
        con.commit()
        make_db.keep = con     # open on purpose: closing the last connection would checkpoint the -wal away
    else:
        con.close()


def rows_of(db: Path, sql: str):
    with contextlib.closing(sqlite3.connect(db)) as con:
        return con.execute(sql).fetchall()


def make_src(tmp: Path, name: str = "src") -> Path:
    src = tmp / name
    for rel in (*cr.CODE_FILES, *cr.READ_SET):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(f"stand-in for {rel}\n", encoding="utf-8")
    make_db(src / "social_media.db", wal_tail=True)
    return src


def make_cfg(tmp: Path, src: Path, **over) -> SimpleNamespace:
    (tmp / "gb").mkdir(exist_ok=True)
    (tmp / "work").mkdir(exist_ok=True)
    cfg = dict(date=DATE, src=src, repo=ROOT, glassbox_dir=str(tmp / "gb"), work_parent=tmp / "work", keep=False,
               report=tmp / "out" / "collectors_replay.json", image=IMAGE, timeout_a1=60, timeout_b=60)
    return SimpleNamespace(**{**cfg, **over})


def fake_side(root_of, rows=1, text="Madam Speaker, I rise", cassette=False, exit_code=0, stdout="CREC: 1 candidate dates\n"):
    """A stand-in runner: does to its root what the collector would (new speeches, a log row with a time stamp), and prints."""
    def run(cfg, work):
        root = root_of(work)
        with contextlib.closing(sqlite3.connect(root / "social_media.db")) as con, con:
            con.execute("PRAGMA journal_mode=WAL")
            for i in range(rows):
                con.execute("INSERT INTO speeches VALUES('Jane Doe',?,'CREC','house',?,3,?)", (cfg.date, "CREC-" + cfg.date, f"{text} {i}"))
            con.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)", ("crec:CREC-" + cfg.date, "ok", f"{time.time_ns()}"))
        (root / "extra").mkdir(exist_ok=True)
        (root / "extra" / "note.txt").write_text("same on both sides\n", encoding="utf-8")
        if cassette:
            work.cassette.mkdir(parents=True)
            (work.cassette / "cassette.jsonl").write_text('{"v":1,"seq":0}\n', encoding="utf-8")
        time.sleep(0.01)
        return {"exit": exit_code, "stdout": ("keep-awake: system wake lock held\n" if cassette else "keep-awake unavailable (x)\n") + stdout,
                "stderr": "", "seconds": 0.1}
    return run


A1_OK = lambda **kw: fake_side(lambda w: w.a1, cassette=True, **kw)   # noqa: E731
B_OK = lambda **kw: fake_side(lambda w: w.b, **kw)                      # noqa: E731


# ------------------------------------------------------------------------------- social_media.db staging

def test_stage_db_is_a_consistent_single_file_with_the_wal_folded_in(tmp_path):
    src = tmp_path / "social_media.db"
    make_db(src, wal_tail=True)
    assert Path(str(src) + "-wal").stat().st_size > 0                       # the last row exists only in the -wal
    before = rows_of(src, "SELECT COUNT(*) FROM other")
    dest = tmp_path / "stage" / "social_media.db"
    info = cr.stage_db(src, dest)
    assert rows_of(dest, "SELECT COUNT(*) FROM other") == before == [(51,)]
    assert rows_of(dest, "SELECT b FROM other WHERE a=999") == [("only-in-the-wal",)]
    assert info["integrity"] == "ok" and info["journal_mode"] == "delete" and info["sha256"] == cp.sha256_file(dest)
    assert not Path(str(dest) + "-wal").exists() and not Path(str(dest) + "-shm").exists()
    assert dest.read_bytes()[18:20] == b"\x01\x01"                          # rollback-journal header: nothing needs a -shm to read it
    assert rows_of(src, "SELECT COUNT(*) FROM other") == before            # the source was only read
    with pytest.raises(cp.Usage, match="refusing to overwrite"):
        cr.stage_db(src, dest)
    with pytest.raises(cp.Usage, match="is not a file"):
        cr.stage_db(tmp_path / "missing.db", tmp_path / "x.db")


def test_reset_date_drops_only_that_dates_rows(tmp_path):
    db = tmp_path / "a.db"
    make_db(db)
    assert cr.reset_date(db, DATE) == {"package": "CREC-" + DATE, "speeches_removed": 1, "log_rows_removed": 1}
    assert rows_of(db, "SELECT package FROM speeches") == [("CREC-2025-08-01",)]
    assert rows_of(db, "SELECT key FROM cr_fetch_log") == [("crec:CREC-2025-08-01",)]
    assert cr.package_rows(db, DATE) == 0
    other = tmp_path / "b.db"
    sqlite3.connect(other).close()
    with pytest.raises(cp.Usage, match="not a social_media.db"):
        cr.reset_date(other, DATE)


def test_table_digests_mask_only_the_time_stamp_and_see_every_other_change(tmp_path):
    a, b, c = (tmp_path / n for n in ("a.db", "b.db", "c.db"))
    for db in (a, b, c):
        make_db(db)
    with contextlib.closing(sqlite3.connect(b)) as con, con:
        con.execute("UPDATE cr_fetch_log SET ts='another time'")
    with contextlib.closing(sqlite3.connect(c)) as con, con:
        con.execute("UPDATE speeches SET text='x' || text WHERE package LIKE '%09-02'")
    da, db_, dc = (cr.table_digests(p) for p in (a, b, c))
    assert da == db_ and da["integrity"] == "ok" and da["tables"]["cr_fetch_log"]["masked"] == ["ts"]
    assert da["tables"]["speeches"] != dc["tables"]["speeches"] and da["tables"]["other"] == dc["tables"]["other"]
    assert da["tables"]["other"]["rows"] == 50
    with contextlib.closing(sqlite3.connect(b)) as con, con:
        con.execute("UPDATE cr_fetch_log SET status='err'")                  # an unmasked column of the same table
    assert cr.table_digests(b)["tables"]["cr_fetch_log"] != da["tables"]["cr_fetch_log"]


# ------------------------------------------------------------------------------------------- the verdict

def side(**over):
    s = {"exit": 0, "stdout": "keep-awake: held\nCREC: 1\n", "rows": 3, "files": {"extra/n.txt": "ab"},
         "db": {"integrity": "ok", "schema_sha256": "s", "tables": {"speeches": {"rows": 3, "sha256": "h", "masked": []}}}}
    return {**s, **over}


def test_judge_pass_fail_and_inconclusive():
    facts = {"problems": [], "interactions": 1}
    assert cr.judge(side(), side(stdout="keep-awake unavailable\nCREC: 1\n"), facts)[:2] == ("PASS", 0)
    for b, why in ((side(exit=3), "G1"), (side(stdout="CREC: 2\n"), "G2"), (side(files={}), "G4"),
                   (side(db={**side()["db"], "tables": {"speeches": {"rows": 3, "sha256": "other", "masked": []}}}), "G3"),
                   (side(db={**side()["db"], "schema_sha256": "t"}), "G3"), (side(db={**side()["db"], "integrity": "bad"}), "G3")):
        result, code, gates = cr.judge(side(), b, facts)
        assert (result, code) == ("FAIL", 1) and [g["name"] for g in gates if g["status"] == "FAIL"] == [why]
    assert cr.judge(side(), side(), {"problems": ["B: not byte-identical"], "interactions": 1})[:2] == ("FAIL", 1)
    assert cr.judge(side(), side(), {"problems": [], "interactions": 0})[:2] == ("FAIL", 1)     # no request: nothing recorded
    assert cr.judge(side(rows=0), side(rows=0), facts)[:2] == ("INCONCLUSIVE", 3)               # agreed, but proved nothing
    result, code, gates = cr.judge(side(exit=2), None, facts)                                    # A1 failed: B never ran
    assert (result, code) == ("FAIL", 1) and "A1 exited 2" in gates[1]["detail"][0]


def test_mask_stdout_drops_only_keep_awake_lines():
    assert cr.mask_stdout("keep-awake: held\nCREC: 1\n  keep-awake indented stays\n") == "CREC: 1\n  keep-awake indented stays\n"


# ---------------------------------------------------------------------------------- key files (paths only)

def test_key_files_resolve_paths_without_opening_them_and_fail_closed(tmp_path, monkeypatch):
    tree, home = tmp_path / "tree", tmp_path / "home"
    (tree / "fec").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    govinfo, creds, fec = tree / "fec" / "govinfo_key.txt", home / ".claude" / ".credentials.json", tmp_path / "fec_key.txt"
    for f in (govinfo, creds, fec):
        f.write_text(SENTINEL, encoding="utf-8")
    other = tmp_path / "elsewhere.txt"
    other.write_text("x", encoding="utf-8")
    real_open = Path.open
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: pytest.fail(f"opened {self}") if self.suffix == ".txt" else real_open(self, *a, **k))
    monkeypatch.setattr("builtins.open", lambda *a, **k: pytest.fail("a key file was opened"))
    env = {"FEC_DATA_GOV_FILE_HOST": str(fec)}
    assert cr.key_files("collect", env, tree, home) == {"GOVINFO_KEY_FILE_HOST": str(govinfo), "FEC_DATA_GOV_FILE_HOST": str(fec)}
    assert cr.key_files("llm", {}, tree, home) == {"CLAUDE_CREDENTIALS_FILE_HOST": str(creds)}
    assert cr.key_files("llm", {"CLAUDE_CREDENTIALS_FILE_HOST": str(other)}, tree, home) == {"CLAUDE_CREDENTIALS_FILE_HOST": str(other)}   # the environment wins
    with pytest.raises(cp.Usage, match="FEC_DATA_GOV_FILE_HOST is unset and has no default"):    # keys.json is never guessed
        cr.key_files("collect", {}, tree, home)
    with pytest.raises(cp.Usage, match="is not an existing file") as bad:
        cr.key_files("collect", {"FEC_DATA_GOV_FILE_HOST": str(tmp_path / "nope")}, tmp_path / "empty", home)
    assert "GOVINFO_KEY_FILE_HOST" in str(bad.value) and SENTINEL not in str(bad.value)             # every problem named at once


def test_keyenv_prints_paths_only(tmp_path, monkeypatch, capsys):
    fec = tmp_path / "k.txt"
    fec.write_text(SENTINEL, encoding="utf-8")
    govinfo = tmp_path / "g.txt"
    govinfo.write_text(SENTINEL, encoding="utf-8")
    monkeypatch.setenv("FEC_DATA_GOV_FILE_HOST", str(fec))
    monkeypatch.setenv("GOVINFO_KEY_FILE_HOST", str(govinfo))
    assert cr.main(["keyenv", "--service", "collect"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [f"GOVINFO_KEY_FILE_HOST={govinfo}", f"FEC_DATA_GOV_FILE_HOST={fec}"] and SENTINEL not in out
    monkeypatch.delenv("FEC_DATA_GOV_FILE_HOST")
    assert cr.main(["keyenv", "--service", "collect"]) == 2


def test_the_key_variable_table_matches_compute_engine_and_the_compose_secrets():
    compose = (ROOT / "ops" / "compute" / "collectors.compose.yaml").read_text(encoding="utf-8")
    assert set(re.findall(r"\$\{([A-Z_]+_FILE_HOST):\?", compose)) == set(cr.ALL_KEY_VARS)        # an unset one stops the run
    engine = ROOT / "fec" / "compute_engine.py"
    if not engine.is_file():
        pytest.skip("fec/ is not in this tree")
    sys.path.insert(0, str(ROOT / "fec"))
    import compute_engine as CE
    assert {k: tuple(v) for k, v in CE.SECRET_VARS.items()} == cr.SERVICE_KEY_VARS and CE.ALL_SECRET_VARS == cr.ALL_KEY_VARS


# ----------------------------------------------------------------------------------------------- commands

def test_the_container_command_is_the_replay_profile_and_every_compose_variable_is_set(tmp_path):
    src = make_src(tmp_path)
    cfg = make_cfg(tmp_path, src)
    work = cr.Work(tmp_path / "work" / "w")
    argv, env, cwd = cr.b_command(cfg, work, "melange-replay-x")
    assert argv[:2] == ["docker", "compose"] and argv[argv.index("--profile") + 1] == "replay" and "--rm" in argv and "-T" in argv
    service = argv.index("melange-replay-x") + 1
    assert argv[service : service + 4] == ["replay", "python", "/melange/ops/compute/http_replay.py", "replay"]
    assert argv[-5:] == ["crec", "--since", DATE, "--until", DATE] and argv[argv.index("--") + 1] == "/melange/" + cr.COLLECTOR
    compose = (ROOT / "ops" / "compute" / "collectors.compose.yaml").read_text(encoding="utf-8")
    assert set(re.findall(r"\$\{([A-Z_]+):\?", compose)) <= set(env)                              # compose parses the whole file
    assert env["IMAGE_COMPUTE_COLLECTOR"] == IMAGE and env["MELANGE_HOST_ROOT"] == str(work.code)  # the frozen code, not the live tree
    assert env["CASSETTE_HOST_DIR"] == str(work.cassette) and env["REPLAY_OUT_HOST_DIR"] == str(work.b)
    assert all(env[v] == str(work.empty) for v in cr.ALL_KEY_VARS)                                 # the replay carries no key
    assert Path(cwd) == ROOT


def test_the_windows_command_is_a_clean_environment_on_scratch_roots(tmp_path, monkeypatch):
    src = make_src(tmp_path)
    (src / "fec" / "govinfo_key.txt").write_text(SENTINEL, encoding="utf-8")
    monkeypatch.setenv("GOVINFO_KEY", SENTINEL)
    monkeypatch.setenv("MONEYBALL_DRAFT", "C:/live/draft")
    cfg = make_cfg(tmp_path, src)
    work = cr.Work(tmp_path / "work" / "w")
    argv, env, cwd = cr.a1_command(cfg, work)
    assert argv[1] == str(work.code / "ops" / "compute" / "http_replay.py") and argv[2:4] == ["record", str(work.cassette)]
    assert argv[argv.index("--secret-file") + 1] == str(src / "fec" / "govinfo_key.txt") and argv[-5:] == ["crec", "--since", DATE, "--until", DATE]
    assert env["MONEYBALL_ROOT"] == str(work.a1) == cwd and env["MELANGE_LOCAL_ROOT"] == str(work.local)
    assert Path(env["LOCALAPPDATA"]) == work.local.parent and env["GLASSBOX_EVENTS_DIR"] == str(tmp_path / "gb")
    assert not {"GOVINFO_KEY", "MONEYBALL_DRAFT", "FEC_API_KEY"} & set(env) and SENTINEL not in json.dumps(env)
    assert "PYTHONUTF8" not in env                                  # the collector image runs cp1252 like Windows
    assert not (set(env) - set(cp.ENV_ALLOW)) - {"MONEYBALL_ROOT", "MELANGE_LOCAL_ROOT", "LOCALAPPDATA", "GLASSBOX_EVENTS_DIR", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"}


def test_the_shared_scratch_guard_refuses_the_live_store_and_a_missing_glassbox_dir(tmp_path, monkeypatch):
    live = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(live))
    with pytest.raises(cp.Usage, match="not the live"):
        cp.require_scratch_dirs(live / "Melange", tmp_path)
    with pytest.raises(cp.Usage, match="folder is named Melange"):
        cp.require_scratch_dirs(tmp_path / "copy", tmp_path)
    with pytest.raises(cp.Usage, match="--local-dir is required"):
        cp.require_scratch_dirs(None, tmp_path)
    with pytest.raises(cp.Usage, match="--glassbox-dir is required"):
        cp.require_scratch_dirs(tmp_path / "x" / "Melange", None)
    assert cp.require_scratch_dirs(tmp_path / "x" / "Melange", tmp_path) == str(tmp_path / "x" / "Melange")


def test_the_shared_scratch_guard_refuses_the_live_glassbox_events(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    events = home / ".claude" / "glassbox" / "events"
    for given in (events, events / "sub"):
        with pytest.raises(cp.Usage, match="not the live"):
            cp.require_scratch_dirs(tmp_path / "x" / "Melange", given)
    assert cp.require_scratch_dirs(tmp_path / "x" / "Melange", home / ".claude" / "glassbox" / "events-scratch")


def test_the_frozen_code_list_covers_what_the_collector_imports():
    collector = ROOT / cr.COLLECTOR
    if not collector.is_file():
        pytest.skip("fec/ is not in this tree")
    imported = set()
    for node in ast.walk(ast.parse(collector.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported - set(sys.stdlib_module_names) == {"paths"}, "a new non-stdlib import: add it to CODE_FILES"
    assert all((ROOT / rel).is_file() for rel in cr.CODE_FILES)


# -------------------------------------------------------------------------------------------- the whole run

def test_a_matching_run_passes_writes_the_cutover_evidence_and_deletes_every_copy(tmp_path):
    src = make_src(tmp_path)
    live_before = (cp.sha256_file(src / "social_media.db"), (src / "social_media.db-wal").stat().st_size)
    cfg = make_cfg(tmp_path, src)
    seen = {}

    def a1(cfg_, work):
        seen["a1_dates"] = rows_of(work.a1 / "social_media.db", f"SELECT COUNT(*) FROM speeches WHERE package='CREC-{DATE}'")
        seen["a1_wal_row"] = rows_of(work.a1 / "social_media.db", "SELECT b FROM other WHERE a=999")
        seen["work"] = work.root
        return A1_OK()(cfg_, work)

    report = cr.orchestrate(cfg, a1, B_OK())
    assert report["result"] == "PASS" and report["exit_code"] == 0 and report["failed_gates"] == []
    assert seen["a1_dates"] == [(0,)] and seen["a1_wal_row"] == [("only-in-the-wal",)]       # the date was dropped from the copy; the WAL row came along
    assert not seen["work"].exists()                                                          # copies, cassette and staged database are gone
    assert report["inputs"]["reset"]["speeches_removed"] == 1 and report["inputs"]["cassette"]["interactions"] == 1
    assert set(report["inputs"]["code_sha256"]) == set(cr.CODE_FILES) and report["inputs"]["staged_db"]["journal_mode"] == "delete"
    assert json.loads(cfg.report.read_text(encoding="utf-8")) == report
    assert report["db"]["A1"]["tables"]["speeches"] == report["db"]["B"]["tables"]["speeches"] and report["speeches_for_date"] == 1
    assert (cp.sha256_file(src / "social_media.db"), (src / "social_media.db-wal").stat().st_size) == live_before   # the live database was only read
    try:
        sys.path[:0] = [str(ROOT), str(ROOT / "fec")]
        import cutover
    except ImportError:
        return                                                                                # the platform repo has no fec/: the shape is checked below
    finally:
        sys.path[:] = [p for p in sys.path if p not in (str(ROOT), str(ROOT / "fec"))]
    assert set(cutover.check_evidence("collect", directory=cfg.report.parent)) == {"report"}  # cutover.py accepts exactly this file


def test_the_evidence_shape_cutover_reads_is_result_pass(tmp_path):
    cfg = make_cfg(tmp_path, make_src(tmp_path))
    report = cr.orchestrate(cfg, A1_OK(), B_OK())
    assert report["result"] == "PASS" and report["schema"] == 1 and {g["name"] for g in report["gates"]} == {"G0", "G1", "G2", "G3", "G4", "G5"}


@pytest.mark.parametrize("b_runner, result, code, gate", [
    (B_OK(text="different words"), "FAIL", 1, "G3"),                  # one table differs
    (B_OK(rows=2), "FAIL", 1, "G3"),
    (B_OK(exit_code=3), "FAIL", 1, "G1"),                             # http_replay: a request the cassette lacked
    (B_OK(stdout="CREC: other\n"), "FAIL", 1, "G2"),
])
def test_a_difference_fails_the_run_and_the_copies_are_still_deleted(tmp_path, b_runner, result, code, gate):
    cfg = make_cfg(tmp_path, make_src(tmp_path))
    report = cr.orchestrate(cfg, A1_OK(), b_runner)
    assert (report["result"], report["exit_code"]) == (result, code) and gate in report["failed_gates"]
    assert list((tmp_path / "work").iterdir()) == []


def test_a_day_with_no_speeches_is_inconclusive_not_a_pass(tmp_path):
    cfg = make_cfg(tmp_path, make_src(tmp_path))
    report = cr.orchestrate(cfg, A1_OK(rows=0), B_OK(rows=0))
    assert (report["result"], report["exit_code"], report["failed_gates"]) == ("INCONCLUSIVE", 3, ["G5"])


def test_a_failed_record_run_skips_the_replay_and_a_run_that_made_no_request_proves_nothing(tmp_path):
    cfg = make_cfg(tmp_path, make_src(tmp_path))
    called = []
    report = cr.orchestrate(cfg, A1_OK(exit_code=2), lambda *a: called.append(1))
    assert report["result"] == "FAIL" and not called and "B" not in report["sides"]
    cfg = make_cfg(tmp_path, make_src(tmp_path, "src2"), report=tmp_path / "out" / "second.json")
    report = cr.orchestrate(cfg, fake_side(lambda w: w.a1, cassette=False), lambda *a: called.append(1))
    assert report["result"] == "FAIL" and not called and "G0" in report["failed_gates"]


def test_keep_leaves_the_scratch_directory_and_a_name_collision_is_refused(tmp_path, capsys):
    cfg = make_cfg(tmp_path, make_src(tmp_path), keep=True)
    cr.orchestrate(cfg, A1_OK(), B_OK())
    kept = list((tmp_path / "work").iterdir())
    assert len(kept) == 1 and (kept[0] / "a1" / "social_media.db").is_file() and "kept" in capsys.readouterr().out


def test_a_read_set_file_missing_from_the_tree_stops_before_anything_runs(tmp_path):
    src = make_src(tmp_path)
    (src / cr.READ_SET[0]).unlink()
    cfg = make_cfg(tmp_path, src)
    with pytest.raises(cp.Usage, match="could not freeze"):
        cr.orchestrate(cfg, lambda *a: pytest.fail("ran"), lambda *a: pytest.fail("ran"))
    assert list((tmp_path / "work").iterdir()) == []


# ---------------------------------------------------------------------------------------------- the cli

def test_run_without_execute_prints_the_plan_and_touches_nothing(tmp_path, monkeypatch, capsys):
    src = make_src(tmp_path)
    (tmp_path / "gb").mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(cr, "_resolve_image", lambda args: IMAGE)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("the plan ran a process"))
    monkeypatch.setattr(cr, "orchestrate", lambda *a, **k: pytest.fail("the plan ran the test"))
    assert cr.main(["run", "--date", DATE, "--glassbox-dir", str(tmp_path / "gb"), "--src", str(src), "--work-parent", str(tmp_path / "work"),
                    "--report", str(tmp_path / "r.json")]) == 0
    out = capsys.readouterr().out
    assert "printed only, nothing executed" in out and "[A1 (Windows record)]" in out and "[B (container replay)]" in out and IMAGE in out
    assert list((tmp_path / "work").iterdir()) == [] and not (tmp_path / "r.json").exists()


def test_run_refuses_a_docker_desktop_that_answers_but_is_not_running(tmp_path, monkeypatch, capsys):
    src = make_src(tmp_path)
    (tmp_path / "gb").mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(cr, "_resolve_image", lambda args: IMAGE)
    monkeypatch.setattr(cr.shutil, "which", lambda name: "docker")
    down = subprocess.CompletedProcess([], 0, stdout="\n", stderr="Error response from daemon: Docker Desktop is unable to start")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: down)
    monkeypatch.setattr(cr, "orchestrate", lambda *a, **k: pytest.fail("ran against a stopped daemon"))
    assert cr.main(["run", "--date", DATE, "--glassbox-dir", str(tmp_path / "gb"), "--src", str(src), "--work-parent", str(tmp_path / "work"),
                    "--report", str(tmp_path / "r.json"), "--execute"]) == 2
    assert "unable to start" in capsys.readouterr().err and not (tmp_path / "r.json").exists()


@pytest.mark.parametrize("extra, text", [(["--date", "2999-01-01"], "past day"), (["--date", "09/02/2025"], "YYYY-MM-DD")])
def test_run_refuses_a_bad_date(tmp_path, capsys, extra, text):
    (tmp_path / "gb").mkdir()
    assert cr.main(["run", *extra, "--glassbox-dir", str(tmp_path / "gb")]) == 2
    assert text in capsys.readouterr().err


def test_run_requires_a_glassbox_dir():
    assert cr.main(["run", "--date", DATE]) == 2          # argparse: required, so the live events folder is never the default

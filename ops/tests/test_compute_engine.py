"""Tests for fec/compute_engine.py (the per-phase host-or-container switch) and its call sites: weekly_refresh's step(), the daily
Congress pipeline and daily_media_refresh (R-6 Option A, Paul 2026-10-12).

Synthetic trees under tmp_path only: no Docker, no network, no live tree. step() and _adopt_live_child() are exec'd from their REAL
source text (weekly_refresh.py is never imported: its module level takes the locks and runs the whole refresh).
"""
from __future__ import annotations

import ast
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
FEC = REPO / "fec"
sys.path.insert(0, str(FEC))
import bounded_subprocess  # noqa: E402
import compute_engine as CE  # noqa: E402

COLLECT_IMG = "ghcr.io/pjg93-222/compute-collector@sha256:" + "a" * 64
CHAIN_IMG = "ghcr.io/pjg93-222/compute-melange@sha256:" + "b" * 64
OFFICE_IMG = "ghcr.io/pjg93-222/office-calc@sha256:" + "c" * 64
ALL_CONTAINER = {"MELANGE_ENGINE_COLLECT": "container", "MELANGE_ENGINE_CHAIN": "container", "MELANGE_ENGINE_OFFICE": "container"}
WINDOWS_FLAGS = [part for key in ("COLLECT", "CHAIN", "OFFICE") for part in ("-e", f"MELANGE_ENGINE_{key}=windows")]


class HardAbort(BaseException):
    """_hard_abort's sys.exit(1); a BaseException, as SystemExit is, so step()'s own `except Exception` cannot swallow it."""


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for var in (*(f"MELANGE_ENGINE_{k}" for k in ("COLLECT", "CHAIN", "OFFICE")), "IMAGE_COMPUTE_COLLECTOR", "IMAGE_COMPUTE_MELANGE",
                "IMAGE_OFFICE_CALC", *CE.ALL_SECRET_VARS, *CE.FORWARD, "GLASSBOX_EVENTS_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(CE.paths, "ROOT", str(REPO))
    monkeypatch.setattr(CE.paths, "LOCAL_MELANGE", str(tmp_path / "local"))
    monkeypatch.setattr(CE.paths, "DRAFT", str(tmp_path / "draft"))
    monkeypatch.setattr(CE.paths, "MELANGE_DATA", str(tmp_path / "data"))
    CE.reset()
    yield
    CE.reset()


def container_world(monkeypatch, tmp_path, *keys, secrets=CE.ALL_SECRET_VARS):
    """Engines for `keys` set to container, every image given by env, the named secret variables pointing at real files."""
    for key in keys:
        monkeypatch.setenv(f"MELANGE_ENGINE_{key.upper()}", "container")
    monkeypatch.setenv("IMAGE_COMPUTE_COLLECTOR", COLLECT_IMG)
    monkeypatch.setenv("IMAGE_COMPUTE_MELANGE", CHAIN_IMG)
    monkeypatch.setenv("IMAGE_OFFICE_CALC", OFFICE_IMG)
    for var in secrets:
        path = tmp_path / var
        path.write_text("secret", encoding="utf-8")
        monkeypatch.setenv(var, str(path))
    CE.reset()


def tree(tmp_path) -> Path:
    """A tree holding the three compose files, as the repo does."""
    root = tmp_path / "tree"
    (root / "ops" / "compute").mkdir(parents=True)
    for name in ("collectors", "melange", "office-calc"):
        (root / "ops" / "compute" / f"{name}.compose.yaml").write_text("name: x\n", encoding="utf-8")
    return root


# ------------------------------------------------------------------------------------------ weekly_refresh.py, read as text

def weekly_source() -> str:
    return (FEC / "weekly_refresh.py").read_bytes().decode("utf-8").replace("\r\n", "\n")


def weekly_steps():
    """[(name, argv list, timeout, hard)] for every step() call, evaluated for today's date."""
    today = datetime.date.today()
    names = {"today": today, "yesterday": today - datetime.timedelta(days=1), "datetime": datetime, "str": str}

    def value(node):
        if isinstance(node, ast.Constant):
            return node.value
        return eval(compile(ast.fix_missing_locations(ast.Expression(node)), "<weekly_refresh>", "eval"), names)

    rows = []
    for node in ast.walk(ast.parse(weekly_source())):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "step" and len(node.args) > 1 and isinstance(node.args[1], ast.List):
            keys = {kw.arg: kw.value for kw in node.keywords}
            rows.append((node.lineno, node.args[0].value, [value(e) for e in node.args[1].elts], keys["timeout"].value,
                         bool(keys["hard"].value) if "hard" in keys else False))
    return [row[1:] for row in sorted(rows)]


def producer_paths() -> dict:
    found = {}
    for folder, dirs, files in os.walk(FEC):
        dirs[:] = sorted(d for d in dirs if d not in ("tests", "attic", "__pycache__") and not d.startswith("_quarantine"))
        for name in sorted(files):
            if name.endswith(".py"):
                found.setdefault(name, []).append(os.path.join(folder, name))
    return found


def producer_of(args) -> str:
    found = producer_paths()[os.path.basename(args[0])]
    assert len(found) == 1, found
    return found[0]


def function_source(name: str) -> str:
    source = weekly_source()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(source, node)


# the weekly steps that can run in a container, and where. Everything else stays on the host whatever the engines say.
MOVES = {
    "CREC floor speeches": "collect", "CHRG finance hearings": "collect", "Congress.gov bill discovery": "collect",
    "Official bill evidence and automatic scoring admission": "llm", "FEC source money refresh": "collect",
    "Congress rollcall source refresh": "collect", "GovInfo BILLSTATUS bulk refresh": "collect",
    "interviews: CNN (14d)": "collect", "interviews: MTP": "collect", "interviews: CBS": "collect", "interviews: ABC": "collect",
    "interviews: PBS (21d)": "collect", "interviews: NPR (14d)": "collect", "interviews: Fox archive audit": "collect",
    "interviews: MS NOW": "collect", "Wikipedia biographies (full roster)": "collect", "FULL PIPELINE + dashboards": "chain",
}


# ----------------------------------------------------------------------------------------------------------- the config

def host_engines(monkeypatch):
    """Every phase on the host, whatever ops/compute/engines.json holds (cutover.py moves a phase there, with its evidence)."""
    for key in ("COLLECT", "CHAIN", "OFFICE"):
        monkeypatch.setenv(f"MELANGE_ENGINE_{key}", "windows")
    CE.reset()


def test_the_committed_config_is_valid_and_chain_and_collect_are_still_windows():
    """office moved to container on 2026-10-13 (cutover.py, its parity evidence PASS); a phase flips only by cutover.py, which
    refuses without evidence, so chain and collect staying on the host is pinned here until theirs exists."""
    committed = json.loads((REPO / "ops" / "compute" / "engines.json").read_text(encoding="utf-8"))
    assert set(committed) == set(CE.KEYS) and set(committed.values()) <= set(CE.ENGINES)
    assert committed["chain"] == "windows" and committed["collect"] == "windows"
    assert CE.load(REPO) == committed


def test_a_missing_file_or_missing_keys_mean_windows(tmp_path):
    root = tree(tmp_path)
    assert CE.load(root) == {"collect": "windows", "chain": "windows", "office": "windows"}
    (root / "ops" / "compute" / "engines.json").write_text('{"chain": "container"}', encoding="utf-8")
    assert CE.load(root) == {"collect": "windows", "chain": "container", "office": "windows"}


def test_the_environment_overrides_the_file_per_key(tmp_path):
    root = tree(tmp_path)
    (root / "ops" / "compute" / "engines.json").write_text('{"chain": "container", "collect": "container"}', encoding="utf-8")
    got = CE.load(root, {"MELANGE_ENGINE_CHAIN": "windows", "MELANGE_ENGINE_OFFICE": " container "})
    assert got == {"collect": "container", "chain": "windows", "office": "container"}


@pytest.mark.parametrize("text", ['{"chain": "containr"}', '{"bogus": "windows"}', '["windows"]', "not json", '{"chain": null}'])
def test_a_bad_config_fails_closed(tmp_path, text):
    root = tree(tmp_path)
    (root / "ops" / "compute" / "engines.json").write_text(text, encoding="utf-8")
    with pytest.raises(CE.EngineError):
        CE.load(root)


def test_a_bad_environment_value_fails_closed(tmp_path):
    with pytest.raises(CE.EngineError, match="windows / container"):
        CE.load(tree(tmp_path), {"MELANGE_ENGINE_COLLECT": "docker"})


# ------------------------------------------------------------------------- default engines: today's exact commands, pinned

def test_the_weekly_table_is_the_pinned_one():
    rows = weekly_steps()
    assert len(rows) == 49, "weekly_refresh.py no longer declares 49 steps: re-pin the moves below deliberately"
    assert {name: CE.STEP_SERVICE.get(os.path.basename(args[0])) for name, args, _t, _h in rows if CE.STEP_SERVICE.get(os.path.basename(args[0]))} == MOVES


def test_default_engines_launch_exactly_todays_argv_for_every_weekly_step(monkeypatch):
    host_engines(monkeypatch)
    rows = weekly_steps()
    assert CE.snapshot() == {"collect": "windows", "chain": "windows", "office": "windows"}
    for name, args, _timeout, _hard in rows:
        path = producer_of(args)
        assert CE.launch(path, args[1:], cwd=str(FEC)) == CE.Launch([sys.executable, path, *args[1:]], None, None, None), name


def test_with_every_engine_on_container_exactly_the_pinned_steps_move(monkeypatch, tmp_path):
    container_world(monkeypatch, tmp_path, "collect", "chain", "office")
    for name, args, _timeout, _hard in weekly_steps():
        path = producer_of(args)
        got = CE.launch(path, args[1:], root=REPO, cwd=str(FEC))
        if name in MOVES:
            assert got.argv[:2] == ["docker", "compose"] and got.container and got.phase in ("collect", "chain"), name
            assert got.argv[-(len(args) + 2):] == [MOVES[name], "python", "/melange/fec/" + Path(path).relative_to(FEC).as_posix(), *args[1:]], name
        else:
            assert got == CE.Launch([sys.executable, path, *args[1:]], None, None, None), name


# ------------------------------------------------------------------------------------------- container: compose commands

def test_collect_builds_the_collectors_compose_command(monkeypatch, tmp_path):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "collect", secrets=CE.SECRET_VARS["collect"])
    monkeypatch.setenv("BILLSTATUS_BULK_REFRESH", "1")
    script = str(root / "fec" / "collect" / "fetch_congressional_record.py")
    got = CE.launch(script, ["crec", "--since", "2026-10-01"], root=root, cwd=str(root / "fec"))
    compose = str(root / "ops" / "compute" / "collectors.compose.yaml")
    assert got.argv[:9] == ["docker", "compose", "-f", compose, "--profile", "collect", "run", "--rm", "-T"]
    assert got.argv[9] == "--name" and got.argv[10] == got.container and got.container.startswith("melange-collect-")
    assert got.argv[11:13] == ["-w", "/melange/fec"]
    assert got.argv[13:19] == WINDOWS_FLAGS and got.argv[19:21] == ["-e", "BILLSTATUS_BULK_REFRESH=1"]
    assert got.argv[21:] == ["collect", "python", "/melange/fec/collect/fetch_congressional_record.py", "crec", "--since", "2026-10-01"]
    assert got.phase == "collect"
    env = got.env
    assert env["IMAGE_COMPUTE_COLLECTOR"] == COLLECT_IMG and env["MELANGE_HOST_ROOT"] == str(root)
    assert env["LDA_RAW_HOST_DIR"] == os.path.join(str(tmp_path / "local"), "lda_rebuild", "raw")
    assert os.path.isfile(env["CLAUDE_CREDENTIALS_FILE_HOST"]) and os.path.getsize(env["CLAUDE_CREDENTIALS_FILE_HOST"]) == 0   # unused: an empty file
    assert env["GOVINFO_KEY_FILE_HOST"] == os.environ["GOVINFO_KEY_FILE_HOST"]                                                 # used: the caller's, untouched
    assert env["CASSETTE_HOST_DIR"] == env["REPLAY_OUT_HOST_DIR"] and os.path.isdir(env["CASSETTE_HOST_DIR"])


def test_admissions_uses_the_llm_service_and_needs_the_credentials_file(monkeypatch, tmp_path):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "collect", secrets=CE.SECRET_VARS["llm"])
    script = str(root / "fec" / "legislation" / "refresh_sentinel_admissions.py")
    got = CE.launch(script, ["--limit", "32"], root=root, cwd=str(root))
    assert got.argv[4:6] == ["--profile", "llm"]
    assert got.argv[-5:] == ["llm", "python", "/melange/fec/legislation/refresh_sentinel_admissions.py", "--limit", "32"]
    assert got.argv[11:13] == ["-w", "/melange"] and got.env["GOVINFO_KEY_FILE_HOST"].endswith("melange-compute-empty")
    monkeypatch.delenv("CLAUDE_CREDENTIALS_FILE_HOST")
    with pytest.raises(CE.EngineError, match="CLAUDE_CREDENTIALS_FILE_HOST"):
        CE.launch(script, [], root=root, cwd=str(root))


def test_a_collector_without_its_secret_file_is_refused(monkeypatch, tmp_path):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "collect")
    monkeypatch.setenv("FEC_DATA_GOV_FILE_HOST", str(tmp_path / "does-not-exist"))
    with pytest.raises(CE.EngineError, match="FEC_DATA_GOV_FILE_HOST"):
        CE.launch(str(root / "fec" / "collect" / "refresh_fec_money.py"), [], root=root, cwd=str(root / "fec"))


def test_chain_builds_the_melange_compose_command(monkeypatch, tmp_path):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "chain")
    monkeypatch.setenv("COSPONSOR_FORECAST_REFRESH", "1")
    got = CE.launch(str(root / "fec" / "run_pipeline.py"), ["--from", "verify_collector_roster_updates.py"], root=root, cwd=str(root / "fec"))
    assert got.argv[:9] == ["docker", "compose", "-f", str(root / "ops" / "compute" / "melange.compose.yaml"),
                            "--profile", "chain", "run", "--rm", "-T"]
    assert got.argv[11:13] == ["-w", "/melange/fec"] and got.argv[19:21] == ["-e", "COSPONSOR_FORECAST_REFRESH=1"]
    assert got.argv[21:] == ["chain", "python", "/melange/fec/run_pipeline.py", "--from", "verify_collector_roster_updates.py"]
    assert got.phase == "chain" and got.container.startswith("melange-chain-")
    env = got.env
    assert env["IMAGE_COMPUTE_MELANGE"] == CHAIN_IMG and env["MELANGE_TREE_DIR"] == str(root)
    assert (env["MELANGE_DRAFT_DIR"], env["MELANGE_DATA_DIR"], env["MELANGE_LOCAL_DIR"]) == (
        str(tmp_path / "draft"), str(tmp_path / "data"), str(tmp_path / "local"))
    assert env["MELANGE_GLASSBOX_DIR"].endswith(os.path.join(".claude", "glassbox", "events"))


def test_producers_without_a_service_stay_on_the_host_whatever_the_engines(monkeypatch, tmp_path):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "collect", "chain", "office")
    for relative in ("collect/refresh_press_stream.py", "collect/refresh_cspan_wj.py", "legislation/update_bill_repository.py",
                     "collect/daily_media_refresh.py", "surfaces/publish_dashboard.py", "members/member_reference_layer.py",
                     "collect/fold_promoted_press_runs.py"):
        script = str(root / "fec" / relative)
        assert CE.launch(script, ["x"], root=root) == CE.Launch([sys.executable, script, "x"], None, None, None), relative


def test_the_phase_filter_keeps_the_chain_on_the_host_for_a_collector_only_caller(monkeypatch, tmp_path):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "collect", "chain")
    chain = str(root / "fec" / "run_pipeline.py")
    assert CE.launch(chain, [], phases=("collect",), root=root).container is None
    assert CE.launch(chain, [], root=root, cwd=str(root)).container
    assert CE.engine_of(chain, ("collect",)) == "windows" and CE.engine_of(chain) == "container"


def test_a_container_resolves_every_phase_to_windows_so_it_never_starts_a_sibling(monkeypatch, tmp_path):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "collect")
    got = CE.launch(str(root / "fec" / "collect" / "fetch_congressional_record.py"), [], root=root)
    passed = {got.argv[i + 1].split("=")[0]: got.argv[i + 1].split("=")[1] for i, a in enumerate(got.argv) if a == "-e"}
    assert passed == {"MELANGE_ENGINE_COLLECT": "windows", "MELANGE_ENGINE_CHAIN": "windows", "MELANGE_ENGINE_OFFICE": "windows"}
    (root / "ops" / "compute" / "engines.json").write_text(json.dumps(dict.fromkeys(CE.KEYS, "container")), encoding="utf-8")
    assert set(CE.load(root, environ=passed).values()) == {"windows"}


def test_forwarded_environment_is_exactly_the_declared_names(monkeypatch, tmp_path):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "collect")
    environ = {"BILLSTATUS_BULK_REFRESH": "1", "PYTHONUTF8": "1", "MELANGE_PIPELINE_LOCK_TOKEN": "tok", "SOMETHING_ELSE": "x"}
    got = CE.launch(str(root / "fec" / "collect" / "fetch_congressional_record.py"), [], root=root, environ={**os.environ, **environ})
    assert "-e" in got.argv and "BILLSTATUS_BULK_REFRESH=1" in got.argv and "PYTHONUTF8=1" in got.argv
    assert not any("LOCK_TOKEN" in a or "SOMETHING_ELSE" in a for a in got.argv)       # a lease token never enters a container


def test_an_argument_naming_the_host_tree_or_a_script_outside_it_is_refused(monkeypatch, tmp_path):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "collect")
    with pytest.raises(CE.EngineError, match="names the host tree"):
        CE.launch(str(root / "fec" / "collect" / "fetch_congressional_record.py"), ["--out", str(root / "x")], root=root)
    with pytest.raises(CE.EngineError, match="outside the tree"):
        CE.launch(str(tmp_path / "elsewhere" / "fetch_congressional_record.py"), [], root=root)


def test_a_tree_without_the_compose_file_is_refused(monkeypatch, tmp_path):
    container_world(monkeypatch, tmp_path, "chain")
    with pytest.raises(CE.EngineError, match="compose files"):
        CE.launch(str(tmp_path / "bare" / "fec" / "run_pipeline.py"), [], root=tmp_path / "bare")


# ------------------------------------------------------------------------------------------------ image resolution

def fake_run(monkeypatch, *, stdout="", stderr="", returncode=0, calls=None):
    def run(argv, **kwargs):
        (calls if calls is not None else []).append(argv)
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    monkeypatch.setattr(CE, "run_bounded", run)


def test_the_env_override_wins_and_the_manifest_is_not_asked(monkeypatch, tmp_path):
    calls = []
    fake_run(monkeypatch, stdout=CHAIN_IMG, calls=calls)
    other = "ghcr.io/pjg93-222/compute-melange@sha256:" + "d" * 64
    assert CE.image("compute-melange", "IMAGE_COMPUTE_MELANGE", environ={"IMAGE_COMPUTE_MELANGE": other}) == other
    assert calls == []


def test_without_an_override_the_verified_manifest_is_asked(monkeypatch, tmp_path):
    calls = []
    fake_run(monkeypatch, stdout=CHAIN_IMG + "\n", calls=calls)
    assert CE.image("compute-melange", "IMAGE_COMPUTE_MELANGE", root=tmp_path, environ={}) == CHAIN_IMG
    assert calls == [[sys.executable, str(tmp_path / "ops" / "release_lib" / "current.py"), "image", "compute-melange"]]


@pytest.mark.parametrize("stdout,stderr,code", [("", "no release has been published", 1), (CHAIN_IMG, "bad signature", 1), ("", "", 0)])
def test_no_image_fails_closed_and_nothing_is_launched(monkeypatch, tmp_path, stdout, stderr, code):
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "chain")
    monkeypatch.delenv("IMAGE_COMPUTE_MELANGE")
    fake_run(monkeypatch, stdout=stdout, stderr=stderr, returncode=code)
    with pytest.raises(CE.EngineError, match="no verified image for compute-melange: not running") as error:
        CE.launch(str(root / "fec" / "run_pipeline.py"), [], root=root)
    assert "IMAGE_COMPUTE_MELANGE" in str(error.value)          # the message names the interim override


def test_a_tag_is_not_an_image(monkeypatch):
    for ref in ("ghcr.io/pjg93-222/compute-melange:latest", "compute-melange@sha256:abc", "ghcr.io/x/y@sha256:" + "A" * 64):
        with pytest.raises(CE.EngineError, match="digest reference"):
            CE.image("compute-melange", "IMAGE_COMPUTE_MELANGE", environ={"IMAGE_COMPUTE_MELANGE": ref})


# --------------------------------------------------------------------------- weekly_refresh.py's step(), real source text

def step_world(result=None, producer="producer.py", handoff=True):
    calls, logs, aborts, events = [], [], [], []

    def run_bounded(argv, **kwargs):
        events.append("run")
        calls.append((argv, kwargs))
        if isinstance(result, BaseException):
            raise result
        return result or SimpleNamespace(stdout="done\n", stderr="", returncode=0, timed_out=False, timeout_note="")

    def hard_abort(name, failures, why):
        aborts.append((name, list(failures), why))
        raise HardAbort(why)

    ns = {"bounded_subprocess": SimpleNamespace(run_bounded=run_bounded), "compute_engine": CE, "subprocess": subprocess,
          "sys": sys, "os": os, "time": __import__("time"), "datetime": datetime, "FE": str(FEC), "_LIVE_CHILD": [None], "_tolerated": [], "_soft_failures": [],
          "_producer_path": lambda name: producer if os.path.isabs(producer) else os.path.join(str(FEC), *producer.split("/")),
          "_chain_tail_is_audit_full": lambda: True, "log": logs.append, "_write_status": lambda **kw: None,
          "_emit": lambda *a, **k: None, "_hard_abort": hard_abort,
          "_lease_handoff_out": lambda: events.append("out"), "_lease_handoff_in": lambda: events.append("in")}
    for name in ("_adopt_live_child", "_soft_fail", "step"):
        exec(function_source(name), ns)
    return SimpleNamespace(ns=ns, calls=calls, logs=logs, aborts=aborts, events=events)


def test_step_on_the_default_engines_makes_the_same_call_it_always_made():
    world = step_world(producer="collect/fetch_congressional_record.py")
    assert world.ns["step"]("CREC floor speeches", ["collect/fetch_congressional_record.py", "crec"], timeout=3000) is True
    (argv, kwargs), = world.calls
    assert argv == [sys.executable, os.path.join(str(FEC), "collect", "fetch_congressional_record.py"), "crec"]
    assert kwargs == {"timeout": 3000, "env": None, "cwd": str(FEC), "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                      "start_new_session": os.name != "nt", "on_start": world.ns["_adopt_live_child"]}
    assert world.events == ["run"] and world.aborts == [] and world.ns["_LIVE_CHILD"] == [None]


@pytest.mark.parametrize("engines", [{}, ALL_CONTAINER])
def test_press_partial_is_unchanged_on_every_engine_setting(monkeypatch, tmp_path, engines):
    container_world(monkeypatch, tmp_path, *[k.split("_")[-1].lower() for k in engines])
    exit3 = SimpleNamespace(stdout="", stderr="3 pending", returncode=3, timed_out=False, timeout_note="")
    seen = []
    world = step_world(exit3, producer="collect/refresh_press_stream.py")
    assert world.ns["step"]("press delta refresh", ["refresh_press_stream.py"], timeout=129600, hard=True,
                            partial=lambda returncode, started: seen.append(returncode) or True) is True
    assert seen == [3] and world.aborts == [] and world.calls[0][0][0] == sys.executable        # the R step is a host step
    refused = step_world(exit3, producer="collect/refresh_press_stream.py")
    with pytest.raises(HardAbort):
        refused.ns["step"]("press delta refresh", ["refresh_press_stream.py"], timeout=129600, hard=True, partial=lambda r, s: False)
    assert refused.aborts == [("press delta refresh", [], "step exited 3")]


def test_a_chain_container_hands_the_lease_over_and_takes_it_back_and_reaps(monkeypatch, tmp_path):
    container_world(monkeypatch, tmp_path, "chain")
    reaped = []
    monkeypatch.setattr(CE, "run_bounded", lambda argv, **kw: reaped.append(argv))
    world = step_world(producer=str(REPO / "fec" / "run_pipeline.py"))
    assert world.ns["step"]("FULL PIPELINE + dashboards", ["run_pipeline.py"], timeout=7200, hard=True) is True
    (argv, kwargs), = world.calls
    assert world.events == ["out", "run", "in"]                                    # released before the container, re-taken after it
    assert argv[:2] == ["docker", "compose"] and argv[-3:] == ["chain", "python", "/melange/fec/run_pipeline.py"]
    assert kwargs["env"]["IMAGE_COMPUTE_MELANGE"] == CHAIN_IMG and kwargs["cwd"] == str(FEC)
    name = argv[argv.index("--name") + 1]
    assert reaped == [["docker", "stop", "-t", "30", name], ["docker", "rm", "-f", name]]     # SIGTERM + 30 s to unwind, then the force-remove
    assert world.logs[0] == "   FULL PIPELINE + dashboards runs in container " + argv[argv.index("--name") + 1]


def test_reap_still_removes_the_container_when_the_stop_fails(monkeypatch, tmp_path):
    container_world(monkeypatch, tmp_path, "chain")
    spec = SimpleNamespace(container="melange-chain-x")
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        if argv[1] == "stop":
            raise OSError("docker stop failed")

    monkeypatch.setattr(CE, "run_bounded", run)
    CE.reap(spec)
    assert seen == [["docker", "stop", "-t", "30", "melange-chain-x"], ["docker", "rm", "-f", "melange-chain-x"]]


def test_a_timed_out_chain_container_aborts_as_a_timeout_not_as_a_lost_lease(monkeypatch, tmp_path):
    container_world(monkeypatch, tmp_path, "chain")
    monkeypatch.setattr(CE.subprocess, "run", lambda argv, **kw: None)
    timed_out = SimpleNamespace(stdout="", stderr="", returncode=None, timed_out=True, timeout_note="killed")
    world = step_world(timed_out, producer=str(REPO / "fec" / "run_pipeline.py"))
    with pytest.raises(HardAbort):
        world.ns["step"]("FULL PIPELINE + dashboards", ["run_pipeline.py"], timeout=7200, hard=True)
    assert world.events == ["out", "run"]                                                  # the lease is NOT taken back after a timeout
    assert world.aborts == [("FULL PIPELINE + dashboards", [], "timeout after 7200s")]


def test_the_lease_comes_back_even_when_the_container_cannot_start(monkeypatch, tmp_path):
    container_world(monkeypatch, tmp_path, "chain")
    monkeypatch.setattr(CE.subprocess, "run", lambda argv, **kw: None)
    world = step_world(OSError("docker is not installed"), producer=str(REPO / "fec" / "run_pipeline.py"))
    with pytest.raises(HardAbort):
        world.ns["step"]("FULL PIPELINE + dashboards", ["run_pipeline.py"], timeout=7200, hard=True)
    assert world.events == ["out", "run", "in"] and world.aborts[0][2].startswith("OSError")


def test_a_collector_container_never_touches_the_lease(monkeypatch, tmp_path):
    container_world(monkeypatch, tmp_path, "collect")
    monkeypatch.setattr(CE.subprocess, "run", lambda argv, **kw: None)
    world = step_world(producer=str(REPO / "fec" / "collect" / "fetch_congressional_record.py"))
    assert world.ns["step"]("CREC floor speeches", ["collect/fetch_congressional_record.py", "crec"], timeout=3000) is True
    assert world.events == ["run"] and world.calls[0][0][:2] == ["docker", "compose"]


def test_an_engine_that_cannot_start_is_an_error_line_not_a_crash(monkeypatch, tmp_path):
    container_world(monkeypatch, tmp_path, "chain")
    monkeypatch.delenv("IMAGE_COMPUTE_MELANGE")
    fake_run(monkeypatch, returncode=1, stderr="no manifest")
    world = step_world(producer=str(REPO / "fec" / "run_pipeline.py"))
    with pytest.raises(HardAbort):
        world.ns["step"]("FULL PIPELINE + dashboards", ["run_pipeline.py"], timeout=7200, hard=True)
    assert world.calls == [] and world.events == [] and "no verified image" in world.aborts[0][2]
    soft = step_world(producer=str(REPO / "fec" / "collect" / "fetch_congressional_record.py"))
    monkeypatch.setenv("MELANGE_ENGINE_COLLECT", "container")
    CE.reset()
    monkeypatch.setenv("IMAGE_COMPUTE_COLLECTOR", "ghcr.io/x/y:tag")
    assert soft.ns["step"]("CREC floor speeches", ["collect/fetch_congressional_record.py"], timeout=10) is False
    assert soft.calls == [] and "ERROR" in soft.logs[0]


def test_weekly_refresh_wires_the_engine_in_the_places_the_tests_assume():
    source = weekly_source()
    assert "import compute_engine" in source and "compute_engine.snapshot()" in source
    assert source.count("compute_engine.launch(") == 1 and source.count("compute_engine.reap(") == 1
    assert source.count("compute_engine.office_view()") == 1
    assert source.index("compute_engine.office_view()") > source.index("step('prepare dashboard deployment'")


# --------------------------------------------------------------------------------------------- publish phase: office view

def office_tree(tmp_path):
    root = tree(tmp_path)
    (root / CE.WORKBOOK).write_bytes(b"canonical workbook bytes")
    return root


def test_the_office_view_is_skipped_on_windows_and_touches_nothing(monkeypatch, tmp_path):
    host_engines(monkeypatch)
    root = office_tree(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert CE.office_view(root) == ("skipped", "")
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before


def test_the_office_view_runs_office_calc_on_a_hashed_copy_and_cleans_up(monkeypatch, tmp_path):
    root = office_tree(tmp_path)
    container_world(monkeypatch, tmp_path, "office")
    seen = {}

    def run_bounded(argv, **kwargs):
        seen.update(argv=argv, env=kwargs["env"], timeout=kwargs["timeout"],
                    copy=Path(kwargs["env"]["OFFICE_IN_DIR"], CE.WORKBOOK).read_bytes())
        return SimpleNamespace(returncode=0, timed_out=False, stderr="")

    monkeypatch.setattr(bounded_subprocess, "run_bounded", run_bounded)
    status, note = CE.office_view(root, timeout=99)
    sha = hashlib.sha256(b"canonical workbook bytes").hexdigest()
    argv = seen["argv"]
    assert status == "ok" and note.endswith("Moneyball Main Version - Blended.view.xlsx")
    assert argv[:9] == ["docker", "compose", "-f", str(root / "ops" / "compute" / "office-calc.compose.yaml"), "--profile", "office",
                        "run", "--rm", "-T"]
    assert argv[argv.index("office-calc"):] == ["office-calc", "--in", "/in/" + CE.WORKBOOK, "--expect-sha", sha, "--out",
                                                "/out/Moneyball Main Version - Blended.view.xlsx", "--receipt",
                                                "/out/Moneyball Main Version - Blended.view.xlsx.receipt.json"]
    assert seen["env"]["IMAGE_OFFICE_CALC"] == OFFICE_IMG and seen["env"]["OFFICE_OUT_DIR"] == str(root / "outputs" / "workbook_view")
    assert seen["copy"] == b"canonical workbook bytes" and seen["timeout"] == 99
    assert not (root / "outputs" / "workbook_view" / "in").exists()                         # the input copy is removed
    assert (root / CE.WORKBOOK).read_bytes() == b"canonical workbook bytes"                 # the canonical workbook is never written


@pytest.mark.parametrize("failure", ["exit", "timeout", "no image", "no workbook"])
def test_the_office_view_never_raises(monkeypatch, tmp_path, failure):
    root = office_tree(tmp_path)
    container_world(monkeypatch, tmp_path, "office")
    reaped = []
    monkeypatch.setattr(CE, "run_bounded", lambda argv, **kw: reaped.append(argv))
    result = {"exit": SimpleNamespace(returncode=4, timed_out=False, stderr="LibreOffice failed\n"),
              "timeout": SimpleNamespace(returncode=None, timed_out=True, stderr="")}.get(failure)
    monkeypatch.setattr(bounded_subprocess, "run_bounded", lambda argv, **kw: result)
    if failure == "no image":
        monkeypatch.delenv("IMAGE_OFFICE_CALC")
        fake_run(monkeypatch, returncode=1, stderr="no manifest")
    if failure == "no workbook":
        (root / CE.WORKBOOK).unlink()
    status, note = CE.office_view(root)
    assert status == "failed" and note
    assert bool(reaped) == (failure == "timeout")                                           # only a timed-out client leaves a container to remove
    assert not (root / "outputs" / "workbook_view" / "in").exists()


# ------------------------------------------------------------------------------------------ the other two call sites

def test_daily_media_refresh_follows_the_collect_engine(monkeypatch, tmp_path):
    from collect import daily_media_refresh as media
    launched = []

    class Process:
        pid = 1

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def popen(argv, **kwargs):
        launched.append((argv, kwargs))
        return Process()

    monkeypatch.setattr(media.subprocess, "Popen", popen)
    environment = {**os.environ, "MONEYBALL_ROOT": str(tmp_path)}
    crec = next(step for step in media.source_steps(datetime.date(2026, 10, 12)) if step.name == "crec")
    assert media.run_process(crec, tmp_path, tmp_path / "crec.log", environment) == 0
    argv, kwargs = launched[-1]
    assert argv == [sys.executable, str(media.CODE / crec.script), *crec.args] and kwargs["env"] is environment and kwargs["cwd"] == tmp_path

    container_world(monkeypatch, tmp_path, "collect")
    environment = {**os.environ, "MONEYBALL_ROOT": str(tmp_path)}      # the child environment as run() builds it: images and secrets ride in it
    assert media.run_process(crec, REPO, tmp_path / "crec2.log", environment) == 0
    argv, kwargs = launched[-1]
    assert argv[:2] == ["docker", "compose"] and argv[-len(crec.args) - 3:-len(crec.args)] == ["collect", "python", "/melange/fec/collect/fetch_congressional_record.py"]
    assert kwargs["env"]["IMAGE_COMPUTE_COLLECTOR"] == COLLECT_IMG and kwargs["cwd"] == REPO
    press = media.Step("press", "collect/refresh_press_stream.py", ("--run-root", str(tmp_path)))     # an R step stays on the host
    assert media.run_process(press, REPO, tmp_path / "press.log", environment) == 0
    assert launched[-1][0][0] == sys.executable and launched[-1][1]["env"] is environment


def test_daily_media_refresh_receipts_say_when_a_step_ran_in_a_container(monkeypatch, tmp_path):
    from collect import daily_media_refresh as media
    crec = next(step for step in media.source_steps(datetime.date(2026, 10, 12)) if step.name == "crec")
    rows = []
    for engine in ("windows", "container"):
        monkeypatch.setenv("MELANGE_ENGINE_COLLECT", engine)
        CE.reset()
        monkeypatch.setattr(media, "fetch_log_snapshot", lambda root, table: {})
        row = media.execute_step(crec, tmp_path, tmp_path, {}, runner=lambda *a: 5)
        rows.append(row)
    assert "engine" not in rows[0] and rows[1]["engine"] == "container"
    assert rows[0]["command"] == rows[1]["command"] == [sys.executable, str(media.CODE / crec.script), *crec.args]


class Finished:
    pid = 8765

    def __init__(self, returncode=0):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def test_the_congress_pipeline_runs_collectors_per_the_collect_engine_and_the_chain_stays_on_the_host(monkeypatch, tmp_path):
    from legislation import refresh_congress_pipeline as pipeline
    root = tree(tmp_path)
    container_world(monkeypatch, tmp_path, "collect", "chain")
    monkeypatch.delenv("MELANGE_PIPELINE_LOCK_TOKEN", raising=False)
    monkeypatch.delenv("MELANGE_PIPELINE_LOCK_OWNER_PID", raising=False)
    monkeypatch.setattr(pipeline, "CODE", root / "fec")
    monkeypatch.setattr(pipeline, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "DISCOVERY_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setitem(sys.modules, "run_pipeline", SimpleNamespace(preflight_dependencies=lambda: None))
    monkeypatch.setitem(sys.modules, "legislation.sync_sentinel_bill_ledger", SimpleNamespace(runtime=lambda: ("fixture", "node", "modules")))
    seen = []

    def launch(command, *, cwd, env, stdout, stderr):
        seen.append((command, env))
        return Finished(0 if len(seen) == 1 else 4)       # calibration passes; the first container collector then fails the run

    monkeypatch.setattr(pipeline.subprocess, "Popen", launch)
    assert pipeline.run(root) == 1
    host, container = seen[0], seen[1]
    assert host[0] == [sys.executable, str(root / "fec" / "calibration_guard.py")] and host[1]["MONEYBALL_ROOT"] == str(root)
    assert container[0][:2] == ["docker", "compose"] and container[1]["IMAGE_COMPUTE_COLLECTOR"] == COLLECT_IMG
    assert container[0][-3:] == ["collect", "python", "/melange/fec/legislation/refresh_legislation_discovery.py"]
    # discovery, its one retry, then admissions on the llm service (which fails the run); every one of them a container
    assert [cmd[cmd.index("python") - 1] for cmd, _env in seen[1:]] == ["collect", "collect", "llm"]
    assert pipeline.read(root / pipeline.STATUS)["status"] == "FAILED"
    steps = {name: script for name, script, _arguments, _timeout in pipeline.steps(32, include_media=True)}
    assert CE.engine_of(str(root / "fec" / steps["pipeline"]), ("collect",)) == "windows"        # the chain step is not a collector here
    assert CE.engine_of(str(root / "fec" / steps["repository"]), ("collect",)) == "windows"      # it takes the lease itself
    assert CE.engine_of(str(root / "fec" / steps["media"]), ("collect",)) == "windows"           # so does the media orchestrator
    assert {CE.engine_of(str(root / "fec" / steps[n]), ("collect",)) for n in ("discovery", "admissions", "votes", "billstatus")} == {"container"}
